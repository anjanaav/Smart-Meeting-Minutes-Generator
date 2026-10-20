#!/usr/bin/env python3
"""Pre-flight checks for the Dart code, for machines WITHOUT a Flutter SDK. Run from the app folder:

    python3 tools/check_dart.py            (add --quiet to print problems only)

It checks (1) syntax with a real Dart grammar (needs: pip install tree-sitter tree-sitter-language-pack; without it a weaker
bracket-balance check is used), (2) that every relative import exists and every package import is declared in pubspec.yaml,
(3) that a file which uses a well-known SDK or package name imports the library that provides it.

It is NOT a compiler: it cannot find type errors, wrong argument types or missing members. `flutter analyze` is the real check.
Exit code 1 when a problem is found.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# name -> import substrings, any of which provides it
PROVIDERS = {
    "Clipboard": ["flutter/services.dart"], "ClipboardData": ["flutter/services.dart"],
    "Timer": ["dart:async"], "TimeoutException": ["dart:async"], "Uint8List": ["dart:typed_data"],
    "File": ["dart:io"], "FileSystemException": ["dart:io"], "SocketException": ["dart:io"], "HandshakeException": ["dart:io"],
    "LaunchMode": ["url_launcher"], "launchUrl": ["url_launcher"], "Share": ["share_plus"], "XFile": ["share_plus"],
    "getTemporaryDirectory": ["path_provider"], "SharedPreferences": ["shared_preferences"],
    "FirebaseAuth": ["firebase_auth"], "FirebaseAuthException": ["firebase_auth"], "User": ["firebase_auth"],
    "Firebase": ["firebase_core"], "MockClient": ["http/testing.dart"],
    "debugPrint": ["flutter/foundation.dart", "flutter/material.dart"],
    "jsonEncode": ["dart:convert"], "jsonDecode": ["dart:convert"], "utf8": ["dart:convert"],
    "Scaffold": ["flutter/material.dart"], "MaterialApp": ["flutter/material.dart"],
    "InheritedWidget": ["flutter/widgets.dart", "flutter/material.dart"],
    "StatefulWidget": ["flutter/widgets.dart", "flutter/material.dart"],
    "expect": ["flutter_test"],
}
PACKAGE_OK = {"flutter", "flutter_test"}


def strip_strings_and_comments(src: str) -> str:
    """Remove comments and string contents (keeping `${...}` expressions) so brackets and names inside text are ignored."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        raw = c == "r" and i + 1 < n and src[i + 1] in "'\""
        if raw:
            i += 1
            c = src[i]
        if c in "'\"":
            end = c * 3 if src.startswith(c * 3, i) else c
            i += len(end)
            while i < n:
                if not raw and src[i] == "\\":
                    i += 2
                    continue
                if not raw and src.startswith("${", i):
                    depth, i, expr = 1, i + 2, []
                    while i < n and depth:
                        depth += (src[i] == "{") - (src[i] == "}")
                        if depth:
                            expr.append(src[i])
                        i += 1
                    out.append("(" + strip_strings_and_comments("".join(expr)) + ")")
                    continue
                if src.startswith(end, i):
                    i += len(end)
                    break
                if len(end) == 1 and src[i] == "\n":
                    break
                i += 1
            out.append('""')
            continue
        out.append(c)
        i += 1
    return "".join(out)


def bracket_problems(src: str) -> list:
    pairs, stack, problems = {")": "(", "]": "[", "}": "{"}, [], []
    for line_no, line in enumerate(strip_strings_and_comments(src).split("\n"), 1):
        for ch in line:
            if ch in "([{":
                stack.append((ch, line_no))
            elif ch in pairs:
                if not stack or stack[-1][0] != pairs[ch]:
                    return problems + [f"line {line_no}: unexpected '{ch}'"]
                stack.pop()
    if stack:
        problems.append(f"unclosed '{stack[-1][0]}' opened on line {stack[-1][1]}")
    return problems


def syntax_problems(src: bytes, parser) -> list:
    if parser is None:
        return bracket_problems(src.decode("utf-8", "replace"))
    found, lines = [], src.decode("utf-8", "replace").splitlines()

    def walk(node):
        if node.type == "ERROR" or node.is_missing:
            line = node.start_point[0] + 1
            found.append(f"line {line}: syntax error near: {lines[line - 1].strip()[:90] if line <= len(lines) else ''}")
        for child in node.children:
            walk(child)

    walk(parser.parse(src).root_node)
    return found


def load_parser():
    try:
        from tree_sitter_language_pack import get_parser
        return get_parser("dart")
    except Exception:
        return None


def declared_packages(pubspec: Path) -> set:
    return set(re.findall(r"(?m)^\s{2}([a-z_][a-z0-9_]*):", pubspec.read_text())) if pubspec.exists() else set()


def import_problems(path: Path, src: str, packages: set, own_package: str) -> list:
    problems = []
    imports = re.findall(r"""(?m)^\s*(?:import|export|part)\s+'([^']+)'""", src)
    for target in imports:
        if target.startswith("dart:"):
            continue
        if target.startswith("package:"):
            pkg = target[len("package:"):].split("/")[0]
            if pkg not in packages | PACKAGE_OK | {own_package}:
                problems.append(f"imports package '{pkg}' which pubspec.yaml does not declare")
        elif not (path.parent / target).resolve().exists():
            problems.append(f"imports '{target}', which does not exist")
    body = strip_strings_and_comments(src)
    for name, providers in PROVIDERS.items():
        if re.search(rf"(?<![\w.]){name}\b(?!\s*:)", body) and not any(p in i for i in imports for p in providers):
            problems.append(f"uses {name} but imports none of {providers}")
    return problems


def check(app_dir: Path) -> dict:
    """{relative file: [problems]} for every .dart file under lib/ and test/."""
    parser = load_parser()
    pubspec = app_dir / "pubspec.yaml"
    own = re.search(r"(?m)^name:\s*(\w+)", pubspec.read_text()).group(1) if pubspec.exists() else ""
    packages, results = declared_packages(pubspec), {}
    for folder in ("lib", "test"):
        for f in sorted((app_dir / folder).rglob("*.dart")):
            data = f.read_bytes()
            problems = syntax_problems(data, parser) + import_problems(f, data.decode("utf-8", "replace"), packages, own)
            results[str(f.relative_to(app_dir))] = problems
    return results


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    quiet = "--quiet" in args
    app_dir = Path(next((a for a in args if not a.startswith("--")), "."))
    if not (app_dir / "lib").is_dir():
        print(f"{app_dir}/lib not found: run this from the app folder.", file=sys.stderr)
        return 2
    mode = "real Dart grammar" if load_parser() else "bracket balance only (pip install tree-sitter tree-sitter-language-pack for a real syntax check)"
    results = check(app_dir)
    bad = {f: p for f, p in results.items() if p}
    for f, problems in bad.items():
        for p in problems:
            print(f"{f}: {p}")
    if not quiet or bad:
        print(f"checked {len(results)} Dart files ({mode}): {sum(map(len, bad.values()))} problem(s). This is not a compiler: run `flutter analyze` too.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

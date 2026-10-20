"""Tests for check_dart.py, and the current app source is checked with it (so a broken edit fails here)."""
import tempfile
import unittest
from pathlib import Path

from tools import check_dart as cd

APP = Path(__file__).resolve().parents[1]
PUBSPEC = "name: demo_app\ndependencies:\n  flutter:\n    sdk: flutter\n  http: ^1.2.2\n"


def project(files: dict):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / "pubspec.yaml").write_text(PUBSPEC)
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return tmp, root


class CheckerTests(unittest.TestCase):
    def run_on(self, files):
        tmp, root = project(files)
        self.addCleanup(tmp.cleanup)
        return cd.check(root)

    def test_the_real_app_source_is_clean(self):
        bad = {f: p for f, p in cd.check(APP).items() if p}
        self.assertEqual(bad, {})

    def test_a_clean_file_passes(self):
        self.assertEqual(self.run_on({"lib/a.dart": "import 'dart:async';\nvoid main() { Timer(Duration.zero, () {}); }\n"})["lib/a.dart"], [])

    @unittest.skipIf(cd.load_parser() is None, "tree-sitter dart grammar is not installed")
    def test_a_syntax_error_is_found_by_the_real_grammar(self):
        problems = self.run_on({"lib/a.dart": "void main() {\n  print('x'\n}\n"})["lib/a.dart"]
        self.assertTrue(any("syntax error" in p for p in problems), problems)

    @unittest.skipIf(cd.load_parser() is None, "tree-sitter dart grammar is not installed")
    def test_dart3_syntax_is_understood(self):
        src = "String f(int x) => switch (x) { 1 => 'a', _ => 'b' };\n(int, String) g() => (1, 'a');\nvoid h() { final (a, b) = g(); print('$a$b'); }\n"
        self.assertEqual(self.run_on({"lib/a.dart": src})["lib/a.dart"], [])

    def test_bracket_fallback_ignores_brackets_in_strings_and_comments(self):
        self.assertEqual(cd.bracket_problems("void f() { var s = '}{)'; // ((( \n var t = '${ {1: 2}[1] }'; }\n"), [])
        self.assertTrue(cd.bracket_problems("void f() { if (x) { }\n"))

    def test_missing_relative_import_and_undeclared_package_are_found(self):
        r = self.run_on({"lib/a.dart": "import 'missing.dart';\nimport 'package:not_declared/x.dart';\nimport 'package:http/http.dart';\nimport 'package:demo_app/b.dart';\n", "lib/b.dart": ""})
        self.assertEqual(len(r["lib/a.dart"]), 2)
        self.assertTrue(any("missing.dart" in p for p in r["lib/a.dart"]))
        self.assertTrue(any("not_declared" in p for p in r["lib/a.dart"]))

    def test_a_used_but_not_imported_sdk_name_is_found(self):
        r = self.run_on({"lib/a.dart": "void f() { Timer(Duration.zero, () {}); var j = jsonEncode({}); }\n"})["lib/a.dart"]
        self.assertTrue(any("Timer" in p for p in r) and any("jsonEncode" in p for p in r), r)

    def test_names_inside_strings_do_not_count_as_use(self):
        self.assertEqual(self.run_on({"lib/a.dart": "void f() { var s = 'use Timer and jsonEncode here'; // File\n}\n"})["lib/a.dart"], [])


if __name__ == "__main__":
    unittest.main()

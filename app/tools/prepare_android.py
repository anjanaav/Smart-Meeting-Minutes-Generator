#!/usr/bin/env python3
"""Prepare the Android project that `flutter create .` generates for this app.

Run once, from the app folder, after `flutter create .`:

    python3 tools/prepare_android.py            # patch
    python3 tools/prepare_android.py --dry-run  # only report what would change

What it does (every step is idempotent, so running it twice changes nothing the second time):
  1. AndroidManifest.xml (main)   adds the INTERNET permission. Flutter's template only has it in the debug and
                                  profile manifests, so a release APK would have NO network access without it.
                                  Also lets the app ask Android whether an https link can be opened (Jitsi).
  2. AndroidManifest.xml (debug)  allows cleartext http in DEBUG builds only, so the default emulator backend
                                  URL http://10.0.2.2:5000 works. Release builds stay https-only.
  3. app/build.gradle(.kts)       sets minSdk to 23 (Firebase needs it) and applies the Google services plugin.
  4. settings.gradle(.kts)        declares the Google services plugin.

Gradle files differ between Flutter versions. Where the expected lines are not found, the script does not guess:
it leaves the file alone and prints the exact lines to add by hand. Stdlib only.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

INTERNET = '<uses-permission android:name="android.permission.INTERNET"/>'
HTTPS_QUERY = '<intent>\n            <action android:name="android.intent.action.VIEW"/>\n            <data android:scheme="https"/>\n        </intent>'
GMS_VERSION = "4.4.2"
MIN_SDK = 23


class Report:
    def __init__(self, dry_run: bool):
        self.dry_run, self.changed, self.ok, self.manual = dry_run, [], [], []

    def write(self, path: Path, text: str, what: str) -> None:
        self.changed.append(f"{path}: {what}")
        if not self.dry_run:
            path.write_text(text, encoding="utf-8")


def patch_main_manifest(path: Path, rep: Report) -> None:
    text = path.read_text(encoding="utf-8")
    new = text
    if "android.permission.INTERNET" not in new:
        new, n = re.subn(r"(<manifest\b[^>]*>)", lambda m: f"{m.group(1)}\n    {INTERNET}", new, count=1)
        if n == 0:
            rep.manual.append(f"{path}: add {INTERNET} inside <manifest>")
    if 'android:scheme="https"' not in new:
        if "</queries>" in new:
            new = new.replace("</queries>", f"    {HTTPS_QUERY}\n    </queries>", 1)
        elif "</manifest>" in new:
            new = new.replace("</manifest>", f"    <queries>\n        {HTTPS_QUERY}\n    </queries>\n</manifest>", 1)
    if new != text:
        rep.write(path, new, "INTERNET permission / https link query added")
    else:
        rep.ok.append(f"{path}: already patched")


def patch_debug_manifest(path: Path, rep: Report) -> None:
    text = path.read_text(encoding="utf-8")
    if "usesCleartextTraffic" in text:
        rep.ok.append(f"{path}: already allows cleartext in debug")
        return
    if "</manifest>" not in text:
        rep.manual.append(f'{path}: add <application android:usesCleartextTraffic="true"/> inside <manifest> (debug only)')
        return
    new = text.replace("</manifest>", '    <!-- Debug builds only: lets the app reach a development backend over http (e.g. the emulator\'s 10.0.2.2). -->\n    <application android:usesCleartextTraffic="true"/>\n</manifest>', 1)
    rep.write(path, new, "cleartext http allowed for DEBUG builds only")


def patch_app_gradle(path: Path, rep: Report) -> None:
    text = path.read_text(encoding="utf-8")
    new, kts = text, path.suffix == ".kts"

    # minSdk
    if kts:
        new = re.sub(r"minSdk\s*=\s*flutter\.minSdkVersion", f"minSdk = {MIN_SDK}", new)
        has_min = re.search(rf"minSdk\s*=\s*(\d+)", new)
    else:
        new = re.sub(r"minSdkVersion\s+flutter\.minSdkVersion", f"minSdkVersion {MIN_SDK}", new)
        has_min = re.search(r"minSdkVersion\s+(\d+)", new)
    if not has_min:
        rep.manual.append(f"{path}: set minSdk to {MIN_SDK} (Firebase requires it)")
    elif int(has_min.group(1)) < MIN_SDK:
        rep.manual.append(f"{path}: minSdk is {has_min.group(1)}; raise it to {MIN_SDK} (Firebase requires it)")

    # Google services plugin
    if "com.google.gms.google-services" not in new:
        anchor = r'(id\(\s*"com\.android\.application"\s*\)\s*\n)' if kts else r'(id\s+"com\.android\.application"\s*\n)'
        line = '    id("com.google.gms.google-services")\n' if kts else '    id "com.google.gms.google-services"\n'
        new, n = re.subn(anchor, lambda m: m.group(1) + line, new, count=1)
        if n == 0:
            rep.manual.append(f'{path}: in the plugins {{ }} block add {line.strip()}')
    if new != text:
        rep.write(path, new, f"minSdk {MIN_SDK} and Google services plugin applied")
    else:
        rep.ok.append(f"{path}: already patched")


def patch_settings_gradle(path: Path, rep: Report) -> None:
    text = path.read_text(encoding="utf-8")
    if "com.google.gms.google-services" in text:
        rep.ok.append(f"{path}: Google services plugin already declared")
        return
    kts = path.suffix == ".kts"
    anchor = r'(id\(\s*"com\.android\.application"\s*\)\s*version[^\n]*\n)' if kts else r'(id\s+"com\.android\.application"\s+version[^\n]*\n)'
    line = f'    id("com.google.gms.google-services") version "{GMS_VERSION}" apply false\n' if kts else f'    id "com.google.gms.google-services" version "{GMS_VERSION}" apply false\n'
    new, n = re.subn(anchor, lambda m: m.group(1) + line, text, count=1)
    if n == 0:
        rep.manual.append(
            f"{path}: this Flutter template does not use the plugins {{ }} block in settings.gradle. Follow the FlutterFire "
            f"manual Android setup instead (classpath com.google.gms:google-services:{GMS_VERSION} in android/build.gradle and "
            "apply plugin: 'com.google.gms.google-services' in app/build.gradle)."
        )
        return
    rep.write(path, new, f"Google services plugin {GMS_VERSION} declared")


def find(android: Path, *names: str) -> Path | None:
    for name in names:
        p = android / name
        if p.exists():
            return p
    return None


def run(android: Path, dry_run: bool = False) -> Report:
    rep = Report(dry_run)
    main_manifest = android / "app/src/main/AndroidManifest.xml"
    debug_manifest = android / "app/src/debug/AndroidManifest.xml"
    for p, fn in ((main_manifest, patch_main_manifest), (debug_manifest, patch_debug_manifest)):
        if p.exists():
            fn(p, rep)
        else:
            rep.manual.append(f"{p} not found. Did you run `flutter create .` in the app folder?")
    app_gradle = find(android, "app/build.gradle.kts", "app/build.gradle")
    settings = find(android, "settings.gradle.kts", "settings.gradle")
    if app_gradle:
        patch_app_gradle(app_gradle, rep)
    else:
        rep.manual.append("android/app/build.gradle(.kts) not found.")
    if settings:
        patch_settings_gradle(settings, rep)
    else:
        rep.manual.append("android/settings.gradle(.kts) not found.")
    if not (android / "app/google-services.json").exists():
        rep.manual.append("android/app/google-services.json is missing. Download it from the Firebase console "
                          "(Project settings > Your apps > Android) and put it in android/app/.")
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--android-dir", default="android", help="the generated android folder (default: ./android)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    android = Path(args.android_dir)
    if not android.is_dir():
        print(f"{android} does not exist. Run `flutter create .` in the app folder first.", file=sys.stderr)
        return 2
    rep = run(android, args.dry_run)
    for line in rep.changed:
        print(("would change  " if args.dry_run else "changed       ") + line)
    for line in rep.ok:
        print("ok            " + line)
    for line in rep.manual:
        print("DO BY HAND    " + line)
    return 1 if rep.manual else 0


if __name__ == "__main__":
    sys.exit(main())

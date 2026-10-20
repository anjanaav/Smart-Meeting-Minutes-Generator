"""Tests for prepare_android.py against SYNTHETIC copies of Flutter's generated Android files.

The fixtures are written from the layout of recent `flutter create` templates; they are not output from a real
`flutter create` run (no Flutter SDK was available when this was written). Run:  python3 -m unittest tools.test_prepare_android
"""
import tempfile
import unittest
from pathlib import Path

from tools import prepare_android as pa

MAIN_MANIFEST = """<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <application
        android:label="smart_meeting_minutes"
        android:name="${applicationName}"
        android:icon="@mipmap/ic_launcher">
        <activity android:name=".MainActivity" android:exported="true"/>
    </application>
    <!-- Required to query activities that can process text -->
    <queries>
        <intent>
            <action android:name="android.intent.action.PROCESS_TEXT"/>
            <data android:mimeType="text/plain"/>
        </intent>
    </queries>
</manifest>
"""
DEBUG_MANIFEST = """<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <uses-permission android:name="android.permission.INTERNET"/>
</manifest>
"""
APP_KTS = """plugins {
    id("com.android.application")
    id("kotlin-android")
    id("dev.flutter.flutter-gradle-plugin")
}
android {
    defaultConfig {
        applicationId = "com.example.smart_meeting_minutes"
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
    }
}
"""
SETTINGS_KTS = """pluginManagement { }
plugins {
    id("dev.flutter.flutter-plugin-loader") version "1.0.0"
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.1.0" apply false
}
include(":app")
"""
APP_GROOVY = """plugins {
    id "com.android.application"
    id "kotlin-android"
    id "dev.flutter.flutter-gradle-plugin"
}
android {
    defaultConfig {
        minSdkVersion flutter.minSdkVersion
        targetSdkVersion flutter.targetSdkVersion
    }
}
"""
SETTINGS_GROOVY = """plugins {
    id "dev.flutter.flutter-plugin-loader" version "1.0.0"
    id "com.android.application" version "8.1.0" apply false
    id "org.jetbrains.kotlin.android" version "1.8.22" apply false
}
include ":app"
"""
OLD_SETTINGS = """include ':app'
def localPropertiesFile = new File(rootProject.projectDir, "local.properties")
"""


def build(root: Path, kts=True, settings=None, with_json=True):
    (root / "app/src/main").mkdir(parents=True)
    (root / "app/src/debug").mkdir(parents=True)
    (root / "app/src/main/AndroidManifest.xml").write_text(MAIN_MANIFEST)
    (root / "app/src/debug/AndroidManifest.xml").write_text(DEBUG_MANIFEST)
    (root / ("app/build.gradle.kts" if kts else "app/build.gradle")).write_text(APP_KTS if kts else APP_GROOVY)
    name = "settings.gradle.kts" if kts else "settings.gradle"
    (root / name).write_text(settings if settings is not None else (SETTINGS_KTS if kts else SETTINGS_GROOVY))
    if with_json:
        (root / "app/google-services.json").write_text("{}")
    return root


class PrepareAndroidTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "android"
        self.addCleanup(self.tmp.cleanup)

    def read(self, rel):
        return (self.root / rel).read_text()

    def test_kotlin_dsl_is_fully_patched(self):
        build(self.root)
        rep = pa.run(self.root)
        self.assertEqual(rep.manual, [])
        main = self.read("app/src/main/AndroidManifest.xml")
        self.assertIn("android.permission.INTERNET", main)
        self.assertIn('android:scheme="https"', main)
        self.assertEqual(main.count("<queries>"), 1)                       # reused, not duplicated
        self.assertIn('android:usesCleartextTraffic="true"', self.read("app/src/debug/AndroidManifest.xml"))
        self.assertNotIn("usesCleartextTraffic", main)                      # release stays https-only
        app = self.read("app/build.gradle.kts")
        self.assertIn("minSdk = 23", app)
        self.assertNotIn("flutter.minSdkVersion", app)
        self.assertLess(app.index("com.android.application"), app.index("com.google.gms.google-services"))
        self.assertIn('id("com.google.gms.google-services") version "4.4.2" apply false', self.read("settings.gradle.kts"))

    def test_groovy_dsl_is_fully_patched(self):
        build(self.root, kts=False)
        rep = pa.run(self.root)
        self.assertEqual(rep.manual, [])
        self.assertIn("minSdkVersion 23", self.read("app/build.gradle"))
        self.assertIn('id "com.google.gms.google-services"', self.read("app/build.gradle"))
        self.assertIn('id "com.google.gms.google-services" version "4.4.2" apply false', self.read("settings.gradle"))

    def test_second_run_changes_nothing(self):
        build(self.root)
        pa.run(self.root)
        snapshot = {p: p.read_text() for p in self.root.rglob("*") if p.is_file()}
        rep = pa.run(self.root)
        self.assertEqual(rep.changed, [])
        self.assertEqual(rep.manual, [])
        self.assertEqual(snapshot, {p: p.read_text() for p in self.root.rglob("*") if p.is_file()})

    def test_dry_run_writes_nothing(self):
        build(self.root)
        before = {p: p.read_text() for p in self.root.rglob("*") if p.is_file()}
        rep = pa.run(self.root, dry_run=True)
        self.assertTrue(rep.changed)
        self.assertEqual(before, {p: p.read_text() for p in self.root.rglob("*") if p.is_file()})

    def test_unrecognised_settings_gradle_is_left_alone_with_instructions(self):
        build(self.root, kts=False, settings=OLD_SETTINGS)
        rep = pa.run(self.root)
        self.assertEqual(self.read("settings.gradle"), OLD_SETTINGS)
        self.assertTrue(any("settings.gradle" in m and "FlutterFire" in m for m in rep.manual))

    def test_missing_google_services_json_is_reported(self):
        build(self.root, with_json=False)
        self.assertTrue(any("google-services.json" in m for m in pa.run(self.root).manual))

    def test_low_hardcoded_min_sdk_is_flagged_not_silently_kept(self):
        build(self.root)
        (self.root / "app/build.gradle.kts").write_text(APP_KTS.replace("flutter.minSdkVersion", "21"))
        self.assertTrue(any("raise it to 23" in m for m in pa.run(self.root).manual))

    def test_missing_android_folder_files_are_reported(self):
        self.root.mkdir(parents=True)
        self.assertTrue(any("flutter create" in m for m in pa.run(self.root).manual))


if __name__ == "__main__":
    unittest.main()

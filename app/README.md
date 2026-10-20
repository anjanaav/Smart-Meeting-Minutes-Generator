# Smart Meeting Minutes - Flutter app

Screens: Login, Sign Up, Home, New Meeting, Meeting (bot/participants/processing), Minutes, Past Meetings, Settings, Help.
The app talks only to the Flask backend (`docs/API.md`) and to Firebase Authentication. It holds **no** API keys or service-account files.

## One-time setup

This folder contains `lib/`, `test/` and `pubspec.yaml`. Generate the platform folders next to them:

```bash
cd app
flutter create . --project-name smart_meeting_minutes --platforms android
flutter pub get
```

### Firebase (authentication)
1. In the Firebase console create a project and enable **Authentication -> Email/Password**.
2. Add an Android app with your package name (the one `flutter create` generated, see `android/app/build.gradle`).
3. Download `google-services.json` into `android/app/`. (It is git-ignored; it is a client config, not a server secret.)
4. The Google services plugin is wired by `tools/prepare_android.py` (below). Alternative: `dart pub global activate flutterfire_cli && flutterfire configure`, then pass the generated options to `Firebase.initializeApp(options: ...)` in `lib/main.dart`.
5. The backend must use the same project: set `FIREBASE_PROJECT_ID` and `AUTH_MODE=firebase` (see `backend/.env.example`).

### Android project (permissions, minSdk, Google services plugin)
After `flutter create .` and putting `google-services.json` in `android/app/`, run:
```bash
python3 tools/prepare_android.py --dry-run   # shows what it would change
python3 tools/prepare_android.py             # applies it (safe to run twice)
```
It adds the `INTERNET` permission (a release APK has none without it), allows cleartext `http://` **in debug builds only** (so the
emulator default `http://10.0.2.2:5000` works while release stays https-only), sets `minSdk 23`, and wires the Google services plugin.
Where your Flutter version's Gradle files do not look as expected it changes nothing in that file and prints the lines to add by hand
(exit code 1). It was tested against synthetic copies of the Flutter templates, **not** a real `flutter create` output: check the diff.

## Run
```bash
# Android emulator: the host machine is 10.0.2.2 (this is the default "Backend server URL")
flutter run
# Physical phone: use http://<your-PC-LAN-IP>:5000 in New Meeting / Settings
```
On a real phone the **bot** must also be able to reach the backend: set `PUBLIC_BACKEND_URL` on the backend to an address the bot can use.

## Build an Android APK
```bash
flutter build apk --release          # build/app/outputs/flutter-apk/app-release.apk
flutter build appbundle --release    # for the Play Store
```
Sign release builds with your own keystore (`android/key.properties`, git-ignored). Use an `https://` backend in production.

## Tests
```bash
flutter test      # 24 tests: validators, model parsing and traceability, API client (mocked HTTP)
python3 tools/check_dart.py   # no Flutter needed: syntax (real Dart grammar), imports, packages
flutter analyze   # run this first: the code has never been compiled
```
Screen-level widget tests are not included because the screens depend on Firebase platform setup.

## What the New Meeting controls do
| Control | Behaviour |
|---|---|
| Join via Link | Use the Jitsi link / room name you entered |
| Auto | The backend generates a new room name; the link is shown on the Meeting screen to share |
| Test | `GET /api/health`: shows whether the backend, speech recognition and AI/rule-based generation are available |
| Open in Jitsi Meet app | After the bot is sent, opens the meeting URL in the Jitsi app or browser |
| Start Meeting | Creates the meeting record, sends the bot, then follows the real backend status |
The recording-consent checkbox is required by the backend (`CONSENT_REQUIRED`).

# Status after Stage 6

## Verified in the build sandbox
| Check | Result |
|---|---|
| Backend tests (`cd backend && python3 -m unittest discover -s tests -t .`) | 170 pass |
| Bot unit tests (`cd bot && npm test`) | 59 pass |
| Browser smoke tests (`cd bot && npm run smoke`, real Chromium, local pages) | 19 pass (8 DOM-hint checks + 11 Jitsi-state checks) |
| Backend <-> bot end to end (`python3 e2e/run_e2e.py`) | 28/28 pass |
| Deployment files + backend under real gunicorn (`python3 -m unittest e2e.test_deploy`) | 22 pass |
| App tooling tests (`cd app && python3 -m unittest tools.test_prepare_android tools.test_check_dart`) | 16 pass (Android prep 8, Dart checker 8) |
| Dart files through a real Dart grammar (`cd app && python3 tools/check_dart.py`) | 20 files, 0 syntax errors, 0 missing imports, 0 undeclared packages (still NOT a compile: no type checking) |

### What changed in Stage 6
* **Dart pre-flight check (`app/tools/check_dart.py`).** Replaces the bracket-balance script with a real Dart grammar (tree-sitter, `pip install tree-sitter tree-sitter-language-pack`). It understands the Dart 3 syntax the app uses (pattern switches, records) and flags syntax errors, relative imports that do not exist, packages missing from `pubspec.yaml`, and SDK/package names used without the import that provides them. A test runs it over the real app source, so a broken edit fails the suite. It cannot find type errors; `flutter analyze` is still the real check.
* **Rule-based minutes (the fallback when no AI key is set) reviewed on a realistic, messy meeting** (`tests/test_rule_based_quality.py`). It was already safe (proposals like "maybe move it to the 20th" and questions never became decisions or tasks; no owner or date invented). Three weaknesses fixed: "Decision made, the launch is on the 15th" was missed; a task and its confirmation ("Meera will draft it by Monday" / "I'll share the draft on Monday") were listed twice (now merged into one item citing both lines, deliberately strict so two different tasks are never merged); and questions could appear in the summary. Known limit: rule-based key points are coarse (one per block of eight lines) and task text is the spoken sentence, so "I will finish it by Thursday" needs its evidence line for context. The AI path produces better minutes but has never run against a live model here.

### What changed in Stage 5
* **Deployment (new): `docker-compose.yml`, `backend/Dockerfile`, `bot/Dockerfile`, `deploy/Caddyfile`, `deploy/.env.example`.** Caddy gets an automatic HTTPS certificate and is the only published service; backend and bot talk over the compose network; the Firebase key is a mounted secret file, never in an image; raw audio lives on a `tmpfs` (memory only, gone when the container stops); one gunicorn worker on purpose. Validated by `e2e/test_deploy.py`: files parse, every variable passed is one the code reads, the backend and bot accept their compose settings through their own config validation, `docker compose config` accepts the file and refuses to start while required values are missing, and the backend answers real HTTP under gunicorn. **The images have never been built** (no Docker daemon in the sandbox), Chromium inside the container is untested, and no certificate was ever requested.
* **Bug found by `docker compose config`, present since Stage 1: the `.env.example` files.** A line like `WHISPER_LANGUAGE=   # explanation` is parsed with the comment AS THE VALUE by dotenv and compose. Anyone who copied the template and left a line untouched would have got, for example, a speech language of "# empty = auto-detect" (breaking transcription) or a Jitsi allow-list that rejects every meeting. All three templates now put comments on their own lines, the backend ignores any value that starts with `#`, and a test fails if a template regresses.
* **Setup failures now reach the user correctly.** If the Whisper model cannot be loaded (typically the first run with no internet), the backend used to count every audio chunk as failed and report `ASR_FAILED`. It now reports `ASR_UNAVAILABLE` (retryable, audio kept), does not retry-loop, and keeps library error text (which can contain URLs/tokens) out of logs. After the problem is fixed, Retry works without restarting the backend (tested end to end).
* `docs/TROUBLESHOOTING.md` (new): sign-in, backend connectivity, Jitsi, transcription, minutes generation, export, Docker. `firebase/firebase.json` added so `firebase deploy --only firestore` works.

### What changed in Stage 4
* **Flutter app (written, still never compiled).** Static review of every Dart file against the actual Flask JSON. Models now tolerate wrong field types (a number where a string was expected no longer throws), carry `segment_ids`, and the Minutes screen shows a "Source: Alice · from 02:15" line under each key point, decision and action item (traceability, requirement 13). Async-context safety fixes, TLS-error message, more friendly error codes, services created once in `main.dart`, export file name no longer collapses for non-Latin titles. Dart tests grew from 20 to 24 (written, not run).
* **Android preparation script** (`app/tools/prepare_android.py`): INTERNET permission (release APKs have none otherwise), cleartext http for DEBUG builds only, minSdk 23, Google services plugin. Idempotent, has `--dry-run`, and refuses to guess when a Gradle file looks different. Tested on synthetic templates, not on real `flutter create` output.
* **Backend bugs found and fixed by the new tests**
  * *Download header:* a meeting title in Malayalam (or any non-Latin script) made the export's `Content-Disposition` header non-Latin-1, which a real WSGI server rejects. Now an ASCII fallback plus RFC 5987 `filename*`. (The Flask test client had hidden this.)
  * *HTTP errors:* Werkzeug errors such as a malformed upload (400) were turned into 500s. They now keep their status inside the normal error envelope.
  * *PDF:* characters the PDF font cannot draw used to vanish silently. They are now shown as `?` with a note pointing to the .txt export. Latin/Cyrillic are fine; Hindi/Malayalam are not drawn correctly in PDF (complex-script shaping is not implemented).
  * *Firestore store:* rewritten to be testable (injected client), uses the current `FieldFilter` API, writes in batches below Firestore's 500-write limit, and deletes stale parts when a retry re-saves. Tested against a strict fake (see backend README for exactly what the fake enforces) and against the real client library's method surface.
  * *OpenAI HTTP errors:* error responses are now closed (connection leak) without reading or logging the body.
* **Bot capture script rewritten.** Speaker and display name now come from Jitsi's own state (`window.APP.store`) per block of audio, with the old DOM heuristics as fallback. The bot's own audio is dropped, vanished streams are pruned, and a speaker change on a shared audio slot is followed. Verified in real Chromium against a page that imitates that state, and the test fails when the lookup is disabled (mutation-checked).
* **Docs:** completion semantics corrected in `docs/API.md` (a repeated `complete` gets `401 INVALID_BOT_TOKEN` because the token is revoked when processing starts; the bot treats this as "closed"). READMEs updated.

## Simulated in those checks (so they prove wiring, not quality)
* The e2e "meeting" uses `BOT_DRIVER=fake` (a sine tone), not a real Jitsi room.
* ASR is scripted; Whisper was not installed. Minutes came from the rule-based path, not an LLM.
* The "Jitsi state" smoke page is **my model** of `window.APP.store`, written from memory of the Jitsi web app. It was not captured from a real Jitsi build.
* Firestore and Firebase Auth ran against fakes / rejection paths only.
* Speech recognition: the real Whisper model cannot be downloaded in the sandbox (Hugging Face is blocked), so the engine was tested with a fake model. `faster-whisper` itself imports fine.

## NOT verified
* **The Flutter app has never been compiled or run** (no Flutter/Dart SDK reachable from the sandbox; downloads are blocked). Run `flutter analyze` first and expect a few fixes.
* **A real Jitsi room.** Unknown: whether remote audio is attached via `srcObject`, whether `APP.store` has the structure the script reads, lobby/password text detection, meet.jit.si behaviour toward automated clients.
* **Docker images and the HTTPS proxy:** never built or started.
* Real Whisper quality, the live OpenAI path, a genuine Firebase ID token, live Firestore, Android build, and the Jitsi app hand-off on a device.
* The project report PDF was never supplied: screens come from the written specification and layout fidelity to the report is unchecked.

## Decisions I still need from you
* Does **Auto** mean "create a new Jitsi room" in the report? (Built that way: a guess.)
* What does **Settings** contain in the report? (Limited to default user name, default backend URL, account email, log out.)
* The recording-consent checkbox on New Meeting is my addition (the backend requires it). Keep or remove?
* Upload `Major Project SMMG Final Report.pdf` so I can check screen layouts against it.

## Known gaps
* The app does not let the user type a bot server URL; the backend's `BOT_SERVER_URL` is used.
* Past Meetings opens in-progress/failed meetings in the live Meeting screen.
* PDF export cannot draw Hindi/Malayalam (use the .txt export for those).

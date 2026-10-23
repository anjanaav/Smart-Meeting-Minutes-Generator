# Smart Meeting Minutes Generator

```
Flutter app  --HTTPS/JSON-->  Flask backend  --join/leave-->  Node.js bot --(Chromium)--> Jitsi room
   (app/)                    (backend/)    <--audio+events--   (bot/)
                       ASR (Whisper) -> NLP -> minutes (LLM or rules) -> Firestore
```

| Folder | What | Tests |
|---|---|---|
| `backend/` | Flask API, ASR, NLP, minutes generator, storage | `cd backend && python3 -m unittest discover -s tests -t .` (170) |
| `bot/` | Jitsi bot (Playwright/Chromium) | `cd bot && npm test` (59) |
| `app/` | Flutter app | `cd app && flutter test` (needs Flutter SDK; 24 tests, never run yet) |
| `app/tools/` | Android project preparation, Dart pre-flight check | `cd app && python3 -m unittest tools.test_prepare_android tools.test_check_dart` (16) |
| `e2e/` | Backend <-> bot over real HTTP (simulated meeting) | `python3 e2e/run_e2e.py` (28 checks) |
| `firebase/` | Firestore rules + indexes | |
| `docs/API.md` | API contract | |
| `docs/TROUBLESHOOTING.md` | Sign-in, Jitsi, transcription, minutes, export, Docker | |
| `docker-compose.yml`, `deploy/` | HTTPS deployment (Caddy + backend + bot) | `python3 -m unittest e2e.test_deploy` (22) |

## Run everything locally
1. **Backend**: `cd backend && pip install -r requirements.txt`, copy `.env.example` to `.env`, fill it in (`BOT_SHARED_SECRET` >= 32 chars; for a no-Firebase trial use `AUTH_MODE=dev STORE_BACKEND=memory LLM_PROVIDER=rules`, never in production), then `python wsgi.py`. ffmpeg must be on PATH.
2. **Bot**: `cd bot && npm install && npx playwright install chromium`, copy `.env.example` to `.env` with the **same** `BOT_SHARED_SECRET`, then `npm start`.
3. **App**: see `app/README.md`.

## Deploy with HTTPS (Docker)
```bash
cp deploy/.env.example .env                  # fill in SMMG_DOMAIN, FIREBASE_PROJECT_ID, BOT_SHARED_SECRET, OPENAI_API_KEY ...
cp /path/to/service-account.json deploy/secrets/firebase-service-account.json
(cd firebase && firebase deploy --only firestore --project <your-firebase-project-id>)   # rules + indexes
docker compose up -d --build
```
Only Caddy is published (ports 80/443, automatic Let's Encrypt certificate for `SMMG_DOMAIN`). The backend and bot are reachable only inside the compose
network. Raw audio is held in memory (`tmpfs`) and disappears when the container stops. Then build the app with `https://<SMMG_DOMAIN>` as the backend URL.
**The images have never been built** (no Docker daemon in the build sandbox); only the files were validated. See `docs/TROUBLESHOOTING.md`.

## Checks you can run without a Jitsi room
```bash
python3 e2e/run_e2e.py                   # needs: pip install flask; node; ffmpeg
python3 -m unittest e2e.test_deploy      # deployment files + the backend under gunicorn (needs pyyaml, python-dotenv, gunicorn)
cd bot && npm run smoke                  # needs Playwright + Chromium; real browser, local pages (19 checks)
```
See `STATUS.md` for exactly what has and has not been verified.

##**Overview**

Smart Meeting Minutes Generator aims to reduce manual note-taking by organizing meeting information into a readable format. The project combines a meeting bot, backend processing services, speech recognition, and a mobile application.

##**Key Features**


Meeting workflow designed around Jitsi Meet.

Audio transcription and transcript processing.

Structured meeting summaries and key decisions.

Action-item extraction.

Backend APIs for processing and exchanging meeting data.

Flutter application interface.

Note: Feature availability depends on the current implementation. Refer to STATUS.md for test results and known limitations.

##**Technology Stack**

Mobile application: Flutter, Dart

Backend: Python, Flask

Meeting bot: Node.js, Playwright

Processing: Speech recognition and NLP

Integration: REST APIs

##**Project Structure**

app/ — Flutter application

backend/ — backend processing services

bot/ — meeting bot components

deploy/ — deployment-related files

docs/ — project documentation

e2e/ — end-to-end test resources

firebase/ — Firebase-related resources

##**Getting Started**

Clone the repository.

Review STATUS.md and the documentation in docs/.

Install the dependencies for the component you want to run.

Configure the required environment variables and external services.

Follow the component-specific instructions in the repository.

Run the available tests before attempting the complete workflow.

The application and its services may require separate setup steps. Do not assume that all components work together without additional configuration.

##**Screenshots**

Add screenshots of the actual application here when available.

Suggested screenshots:

Mobile application interface

Meeting or transcript workflow

Generated meeting summary

Action items and decisions

##**Testing and Current Status**

See STATUS.md for documented test results, implementation status, and known limitations.

Backend or simulated integration test results do not by themselves establish that the complete live meeting-to-summary workflow works.

##**Future Improvements**

Verify the complete Jitsi-to-summary workflow.

Complete Flutter application validation.

Validate live speech recognition and NLP integrations.

Improve error handling and end-to-end testing.



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

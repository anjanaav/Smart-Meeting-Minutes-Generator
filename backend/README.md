# Smart Meeting Minutes Generator – Flask backend

ASR, NLP, minutes generation, storage and REST API. (The Node.js bot and Flutter app are separate parts of the repo.)

## Run locally
```bash
cd backend
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt                       # also install ffmpeg and put it on PATH
cp .env.example .env                                  # then edit .env (OPENAI_API_KEY etc.)
python wsgi.py                                        # http://localhost:5000/api/health
```
Without Firebase yet: `AUTH_MODE=dev` and `STORE_BACKEND=memory` let you exercise the API (token `dev:<uid>`); both are
refused when `APP_ENV=production`. To connect Firebase later: set `AUTH_MODE=firebase`, `STORE_BACKEND=firestore`,
`FIREBASE_PROJECT_ID`, and `GOOGLE_APPLICATION_CREDENTIALS`; deploy `../firebase/firestore.rules` and `firestore.indexes.json`.

## Tests
```bash
python -m unittest discover -s tests -t . -v          # 154 tests. firebase-admin, reportlab and pypdf are used when installed; those tests skip otherwise
```

## Data handling
* Raw audio exists only in `TEMP_DIR/<meeting_id>` and is deleted when processing ends. After a failure that can be retried it is
  kept for `TEMP_RETENTION_MINUTES`, then purged. Audio and intermediate results are never written to the database.
* Stored: meeting metadata/status, summary, key points, decisions, action items, participant names, and the final transcript.
* Logs hold ids, counts and error types only: never passwords, tokens, transcript text or audio.
* The OpenAI key lives only in the server's `.env`. Transcript text is sent to OpenAI to generate the minutes.

## Not covered by the automated tests (needs your environment)
Live Firebase Auth/Firestore, live OpenAI calls, real Whisper models, and anything involving a real Jitsi room.
* Firestore: `FirestoreStore` is tested against a strict in-memory fake (`tests/fake_firestore.py`) that enforces transaction read-before-write,
  the 500-write batch limit and the composite indexes declared in `firebase/firestore.indexes.json`, plus a check that the real client library has
  every method the store calls. It cannot show real contention, latency or security rules.
* Firebase Auth: the real SDK is exercised for rejection paths (dev tokens, garbage, expired/revoked via the SDK's own exceptions); a genuine
  ID token from your project can only be verified with your project.
* PDF: Latin and Cyrillic render; Hindi/Malayalam and other complex scripts do not (the PDF says so and points to the .txt export).
Single instance assumed: audio chunks sit on this machine's disk, so run one process (threads are fine) or share the temp volume.

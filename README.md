
# Smart Meeting Minutes Generator (SMMG)

Smart Meeting Minutes Generator aims to reduce manual note-taking by organizing meeting information into structured summaries, key decisions, and action items. The project combines a Flutter mobile application, Flask backend, Node.js meeting bot, speech recognition, NLP, and minutes generation.

## Architecture

```text
Flutter App
    |
    | HTTPS / JSON
    v
Flask Backend <----> Node.js Bot
    |                    |
    |                    v
    |              Chromium / Playwright
    |                    |
    |                    v
    |                 Jitsi Room
    |
    v
ASR (Whisper) -> NLP -> Minutes (LLM or rules) -> Firestore
```

## Key Features

- Jitsi Meet meeting workflow
- Audio transcription and transcript processing
- Structured meeting summaries and key decisions
- Action-item extraction
- Backend APIs for communication between services
- Flutter mobile application
- Firestore integration for supported storage workflows

> **Note:** The complete live meeting-to-summary workflow and all external integrations have not been verified. See [`STATUS.md`](STATUS.md) for implementation status and test results.

## Technology Stack

| Component | Technologies |
|---|---|
| Mobile application | Flutter, Dart |
| Backend | Python, Flask |
| Meeting bot | Node.js, Playwright, Chromium |
| Speech recognition | Whisper |
| NLP and minutes generation | NLP, LLM or rule-based processing |
| API communication | HTTPS, JSON, REST APIs |
| Storage | Firestore |
| Deployment | Docker Compose, Caddy, HTTPS |

## Project Structure

| Folder / File | Description | Tests / Commands |
|---|---|---|
| `app/` | Flutter mobile application | `cd app && flutter test` — 24 tests, not yet run |
| `app/tools/` | Android preparation and Dart pre-flight checks | `cd app && python3 -m unittest tools.test_prepare_android tools.test_check_dart` — 16 tests |
| `backend/` | Flask API, ASR, NLP, minutes generator, storage | `cd backend && python3 -m unittest discover -s tests -t .` — 170 tests |
| `bot/` | Jitsi bot using Playwright and Chromium | `cd bot && npm test` — 59 tests |
| `e2e/` | Backend-to-bot integration using a simulated meeting | `python3 e2e/run_e2e.py` — 28 checks |
| `firebase/` | Firestore rules and indexes | See Firebase setup |
| `docs/API.md` | API contract | Reference documentation |
| `docs/TROUBLESHOOTING.md` | Sign-in, Jitsi, transcription, minutes, export, and Docker troubleshooting | Reference documentation |
| `docker-compose.yml`, `deploy/` | HTTPS deployment configuration | `python3 -m unittest e2e.test_deploy` — 22 tests |

Test counts reflect the documented results. Passing component tests or simulated integration checks does not prove that the complete live system works.

## Getting Started

### Prerequisites

- Python 3
- Node.js and npm
- Flutter SDK for the mobile application
- FFmpeg available on `PATH`
- Playwright and Chromium for bot testing
- Firebase and any required external service credentials

### 1. Start the Backend

```bash
cd backend
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and configure the required environment variables.

For a local trial without Firebase, the documented development configuration is:

```env
AUTH_MODE=dev
STORE_BACKEND=memory
LLM_PROVIDER=rules
```

Set `BOT_SHARED_SECRET` to a secret of at least 32 characters. Use the same secret in the bot configuration.

**Important:** This development configuration is for local trials only, not production.

Start the backend:

```bash
python wsgi.py
```

Ensure FFmpeg is available on `PATH`.

### 2. Start the Meeting Bot

```bash
cd bot
npm install
npx playwright install chromium
```

Copy `bot/.env.example` to `bot/.env` and configure the required values, including the same `BOT_SHARED_SECRET` used by the backend.

Start the bot:

```bash
npm start
```

### 3. Run the Flutter Application

Follow [`app/README.md`](app/README.md) for application-specific setup, dependencies, and backend URL configuration.

A configured Flutter SDK is required to run and test the application.

## Testing

### Backend-to-Bot Integration Checks

```bash
python3 e2e/run_e2e.py
```

Requirements include Flask, Node.js, and FFmpeg.

### Deployment Configuration Tests

```bash
python3 -m unittest e2e.test_deploy
```

Dependencies include PyYAML, `python-dotenv`, and Gunicorn.

### Bot Browser Smoke Tests

```bash
cd bot
npm run smoke
```

Requires Playwright and Chromium. The documented smoke tests use local pages.

### Flutter Tests

```bash
cd app
flutter test
```

The repository documents 24 Flutter tests, but they have not yet been run. Execute them with a configured Flutter SDK and update `STATUS.md` with the actual result.

## HTTPS Deployment with Docker

1. Copy the deployment environment template:

   ```bash
   cp deploy/.env.example .env
   ```

2. Configure required values, including `SMMG_DOMAIN`, `FIREBASE_PROJECT_ID`, and `BOT_SHARED_SECRET`, plus `OPENAI_API_KEY` if applicable.

3. Place your Firebase service-account file at:

   ```text
   deploy/secrets/firebase-service-account.json
   ```

4. Deploy Firestore rules and indexes:

   ```bash
   cd firebase
   firebase deploy --only firestore --project <your-firebase-project-id>
   cd ..
   ```

5. Build and start the services:

   ```bash
   docker compose up -d --build
   ```

The documented configuration publishes Caddy on ports 80 and 443. Caddy handles HTTPS and automatic Let's Encrypt certificates for `SMMG_DOMAIN`. The backend and bot are intended to be accessible only inside the Docker Compose network.

Raw audio is held in memory using `tmpfs` and disappears when the container stops. Configure the mobile application with the appropriate HTTPS backend URL.

**Deployment limitation:** The Docker images have not been built in the documented build environment because a Docker daemon was unavailable. The files were validated, but a successful image build and live deployment have not been established.

See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) for further setup and troubleshooting information.

## Screenshots

Add screenshots of the actual application when available:

- Flutter mobile application interface
- Meeting or transcript workflow
- Generated meeting summary
- Extracted decisions and action items

Only include genuine project screenshots. Clearly label mockups or simulated outputs.

## Current Status and Limitations

See [`STATUS.md`](STATUS.md) for detailed implementation status, test results, and known limitations.

Outstanding verification includes:

- Running the Flutter test suite
- Testing the live Jitsi meeting workflow
- Verifying real speech recognition and minutes generation integrations
- Building and testing the Docker deployment
- Validating the complete workflow with the configured storage and external services

## Future Improvements

- Complete end-to-end testing with a real Jitsi meeting
- Validate the Flutter application and backend integration
- Verify live speech recognition, NLP, and minutes generation
- Build and test Docker images
- Improve error handling and integration reliability



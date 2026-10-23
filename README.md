
# Smart Meeting Minutes Generator (SMMG)

An AI-assisted meeting minutes application designed to turn meeting discussions into structured notes, key decisions, and action items.

SMMG combines a Flutter mobile application, a Flask backend, a Node.js meeting bot, speech-to-text processing, NLP, and Firestore-oriented storage.

## Overview

Taking meeting minutes manually can be time-consuming. SMMG is designed to help by processing meeting audio and turning the resulting transcript into organized meeting information.

### Key capabilities

- **Meeting integration:** A Node.js bot is designed to join Jitsi meeting rooms.
- **Speech-to-text:** Uses a Whisper-based speech recognition component to produce transcript text.
- **Text processing:** An NLP pipeline processes transcript content for structured meeting information.
- **Minutes generation:** Supports generated meeting minutes using an LLM or a rule-based provider.
- **Mobile application:** Flutter provides the mobile client.
- **Backend APIs:** Flask exposes API endpoints for communication between components.
- **Data storage:** Firestore integration is included for meeting data.
- **Testing:** The repository includes backend, bot, deployment, and simulated end-to-end checks.

> **Project status:** Automated tests cover several components, but passing tests do not establish that every live integration works end to end. Check `STATUS.md` for the current verification status. Docker images and the complete live Jitsi, transcription, AI, and Firebase workflow must be tested in the target environment before being described as fully verified.

## Architecture

```text
Flutter Mobile App
        |
        | HTTPS / JSON
        v
Flask Backend API
        |
        | Meeting control and events
        v
Node.js Bot + Chromium
        |
        v
     Jitsi Room

Audio / transcript processing:
Whisper ASR -> NLP -> Minutes Generation
                         |
                         v
                    Firestore
```

## Technology stack

| Component | Technology |
|---|---|
| Mobile application | Flutter, Dart |
| Backend API | Python, Flask |
| Meeting bot | Node.js, Playwright / Chromium |
| Meeting platform | Jitsi |
| Speech recognition | Whisper |
| Text processing | NLP pipeline |
| Minutes generation | LLM or rule-based provider |
| Data storage | Firebase Firestore |
| Deployment configuration | Docker Compose, Caddy |
| Testing | Python unittest, Node.js tests, simulated E2E checks |

## Repository structure

```text
Smart-Meeting-Minutes-Generator/
├── app/          # Flutter mobile application
├── backend/      # Flask API, speech processing and minutes logic
├── bot/          # Node.js meeting bot
├── deploy/       # Deployment configuration
├── docs/         # API and troubleshooting documentation
├── e2e/          # Simulated end-to-end and deployment checks
├── firebase/     # Firestore rules and indexes
├── STATUS.md     # Implementation and verification status
└── README.md
```

## Getting started

### Prerequisites

Install the tools required for the component you want to run:

- Python and pip
- Node.js and npm
- Flutter SDK for the mobile application
- Playwright browser dependencies for the meeting bot
- FFmpeg for audio processing where required
- Firebase project credentials if using Firebase-backed features
- API credentials for any configured external AI provider

### 1. Backend

```bash
cd backend
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and configure the required environment variables according to the backend documentation.

Start the backend using the command documented for your setup. For the repository's WSGI development entry point:

```bash
python wsgi.py
```

### 2. Meeting bot

In a separate terminal:

```bash
cd bot
npm install
npx playwright install chromium
```

Configure the bot environment variables using `bot/.env.example`, including the shared secret required to communicate with the backend.

Start the bot using the repository's configured start script:

```bash
npm start
```

### 3. Flutter application

Follow the instructions in `app/README.md`. Configure the backend URL and any required Firebase settings before running the app.

> Configuration varies by environment. Do not commit API keys, Firebase service-account files, passwords, or shared secrets to GitHub.

## Testing

The repository includes automated checks for multiple components.

| Area | Test command |
|---|---|
| Backend | `cd backend && python3 -m unittest discover -s tests -t .` |
| Meeting bot | `cd bot && npm test` |
| Flutter app | `cd app && flutter test` |
| Android preparation and Dart checks | See the test commands documented in the repository |
| Simulated end-to-end checks | `python3 e2e/run_e2e.py` |
| Deployment configuration checks | `python3 -m unittest e2e.test_deploy` |
| Bot smoke checks | `cd bot && npm run smoke` |

Test prerequisites and current results may vary. Consult `STATUS.md` before interpreting test counts or claiming that a component has been verified.

## Security and configuration

- Keep secrets in local environment files, not source control.
- Use strong shared secrets between the backend and meeting bot.
- Configure Firebase rules and indexes for the target project.
- Use HTTPS for deployed environments.
- Review `docs/TROUBLESHOOTING.md` and deployment documentation before deploying.

## Current limitations

SMMG is an ongoing project. Local automated checks and simulated tests do not necessarily validate live meeting participation, real audio transcription, external AI-provider access, Firebase connectivity, or a production deployment.

Refer to [`STATUS.md`](STATUS.md) for the latest implementation and verification details.

## Future improvements

- Verify the full live meeting-to-minutes workflow.
- Run Flutter tests in a configured Flutter environment.
- Build and test the Docker images in a Docker-enabled environment.
- Improve speaker attribution and action-item accuracy.
- Add more end-to-end integration tests.


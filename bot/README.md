# SMMG Meeting Bot (Node.js)

Joins a Jitsi meeting when the Flask backend asks it to, captures each participant's audio
separately, uploads timestamped WAV chunks to the backend, and reports meeting state.

## Status
Implemented: HTTP API, session lifecycle, ordered events, uploads with retries, audio chunking,
participant tracking, page-state detection, and the browser capture script.
Unit tests (59) pass with `npm test`; they use a fake browser and fake backend.
`npm run smoke` (19 checks) drives real Chromium through the real Playwright driver and capture script against two local pages:
one with Jitsi-like DOM hints, one that imitates Jitsi's internal state (`window.APP.store`).

Not verified: a live Jitsi room. Whether current Jitsi attaches remote audio via `srcObject`, whether `window.APP.store` has the
structure the script reads, and how meet.jit.si treats automated clients are all unknown until you try (see Troubleshooting).

### Who is speaking
Per block of audio the script asks, in order: (1) Jitsi's own state (`APP.store`: which participant owns this audio track right now,
and that participant's display name), (2) the page (an element id containing a participant id, or the nearest `data-participant-id` /
`participant_<id>` ancestor), (3) a generic label. The bot's own audio is dropped, streams whose element left the page are dropped, and
if Jitsi re-assigns a shared audio slot to another speaker the next block follows it. Step 1 relies on an internal Jitsi API and can
break between Jitsi versions; steps 2 and 3 are the fallback.

## Setup
    npm install
    npx playwright install chromium
    cp .env.example .env        # set BOT_SHARED_SECRET to the same value as the backend
    set -a; . ./.env; set +a
    npm start                   # listens on BOT_HOST:BOT_PORT (default 127.0.0.1:3000)

Backend `.env` must point to this bot: `BOT_SERVER_URL=http://localhost:3000`.

## Tests
    npm test

## API (the backend calls these)
- `POST /api/join` with `X-Bot-Secret`: `{meeting_id, bot_token, server, room, backend_url, bot_display_name}` -> 202
- `POST /api/leave` with `X-Bot-Secret`: `{meeting_id}` -> 202, or 404 if no session
- `GET /api/meetings/{id}/status` with `X-Bot-Secret`: state and counts only
- `GET /api/health`

The bot calls the backend with `Authorization: Bot <per-meeting token>` to POST events, audio, and
`complete`, as described in `docs/API.md`.

## Behaviour and limits
- Joins muted, camera off, without the prejoin screen. Never turns on its own microphone.
- Refuses password-protected rooms and lobby rejection with coded errors; never bypasses them.
- Reports `joined` when the conference is shown; the backend reports `capturing` only after it
  measures real audio.
- If participants are present but no audio is captured within `BOT_AUDIO_TIMEOUT_MS`, it reports
  `AUDIO_CAPTURE_UNAVAILABLE`.
- Leaves when alone for `BOT_ALONE_TIMEOUT_MS`.
- On completion: closes the browser, uploads remaining audio, then sends `complete` once.
- Logs contain ids and states only; URLs and tokens are redacted.

## Troubleshooting
- `Playwright is not installed`: run `npm install` and `npx playwright install chromium`.
- `AUDIO_CAPTURE_UNAVAILABLE`: check that remote audio elements have a `srcObject` in the page
  (DevTools console). If Jitsi uses Web Audio only, the capture hook must be changed.
- `BOT_UNREACHABLE` on the backend: check `BOT_SERVER_URL` and that the bot is running.
- Chromium fails in a container: set `BOT_CHROMIUM_NO_SANDBOX=true`. If `npx playwright install chromium` is blocked, point
  `BOT_CHROMIUM_PATH` at an installed Chromium/Chrome.
- Names show as `Participant N` or ids show as `remoteN`: Jitsi's state was not reachable and the page had no hints. In the meeting
  page's DevTools console run `typeof APP !== 'undefined' && Object.keys(APP.store.getState()).filter(k=>/participants|tracks/.test(k))`.
  If that is empty, the script has to be adapted to your Jitsi version (`src/browser/captureScript.js`, functions `storeParticipants`
  and `ownerFromStore`).
- Speech is attributed to the wrong person: the audio slot is shared and the owner lookup failed. Same check as above.

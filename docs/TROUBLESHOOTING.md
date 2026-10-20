# Troubleshooting

Start with **New Meeting -> Test**. It calls `GET /api/health` and tells you whether the backend is reachable, whether speech recognition
is available, and whether minutes use the AI model or the rule-based fallback. Backend logs contain ids, counts and error types only
(never transcript text, audio, tokens or passwords), so a failure is traced by meeting id: `docker compose logs backend | grep <id>`.

## Cannot sign in / "session expired"
| Symptom | Cause and fix |
|---|---|
| `Incorrect email or password` | Firebase rejected the credentials. Check Authentication -> Email/Password is enabled in the Firebase console. |
| Sign-in works, every backend call answers `INVALID_TOKEN` | The backend is checking a different Firebase project. `FIREBASE_PROJECT_ID` must match the project of `google-services.json`, and `GOOGLE_APPLICATION_CREDENTIALS` must point at that project's service-account JSON. |
| `AUTH_MODE=dev` refused | Intentional: development tokens are refused when `APP_ENV=production`. |
| "Firebase is not configured for this build" on app start | `google-services.json` is missing from `android/app/` or the Google services plugin is not applied (`python3 tools/prepare_android.py`). |

## Cannot reach the backend (Test fails)
* Emulator: `http://10.0.2.2:5000` is the host machine. Physical phone: `http://<PC LAN IP>:5000`, same Wi-Fi, firewall open.
* `http://` works in debug builds only (release builds are https-only by design). Use the HTTPS domain for release builds.
* "A secure connection could not be made": the URL scheme does not match the server (https against a plain-http port) or the certificate is invalid.
* Behind Docker/Caddy: DNS must point at the machine and ports 80/443 must be open, otherwise no certificate is issued (`docker compose logs caddy`).

## Jitsi: the bot does not join, or no audio
| Code shown in the app | Meaning | What to do |
|---|---|---|
| `BOT_UNREACHABLE` | The backend cannot call the bot server. | Bot running? `BOT_SERVER_URL` correct? (`http://bot:3000` in compose.) |
| `BOT_REJECTED` | The bot answered 401/403. | `BOT_SHARED_SECRET` differs between backend and bot. |
| `INVALID_MEETING_LINK` / `HOST_UNRESOLVABLE` / `HOST_NOT_ALLOWED` | The link is not a public Jitsi room, DNS failed, or the host is not in `JITSI_ALLOWED_HOSTS`. | Fix the link or the allow-list. Private and loopback addresses are refused on purpose. |
| `PASSWORD_REQUIRED` | The room has a password. | The bot never enters passwords. Use a room without one. |
| `LOBBY_REJECTED` / `LOBBY_TIMEOUT` | The host denied or did not admit the bot. | Ask the host to admit "Smart Meeting Minutes Bot". |
| `AUDIO_CAPTURE_UNAVAILABLE` | Participants were present but no audio stream was ever seen. | See below. |
| Names appear as `Participant N` | Jitsi's state and page markup were not readable. | See below. |

Bot-side checks (details in `bot/README.md`): run `npm run smoke` on the machine that runs the bot (proves Chromium + capture script work);
open the meeting in a normal browser and, in DevTools, check `document.querySelectorAll('audio').length` and
`[...document.querySelectorAll('audio')].map(a => !!a.srcObject)`. If remote audio elements have no `srcObject`, the capture hook must be adapted
to how your Jitsi version attaches audio. To see whether Jitsi's internal state is readable:
`typeof APP !== 'undefined' && Object.keys(APP.store.getState()).filter(k => /participants|tracks/.test(k))`.
The bot is a normal participant: **everyone in the meeting must be told it is recorded** (the consent checkbox exists for that reason).

`capturing` is shown only after the backend measured real audio. If the status stays on "Bot joined": nobody has spoken yet, or audio is below
`MIN_AUDIO_DB` (default -55 dB, treated as silence and discarded).

## Transcription
| Code | Meaning | What to do |
|---|---|---|
| `ASR_UNAVAILABLE` | `faster-whisper` is not installed, or the model could not be loaded. | `pip install faster-whisper`. The first run downloads the model (`WHISPER_MODEL`, default `base`) from the internet into `HF_HOME` (the `whisper_models` volume in compose), so the server needs outbound access once. **Retryable** from the app while temporary audio is still held. |
| `ASR_FAILED` | Every audio chunk failed to transcribe. | Check backend logs for the exception type; try a smaller `WHISPER_MODEL`; check free memory/disk. **Retryable.** |
| `NO_AUDIO` | The bot sent no usable audio before the meeting ended. | See the Jitsi section. |
| `EMPTY_TRANSCRIPT` | Fewer than `MIN_TRANSCRIPT_WORDS` (default 5) recognised words. | Nobody spoke, the audio was too quiet, or the language is wrong: set `WHISPER_LANGUAGE` (e.g. `en`) instead of auto-detect. |
| "N audio segment(s) could not be transcribed" (a note on the minutes) | Some chunks failed; the rest were used. | Parts of the meeting may be missing. |

Accuracy: Whisper `tiny`/`base` are fast but make more mistakes with accents, noise and technical terms. Raise `WHISPER_MODEL` to `small` or `medium`
if the machine can afford it. Speech that Whisper itself marks as low-confidence is shown as "(uncertain)" and is never used alone for a decision or task.

## Minutes generation
* **"rule-based extraction" note on the minutes** (or Test says "rule-based"): no AI model was used. Either `LLM_PROVIDER=rules`, `OPENAI_API_KEY` is empty,
  or the AI service failed for this meeting (the note says which). Rule-based mode only finds explicit cues ("we decided...", "I will...") and is
  deliberately conservative.
* **AI service failures** (rate limit, bad key, network): the backend retries transient errors, then falls back to rule-based extraction instead of
  failing the meeting. Check `OPENAI_API_KEY`, `OPENAI_MODEL` and outbound access to `OPENAI_BASE_URL`.
* `AI_OUTPUT_INVALID` (retryable): the model answered but the result failed schema validation twice. Retry from the app; if it repeats, try another `OPENAI_MODEL`.
* **"N extracted item(s) were discarded"**: the model proposed decisions/tasks that cite transcript lines which do not exist. They are dropped on purpose
  (no invented content). An assignee not supported by the transcript becomes "Unassigned", and a deadline whose wording is not in the cited lines becomes "Unspecified".
* **Very long meetings** are split into overlapping windows and merged; if the final summary step fails, the part summaries are joined and a note says so.
* `DATABASE_ERROR` (retryable) / `DATABASE_UNAVAILABLE`: the minutes were generated but could not be saved/read. Check Firestore access of the service account and that
  `firebase/firestore.indexes.json` has been deployed (`cd firebase && firebase deploy --only firestore --project <id>`); a missing index makes the history list fail.

## Export
* PDF cannot draw Hindi, Malayalam and other complex scripts: those parts show as `?` and the PDF says so. The `.txt` export always has the full text.
* Download fails on one device only: check the phone has free storage; the app writes the file to its cache before sharing it.

## Docker deployment
* `docker compose up` stops at once with "set BOT_SHARED_SECRET..." (or SMMG_DOMAIN / FIREBASE_PROJECT_ID): by design; fill `.env` (see `deploy/.env.example`).
* Backend unhealthy right after start: the first start is slow while Python packages load; the health check allows 60 s.
* Raw audio lives in memory (`tmpfs`, size `TEMP_TMPFS_SIZE`). If meetings fail with disk/space errors, raise it. It is wiped whenever the container stops.
* Chromium crashes in the bot container: `shm_size` is already 1 GB; if it still crashes, give Docker more memory.

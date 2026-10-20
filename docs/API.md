# Backend API (Flask)

All bodies are JSON unless noted. Errors: `{"error": {"code": "...", "message": "..."}}`.
User calls send `Authorization: Bearer <Firebase ID token>`. Bot calls send `Authorization: Bot <per-meeting token>`.

## Meeting status machine
`created → bot_joining → waiting → joined → capturing → processing → completed`  (`failed` and `disconnected` can occur on the way)

* `joined` = the bot is in the room. `capturing` = the **backend measured real audio** from the bot. The app must show a state only when the backend reports it.
* `processing` is claimed atomically, so final processing happens exactly once however many "complete" events arrive.

## Public
| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | `{ok, asr.available, llm.configured, store}` – used by the app's Test button |

## User (Firebase token)
| Method | Path | Notes |
|---|---|---|
| POST | `/api/users/profile` | `{display_name}` – create/update profile (email comes from the token) |
| GET | `/api/users/me` | |
| POST | `/api/meetings` | `{meeting_url?, room_name?, display_name?, title?, consent: true}` → `201 {meeting, reused:false}`; same room already open → `200 {reused:true}` |
| POST | `/api/meetings/{id}/bot/join` | `{bot_server_url?}` → `202`. Backend sends the join request to the bot (URL must match the allow-list) |
| GET | `/api/meetings?limit=&status=` | own meetings, newest first |
| GET | `/api/meetings/{id}` · `/status` | poll `/status` for live state, participants, `audio_confirmed`, `error` |
| GET | `/api/meetings/{id}/minutes` | `409 MINUTES_NOT_READY` until `completed` |
| GET | `/api/meetings/{id}/export?format=pdf\|txt` | file download |
| POST | `/api/meetings/{id}/stop` | asks the bot to leave; finalizes directly if the bot is unreachable |
| POST | `/api/meetings/{id}/retry` | only for failures marked `retryable` while temp data is still held |

Another user's meeting always answers `404 NOT_FOUND` (existence is not revealed).

## Bot (per-meeting token, issued when the backend sends the join request)
| Method | Path | Notes |
|---|---|---|
| POST | `/api/bot/meetings/{id}/events` | `{type: heartbeat\|joining\|waiting\|joined\|participants\|participant_joined\|participant_left\|disconnected\|error, ...}`; error `code`: `PASSWORD_REQUIRED, LOBBY_REJECTED, LOBBY_TIMEOUT, MEETING_NOT_FOUND, AUDIO_CAPTURE_UNAVAILABLE, JOIN_FAILED, BOT_CRASHED` |
| POST | `/api/bot/meetings/{id}/audio` | multipart: `file, participant_id, participant_name, seq, offset_ms, duration_ms, rms?`. Idempotent per `(participant_id, seq)` |
| POST | `/api/bot/meetings/{id}/complete` | `{reason: meeting_ended\|bot_left\|error\|requested}` → `202`. Processing starts once. The bot token is revoked at that moment, so any repeat call is answered `401 INVALID_BOT_TOKEN`, which the bot treats as "meeting closed" (not as an error) |

## Minutes document (`GET /minutes`)
`{schema_version, meeting_id, meeting_title, meeting_date, duration_seconds, summary, key_points[{topic,details,segment_ids}], decisions[{description,speaker,context,evidence,segment_ids}], action_items[{task,assignee|null,deadline|null,status,evidence,segment_ids}], participants[{name}], transcript[{id,speaker,text,start_time,end_time,uncertain}], generated_by: "llm"|"rule_based", warnings[]}`

`assignee` / `deadline` are `null` unless supported by the transcript or participant metadata; `evidence` is copied from the cited transcript segments.

## Error codes users can see
`INVALID_TOKEN, AUTH_REQUIRED, CONSENT_REQUIRED, INVALID_MEETING_LINK, HOST_UNRESOLVABLE, BOT_SERVER_REQUIRED, BOT_SERVER_NOT_ALLOWED, BOT_UNREACHABLE, BOT_REJECTED, MINUTES_NOT_READY, NO_AUDIO, EMPTY_TRANSCRIPT, ASR_FAILED, ASR_UNAVAILABLE, AI_OUTPUT_INVALID, DATABASE_ERROR, DATABASE_UNAVAILABLE, BOT_TIMEOUT`

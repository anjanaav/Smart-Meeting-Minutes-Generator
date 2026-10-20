"""REST API. All responses are JSON; errors look like {"error": {"code", "message"}}."""
from __future__ import annotations

import logging
import math
import re
import unicodedata
import uuid
from urllib.parse import quote

from flask import Blueprint, Response, current_app, g, jsonify, request

from .auth import check_bot_token, hash_token, new_bot_token, require_user
from .bot_client import BotRejected, BotUnreachable
from .errors import ApiError
from .export import render_pdf, render_text
from .jitsi import parse_meeting
from .store import LIVE_STATUSES, utcnow_iso

log = logging.getLogger(__name__)
bp = Blueprint("api", __name__, url_prefix="/api")

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_PID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
EVENT_TYPES = {"heartbeat", "joining", "waiting", "joined", "participants", "participant_joined", "participant_left", "disconnected", "error"}
# Text shown to users is chosen here, never taken from the bot.
BOT_ERRORS = {
    "PASSWORD_REQUIRED": "This meeting is protected by a password. The bot cannot join password-protected meetings.",
    "LOBBY_REJECTED": "The host did not admit the bot to the meeting.",
    "LOBBY_TIMEOUT": "The host did not admit the bot in time.",
    "MEETING_NOT_FOUND": "The meeting could not be found. Check the link.",
    "AUDIO_CAPTURE_UNAVAILABLE": "The bot joined, but the meeting audio could not be captured.",
    "JOIN_FAILED": "The bot could not join the meeting.",
    "BOT_CRASHED": "The bot stopped unexpectedly.",
}


def svc():
    return current_app.extensions["smmg"]


def content_disposition(title: str, ext: str) -> str:
    """Download header that is safe for any title. HTTP headers must be Latin-1, so the plain ``filename`` is ASCII only,
    and the real (Unicode) name goes in ``filename*`` (RFC 5987), which browsers and phones understand."""
    unicode_name = re.sub(r'[\x00-\x1f\x7f"\\/:*?<>|]+', "_", title or "").strip(" ._")[:60] or "meeting"
    ascii_name = unicodedata.normalize("NFKD", unicode_name).encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_name).strip("_.") or "meeting"
    return f"attachment; filename=\"{ascii_name}_minutes.{ext}\"; filename*=UTF-8''{quote(f'{unicode_name}_minutes.{ext}')}"


def json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ApiError(400, "INVALID_JSON", "The request body must be a JSON object.")
    return body


def clean_name(value, default: str = "", limit: int = 60) -> str:
    return _CONTROL.sub("", str(value or "")).strip()[:limit] or default


def public_meeting(m: dict) -> dict:
    keys = ("id", "title", "room_name", "jitsi_server", "meeting_url", "status", "status_detail", "error", "audio_confirmed",
            "created_at", "started_at", "ended_at", "duration_seconds", "generated_by", "summary_preview", "stop_requested")
    out = {k: m.get(k) for k in keys}
    out["participants"] = [{"name": p["name"], "joined_at": p.get("joined_at"), "left_at": p.get("left_at")} for p in m.get("participants", [])]
    return out


def owned_meeting(meeting_id: str) -> dict:
    m = svc().store.get_meeting(meeting_id)
    if not m or m["owner_uid"] != g.user["uid"]:
        raise ApiError(404, "NOT_FOUND", "Meeting not found.")  # same answer for "missing" and "someone else's"
    return m


def bot_meeting(meeting_id: str) -> dict:
    m = svc().store.get_meeting(meeting_id)
    if m is None:
        raise ApiError(401, "INVALID_BOT_TOKEN", "Bot authentication failed.")
    check_bot_token(m)
    return m


# ------------------------------------------------------------------ health and profile
@bp.get("/health")
def health():
    s = svc()
    return jsonify({
        "ok": True, "service": "minutes-backend", "version": "1.0.0",
        "asr": {"available": s.pipeline.asr.is_available()},
        "llm": {"configured": s.pipeline.generator.provider is not None},
        "store": s.cfg.store_backend,
    })


@bp.post("/users/profile")
@require_user
def upsert_profile():
    body = json_body()
    data = {"email": g.user["email"], "updated_at": utcnow_iso()}
    name = clean_name(body.get("display_name"), limit=80)
    if name:
        data["display_name"] = name
    return jsonify(svc().store.upsert_user(g.user["uid"], data))


@bp.get("/users/me")
@require_user
def me():
    return jsonify(svc().store.get_user(g.user["uid"]) or {"uid": g.user["uid"], "email": g.user["email"]})


# ------------------------------------------------------------------ meetings (user)
@bp.post("/meetings")
@require_user
def create_meeting():
    s, body = svc(), json_body()
    if body.get("consent") is not True:
        raise ApiError(400, "CONSENT_REQUIRED", "Confirm that all participants agree to the meeting being recorded and transcribed.")
    parsed = parse_meeting(body.get("meeting_url"), body.get("room_name"), s.cfg.default_jitsi_server, s.cfg.jitsi_allowed_hosts, s.resolver)
    now = utcnow_iso()
    meeting = {
        "id": uuid.uuid4().hex, "owner_uid": g.user["uid"], "title": clean_name(body.get("title"), parsed["room"], 120),
        "room_name": parsed["room"], "jitsi_server": parsed["server"], "meeting_url": parsed["url"], "room_key": parsed["room_key"],
        "display_name": clean_name(body.get("display_name"), "User"), "status": "created", "status_detail": "", "error": None,
        "audio_confirmed": False, "participants": [], "created_at": now, "started_at": None, "ended_at": None,
        "duration_seconds": 0, "bot_token_hash": None, "bot_server_url": None, "last_bot_activity": None, "stop_requested": False,
    }
    m, created = s.store.create_or_get_open(meeting)  # atomic: no duplicate records for the same room
    return jsonify({"meeting": public_meeting(m), "reused": not created}), (201 if created else 200)


def resolve_bot_url(cfg, requested) -> str:
    from urllib.parse import urlparse

    url = (requested or cfg.bot_server_url or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ApiError(400, "BOT_SERVER_REQUIRED", "Enter the bot server URL.")
    allowed = {urlparse(u).netloc or u for u in cfg.bot_server_allowlist}
    if cfg.bot_server_url:
        allowed.add(urlparse(cfg.bot_server_url).netloc)
    if allowed:
        if parsed.netloc not in allowed:
            raise ApiError(400, "BOT_SERVER_NOT_ALLOWED", "This bot server is not allowed by the backend configuration.")
    elif cfg.env != "development":
        raise ApiError(503, "BOT_SERVER_NOT_CONFIGURED", "No bot server is configured on the backend.")
    return url


def _fail(meeting_id: str, code: str, message: str) -> None:
    svc().store.mutate_meeting(meeting_id, lambda x: {"status": "failed", "bot_token_hash": None, "error": {"code": code, "message": message, "retryable": False}}
                               if x["status"] in LIVE_STATUSES + ("created",) else None)


@bp.post("/meetings/<meeting_id>/bot/join")
@require_user
def bot_join(meeting_id):
    s, m = svc(), owned_meeting(meeting_id)
    body = request.get_json(silent=True) or {}
    if m["status"] in LIVE_STATUSES:
        return jsonify({"meeting": public_meeting(m)}), 200  # already joining: idempotent
    if m["status"] != "created":
        raise ApiError(409, "MEETING_NOT_JOINABLE", "This meeting has already ended. Start a new one.")
    bot_url, token, now = resolve_bot_url(s.cfg, body.get("bot_server_url")), new_bot_token(), utcnow_iso()
    applied, m = s.store.mutate_meeting(meeting_id, lambda x: {
        "status": "bot_joining", "status_detail": "Sending the bot to the meeting", "bot_token_hash": hash_token(token),
        "bot_server_url": bot_url, "last_bot_activity": now} if x["status"] == "created" else None)
    if not applied:
        return jsonify({"meeting": public_meeting(m)}), 200
    payload = {"meeting_id": meeting_id, "server": m["jitsi_server"], "room": m["room_name"], "bot_token": token,
               "backend_url": (s.cfg.public_backend_url or request.host_url).rstrip("/"), "bot_display_name": s.cfg.bot_display_name}
    try:
        s.bot_client.join(bot_url, payload)
    except BotUnreachable:
        _fail(meeting_id, "BOT_UNREACHABLE", "The bot server could not be reached. Check the Server URL and that the bot is running.")
        raise ApiError(502, "BOT_UNREACHABLE", "The bot server could not be reached. Check the Server URL and that the bot is running.") from None
    except BotRejected as exc:
        _fail(meeting_id, "BOT_REJECTED", "The bot server refused the request.")
        raise ApiError(502, "BOT_REJECTED", f"The bot server refused the request (HTTP {exc.status}).") from None
    return jsonify({"meeting": public_meeting(s.store.get_meeting(meeting_id))}), 202


@bp.get("/meetings")
@require_user
def list_meetings():
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 100)
    except ValueError:
        raise ApiError(400, "INVALID_PARAMETER", "limit must be a number.") from None
    status = request.args.get("status") or None
    return jsonify({"meetings": [public_meeting(m) for m in svc().store.list_meetings(g.user["uid"], limit, status)]})


@bp.get("/meetings/<meeting_id>")
@require_user
def get_meeting(meeting_id):
    return jsonify({"meeting": public_meeting(owned_meeting(meeting_id))})


@bp.get("/meetings/<meeting_id>/status")
@require_user
def meeting_status(meeting_id):
    m = public_meeting(owned_meeting(meeting_id))
    keys = ("id", "status", "status_detail", "error", "audio_confirmed", "participants", "started_at", "stop_requested")
    return jsonify({k: m[k] for k in keys})


@bp.get("/meetings/<meeting_id>/minutes")
@require_user
def get_minutes(meeting_id):
    m = owned_meeting(meeting_id)
    if m["status"] != "completed":
        raise ApiError(409, "MINUTES_NOT_READY", f"The minutes are not ready (status: {m['status']}).")
    out = svc().store.get_output(meeting_id)
    if out is None:
        raise ApiError(404, "NOT_FOUND", "Minutes not found.")
    return jsonify(out)


@bp.get("/meetings/<meeting_id>/export")
@require_user
def export_minutes(meeting_id):
    m = owned_meeting(meeting_id)
    fmt = (request.args.get("format") or "pdf").lower()
    if fmt not in ("pdf", "txt"):
        raise ApiError(400, "UNSUPPORTED_FORMAT", "format must be 'pdf' or 'txt'.")
    if m["status"] != "completed":
        raise ApiError(409, "MINUTES_NOT_READY", "The minutes are not ready yet.")
    doc = svc().store.get_output(meeting_id)
    if doc is None:
        raise ApiError(404, "NOT_FOUND", "Minutes not found.")
    if fmt == "txt":
        body, mime = render_text(doc).encode("utf-8"), "text/plain; charset=utf-8"
    else:
        body, mime = render_pdf(doc), "application/pdf"
    return Response(body, mimetype=mime, headers={"Content-Disposition": content_disposition(doc["meeting_title"], fmt)})


@bp.post("/meetings/<meeting_id>/stop")
@require_user
def stop_meeting(meeting_id):
    """Ask the bot to leave; it uploads its last audio and then calls /complete. If the bot is unreachable, finalize now."""
    s, m = svc(), owned_meeting(meeting_id)
    if m["status"] not in LIVE_STATUSES:
        return jsonify({"meeting": public_meeting(m)}), 200
    s.store.mutate_meeting(meeting_id, lambda x: {"stop_requested": True, "status_detail": "Stopping"} if x["status"] in LIVE_STATUSES else None)
    try:
        s.bot_client.leave(m["bot_server_url"], meeting_id)
    except (BotUnreachable, BotRejected):
        status, _ = s.pipeline.submit_finalize(meeting_id, "requested")
        return jsonify({"status": status}), 202
    return jsonify({"status": "stopping"}), 202


@bp.post("/meetings/<meeting_id>/retry")
@require_user
def retry_meeting(meeting_id):
    owned_meeting(meeting_id)
    status, applied = svc().pipeline.submit_retry(meeting_id)
    if not applied:
        raise ApiError(409, "NOT_RETRYABLE", "This meeting cannot be retried.")
    return jsonify({"status": status}), 202


# ------------------------------------------------------------------ bot -> backend
def _participants_snapshot(existing: list, incoming: list, now: str, bot_name: str) -> list:
    by_id = {p["id"]: dict(p) for p in existing}
    seen = set()
    for p in incoming:
        pid, name = str(p.get("id", ""))[:64], clean_name(p.get("name"), "Participant", 80)
        if not _PID.match(pid) or name == bot_name:
            continue
        seen.add(pid)
        rec = by_id.setdefault(pid, {"id": pid, "name": name, "joined_at": now, "left_at": None})
        rec["name"], rec["left_at"] = name, None
    for pid, rec in by_id.items():
        if pid not in seen and rec["left_at"] is None:
            rec["left_at"] = now
    return list(by_id.values())


def _apply_event(m: dict, ev: dict, cfg, now: str):
    if m["status"] not in LIVE_STATUSES:
        return None
    f = {"last_bot_activity": now}
    kind = ev["type"]
    if kind == "joining" and m["status"] in ("bot_joining", "waiting"):
        f.update(status="bot_joining", status_detail="The bot is joining the meeting")
    elif kind == "waiting":
        f.update(status="waiting", status_detail="Waiting for the host to let the bot in")
    elif kind == "joined":
        f.update(status="capturing" if m["audio_confirmed"] else "joined", status_detail="", started_at=m.get("started_at") or now)
    elif kind == "participants":
        f["participants"] = _participants_snapshot(m["participants"], ev.get("participants") or [], now, cfg.bot_display_name)
    elif kind == "participant_joined":
        f["participants"] = _participants_snapshot(m["participants"], [{"id": ev.get("id"), "name": ev.get("name")}] + [
            {"id": p["id"], "name": p["name"]} for p in m["participants"] if p["left_at"] is None], now, cfg.bot_display_name)
    elif kind == "participant_left":
        f["participants"] = [{**p, "left_at": p["left_at"] or now} if p["id"] == ev.get("id") else p for p in m["participants"]]
    elif kind == "disconnected":
        f.update(status="disconnected", status_detail="The bot lost its connection to the meeting")
    elif kind == "error":
        code = ev.get("code") if ev.get("code") in BOT_ERRORS else "JOIN_FAILED"
        err = {"code": code, "message": BOT_ERRORS[code], "retryable": False}
        if m["audio_confirmed"] and code in ("BOT_CRASHED", "JOIN_FAILED"):
            f.update(status="disconnected", status_detail=err["message"])  # audio already captured: can still be finalized
        else:
            f.update(status="failed", status_detail="", error=err, bot_token_hash=None)
    return f


@bp.post("/bot/meetings/<meeting_id>/events")
def bot_event(meeting_id):
    s = svc()
    bot_meeting(meeting_id)
    ev = json_body()
    if ev.get("type") not in EVENT_TYPES:
        raise ApiError(400, "INVALID_EVENT", "Unknown event type.")
    now = utcnow_iso()
    applied, m = s.store.mutate_meeting(meeting_id, lambda x: _apply_event(x, ev, s.cfg, now))
    if not applied:
        raise ApiError(409, "MEETING_CLOSED", "The meeting is no longer active.")
    if m["status"] == "failed":
        s.pipeline.purge(meeting_id)
    return jsonify({"status": m["status"]})


@bp.post("/bot/meetings/<meeting_id>/audio")
def bot_audio(meeting_id):
    s, m = svc(), bot_meeting(meeting_id)
    if m["status"] not in LIVE_STATUSES:
        raise ApiError(409, "MEETING_CLOSED", "The meeting is no longer active.")
    upload = request.files.get("file")
    data = upload.read() if upload else b""
    if not data:
        raise ApiError(400, "AUDIO_REQUIRED", "An audio file is required.")
    if len(data) > s.cfg.max_audio_chunk_bytes:
        raise ApiError(413, "AUDIO_TOO_LARGE", "The audio chunk is too large.")
    pid = request.form.get("participant_id", "")
    try:
        seq, offset_ms, duration_ms = (int(request.form[k]) for k in ("seq", "offset_ms", "duration_ms"))
        rms = float(request.form["rms"]) if request.form.get("rms") else None
    except (KeyError, ValueError):
        raise ApiError(400, "INVALID_AUDIO_METADATA", "seq, offset_ms and duration_ms must be integers.") from None
    if not _PID.match(pid) or seq < 0 or offset_ms < 0 or not 0 < duration_ms <= 3_600_000 or (rms is not None and not math.isfinite(rms)):
        raise ApiError(400, "INVALID_AUDIO_METADATA", "Invalid audio metadata.")
    meta = {"participant_id": pid, "participant_name": clean_name(request.form.get("participant_name"), "Participant", 80),
            "seq": seq, "offset_ms": offset_ms, "duration_ms": duration_ms}
    if s.pipeline.store_chunk(meeting_id, pid, seq, data, meta) == "duplicate":
        return jsonify({"stored": False, "duplicate": True})  # bot retry: already have it
    # The backend measures the audio itself; capture is only "confirmed" when real signal was received.
    db = s.pipeline.probe(str(s.pipeline.chunk_path(meeting_id, pid, seq)))
    if db is None and rms:
        db = 20 * math.log10(rms)
    if db is not None and db < s.cfg.min_audio_db:
        s.pipeline.discard_chunk(meeting_id, pid, seq)
        s.store.mutate_meeting(meeting_id, lambda x: {"last_bot_activity": utcnow_iso()} if x["status"] in LIVE_STATUSES else None)
        return jsonify({"stored": False, "reason": "silent"})
    now = utcnow_iso()

    def confirm(x):
        if x["status"] not in LIVE_STATUSES:
            return None
        f = {"last_bot_activity": now}
        if db is not None:
            f["audio_confirmed"] = True
            if x["status"] in ("bot_joining", "joined", "disconnected"):
                f.update(status="capturing", status_detail="", started_at=x.get("started_at") or now)
        return f

    s.store.mutate_meeting(meeting_id, confirm)
    return jsonify({"stored": True, "audio_confirmed": db is not None}), 201


@bp.post("/bot/meetings/<meeting_id>/complete")
def bot_complete(meeting_id):
    s, m = svc(), bot_meeting(meeting_id)
    reason = (request.get_json(silent=True) or {}).get("reason", "meeting_ended")
    if reason not in ("meeting_ended", "bot_left", "error", "requested"):
        raise ApiError(400, "INVALID_REASON", "Unknown completion reason.")
    if m["status"] == "created":
        raise ApiError(409, "MEETING_NOT_STARTED", "The meeting has not started.")
    status, _claimed = s.pipeline.submit_finalize(meeting_id, reason)  # idempotent: only the first call starts processing
    return jsonify({"status": status}), 202

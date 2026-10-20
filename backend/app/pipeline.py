"""Processing pipeline: buffered audio -> transcript -> minutes -> storage -> cleanup.

* Finalization is claimed with an atomic status transition, so it runs exactly once per meeting.
* Raw audio lives only in ``<temp_dir>/<meeting_id>`` and is deleted as soon as processing ends. If a job
  failed for a transient reason it is kept for ``temp_retention_minutes`` so it can be retried, then purged.
* Logs contain ids and counts only, never transcript or audio content.
"""
from __future__ import annotations

import json
import logging
import math
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .asr import ASRUnavailable, filter_segments
from .generator import MinutesValidationError
from .nlp import clean_segments, merge_adjacent, word_count
from .store import LIVE_STATUSES, utcnow_iso

log = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
RETRYABLE = {"ASR_FAILED", "ASR_UNAVAILABLE", "AI_OUTPUT_INVALID", "DATABASE_ERROR"}


class SyncExecutor:
    def submit(self, fn, *args):
        fn(*args)


def probe_mean_db(path: str, timeout: int = 20) -> Optional[float]:
    """Mean volume of an audio file in dB via ffmpeg (about -91 dB is digital silence). None if unavailable."""
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
    return float(m.group(1)) if m else None


class Pipeline:
    def __init__(self, cfg, store, asr, generator, executor=None, probe=probe_mean_db):
        self.cfg, self.store, self.asr, self.generator, self.probe = cfg, store, asr, generator, probe
        self._executor = executor if executor is not None else ThreadPoolExecutor(max_workers=cfg.worker_threads)
        self.temp_root = Path(cfg.temp_dir)

    # ------------------------------------------------------------------ audio buffer
    def meeting_dir(self, meeting_id: str) -> Path:
        if not _SAFE_ID.match(meeting_id):
            raise ValueError("unsafe meeting id")
        return self.temp_root / meeting_id

    def store_chunk(self, meeting_id: str, participant_id: str, seq: int, data: bytes, meta: dict) -> str:
        """Persist one audio chunk to temp storage. Idempotent per (participant, seq) so bot retries are safe."""
        if not _SAFE_ID.match(participant_id):
            raise ValueError("unsafe participant id")
        folder = self.meeting_dir(meeting_id) / participant_id
        folder.mkdir(parents=True, exist_ok=True)
        audio, sidecar = folder / f"{seq:08d}.audio", folder / f"{seq:08d}.json"
        if sidecar.exists():
            return "duplicate"
        audio.write_bytes(data)
        sidecar.write_text(json.dumps(meta), encoding="utf-8")  # written last: marks the chunk complete
        return "stored"

    def discard_chunk(self, meeting_id: str, participant_id: str, seq: int) -> None:
        for suffix in (".json", ".audio"):
            (self.meeting_dir(meeting_id) / participant_id / f"{seq:08d}{suffix}").unlink(missing_ok=True)

    def chunk_path(self, meeting_id: str, participant_id: str, seq: int) -> Path:
        return self.meeting_dir(meeting_id) / participant_id / f"{seq:08d}.audio"

    def _chunks(self, meeting_id: str) -> list:
        base, chunks = self.meeting_dir(meeting_id), []
        if not base.exists():
            return chunks
        for sidecar in base.glob("*/*.json"):
            audio = sidecar.with_suffix(".audio")
            if audio.exists():
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
                chunks.append({"audio": audio, **meta})
        chunks.sort(key=lambda c: (c["offset_ms"], c["participant_id"], c["seq"]))
        return chunks

    def purge(self, meeting_id: str) -> None:
        shutil.rmtree(self.meeting_dir(meeting_id), ignore_errors=True)

    def has_audio(self, meeting_id: str) -> bool:
        return bool(self._chunks(meeting_id))

    # ------------------------------------------------------------------ finalize (exactly once)
    def submit_finalize(self, meeting_id: str, reason: str):
        """Claim finalization atomically and run it in the background. Returns (status, claimed)."""
        def claim(m):
            if m["status"] not in LIVE_STATUSES:
                return None
            return {"status": "processing", "status_detail": "Generating minutes", "finalize_reason": reason,
                    "ended_at": utcnow_iso(), "error": None, "bot_token_hash": None}

        applied, m = self.store.mutate_meeting(meeting_id, claim)
        if applied:
            self._executor.submit(self._run_safe, meeting_id)
        return m["status"], applied

    def submit_retry(self, meeting_id: str):
        def claim(m):
            if m["status"] == "failed" and (m.get("error") or {}).get("retryable"):
                return {"status": "processing", "status_detail": "Generating minutes", "error": None}
            return None

        applied, m = self.store.mutate_meeting(meeting_id, claim)
        if applied:
            self._executor.submit(self._run_safe, meeting_id)
        return m["status"], applied

    def _run_safe(self, meeting_id: str) -> None:
        try:
            self.run(meeting_id)
        except Exception:  # last-resort guard so a job can never leave a meeting stuck in "processing"
            log.exception("finalize crashed for meeting %s", meeting_id)
            self._fail(meeting_id, "PROCESSING_ERROR", "Something went wrong while generating the minutes.", retryable=False)

    def _fail(self, meeting_id, code, message, retryable):
        try:
            self.store.mutate_meeting(meeting_id, lambda m: {
                "status": "failed", "status_detail": "", "bot_token_hash": None,
                "error": {"code": code, "message": message, "retryable": retryable}})
        except Exception:
            log.exception("could not record failure for meeting %s", meeting_id)
        if not retryable:
            self.purge(meeting_id)
        log.warning("meeting %s failed: %s", meeting_id, code)

    def run(self, meeting_id: str) -> None:
        meeting = self.store.get_meeting(meeting_id)
        chunks = self._chunks(meeting_id)
        if not chunks:
            return self._fail(meeting_id, "NO_AUDIO", "No meeting audio was captured, so no minutes could be generated.", False)

        # speaker label per participant id (stable, unique even if two people share a display name)
        labels, used = {}, {}
        for c in chunks:
            if c["participant_id"] not in labels:
                name = (c.get("participant_name") or "Participant").strip() or "Participant"
                used[name] = used.get(name, 0) + 1
                labels[c["participant_id"]] = name if used[name] == 1 else f"{name} ({used[name]})"

        raw, failures = [], 0
        for c in chunks:
            try:
                segs = filter_segments(self.asr.transcribe(str(c["audio"]), self.cfg.whisper_language or None))
            except ASRUnavailable:
                return self._fail(meeting_id, "ASR_UNAVAILABLE", "Speech recognition is not available on the server.", True)
            except Exception as exc:
                failures += 1
                log.warning("ASR failed for a chunk of meeting %s: %s", meeting_id, type(exc).__name__)
                continue
            for s in segs:
                raw.append({"speaker": labels[c["participant_id"]], "text": s["text"], "uncertain": s["uncertain"],
                            "start_time": round(c["offset_ms"] / 1000 + s["start"], 2),
                            "end_time": round(c["offset_ms"] / 1000 + s["end"], 2)})
        if failures == len(chunks):
            return self._fail(meeting_id, "ASR_FAILED", "The audio could not be transcribed.", True)

        transcript = merge_adjacent(clean_segments(raw))
        if sum(word_count(s["text"]) for s in transcript) < self.cfg.min_transcript_words:
            return self._fail(meeting_id, "EMPTY_TRANSCRIPT", "No intelligible speech was recognised in the meeting audio.", False)

        duration = max(c["offset_ms"] + c.get("duration_ms", 0) for c in chunks) / 1000
        names = [p["name"] for p in meeting.get("participants", []) if p.get("name") and not p.get("is_bot")]
        for label in labels.values():
            if label not in names:
                names.append(label)
        try:
            output = self.generator.generate({**meeting, "duration_seconds": duration}, transcript, [{"name": n} for n in dict.fromkeys(names)])
        except MinutesValidationError:
            return self._fail(meeting_id, "AI_OUTPUT_INVALID", "The generated minutes failed validation.", True)
        if failures:
            output["warnings"].append(f"{failures} audio segment(s) could not be transcribed, so parts of the meeting may be missing.")

        try:
            self.store.save_output(meeting_id, output, {
                "status": "completed", "status_detail": "", "error": None, "duration_seconds": duration,
                "generated_by": output["generated_by"], "completed_at": utcnow_iso(),
                "summary_preview": output["summary"][:200], "bot_token_hash": None})
        except Exception:
            log.exception("saving output failed for meeting %s", meeting_id)
            return self._fail(meeting_id, "DATABASE_ERROR", "The minutes were generated but could not be saved. Please retry.", True)
        self.purge(meeting_id)  # raw audio is never kept after successful processing
        log.info("meeting %s completed (%d segments)", meeting_id, len(transcript))

    # ------------------------------------------------------------------ housekeeping
    def sweep(self, now: Optional[datetime] = None) -> None:
        """Finalize meetings whose bot went silent, and delete expired temp data."""
        now = now or datetime.now(timezone.utc)
        idle = timedelta(minutes=self.cfg.bot_idle_timeout_minutes)
        for m in self.store.list_meetings_by_status(LIVE_STATUSES):
            last = datetime.fromisoformat(m.get("last_bot_activity") or m["created_at"])
            if now - last <= idle:
                continue
            if self.has_audio(m["id"]):
                self.submit_finalize(m["id"], "bot_timeout")
            else:
                self.store.mutate_meeting(m["id"], lambda x: {"status": "failed", "bot_token_hash": None, "error": {
                    "code": "BOT_TIMEOUT", "message": "The bot stopped responding before any audio was captured.", "retryable": False}}
                    if x["status"] in LIVE_STATUSES else None)
                self.purge(m["id"])
        ttl = timedelta(minutes=self.cfg.temp_retention_minutes)
        if self.temp_root.exists():
            for d in self.temp_root.iterdir():
                if not d.is_dir():
                    continue
                m = self.store.get_meeting(d.name)
                if m and m["status"] in LIVE_STATUSES + ("processing",):
                    continue
                if now - datetime.fromtimestamp(d.stat().st_mtime, timezone.utc) > ttl or (m and m["status"] == "completed"):
                    shutil.rmtree(d, ignore_errors=True)

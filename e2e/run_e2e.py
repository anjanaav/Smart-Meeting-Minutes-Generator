"""End-to-end contract check: real Flask backend <-> real Node bot over HTTP.

What is real: HTTP between app/backend/bot, the bot's session state machine, WAV encoding and upload,
backend audio probing (ffmpeg), exactly-once finalization, rule-based minutes, storage, export, access control,
and deletion of temporary audio.
What is NOT real (and cannot be in CI): the browser/Jitsi room (BOT_DRIVER=fake emits a sine tone as 'speech')
and Whisper (a scripted ASR returns a known conversation). So this proves the wiring, not transcription quality.

Run from the repo root:   python3 e2e/run_e2e.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402
from app.asr import ASREngine  # noqa: E402
from app.config import Config  # noqa: E402

SECRET = "e2e-shared-secret-" + "x" * 32
CONVERSATION = [
    {"start": 0.0, "end": 4.0, "text": "Let's start with the launch date for the mobile release.", "avg_logprob": -0.2, "no_speech_prob": 0.01},
    {"start": 4.0, "end": 8.0, "text": "We decided to ship the beta on Friday the twentieth.", "avg_logprob": -0.2, "no_speech_prob": 0.01},
    {"start": 8.0, "end": 12.0, "text": "I will send the release notes to the team by Wednesday.", "avg_logprob": -0.2, "no_speech_prob": 0.01},
]


class ScriptedASR(ASREngine):
    def is_available(self):
        return True

    def transcribe(self, path, language=None):
        return [dict(s) for s in CONVERSATION]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(method, url, token=None, body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            payload = r.read()
            return r.status, (payload if raw else json.loads(payload or b"{}"))
    except urllib.error.HTTPError as e:
        payload = e.read()
        return e.code, (payload if raw else json.loads(payload or b"{}"))


def wait_for(fn, what, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.4)
    raise AssertionError(f"timed out waiting for: {what}")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="smmg-e2e-"))
    be_port, bot_port = free_port(), free_port()
    cfg = Config(auth_mode="dev", store_backend="memory", temp_dir=str(tmp / "audio"), llm_provider="rules",
                 bot_server_url=f"http://127.0.0.1:{bot_port}", bot_shared_secret=SECRET,
                 public_backend_url=f"http://127.0.0.1:{be_port}", min_transcript_words=5)
    app = create_app(cfg, asr=ScriptedASR(), resolver=lambda h: ["8.8.8.8"], start_background=False)
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    hits = {"complete": 0}

    @app.after_request
    def _count(resp):
        from flask import request
        if request.method == "POST" and request.path.endswith("/complete"):
            hits["complete"] += 1
        return resp

    server = make_server("127.0.0.1", be_port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    env = {**os.environ, "BOT_DRIVER": "fake", "BOT_HOST": "127.0.0.1", "BOT_PORT": str(bot_port), "BOT_SHARED_SECRET": SECRET,
           "BOT_CHUNK_MS": "1000", "BOT_POLL_MS": "300", "BOT_HEARTBEAT_MS": "2000"}
    env.pop("NODE_ENV", None)
    bot = subprocess.Popen(["node", "src/index.js"], cwd=ROOT / "bot", env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    api = f"http://127.0.0.1:{be_port}/api"
    alice, mallory = "dev:alice:alice@example.com", "dev:mallory:mallory@example.com"
    results = []

    def check(name, cond, extra=""):
        results.append((name, bool(cond)))
        print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))

    try:
        wait_for(lambda: call("GET", f"http://127.0.0.1:{bot_port}/api/health")[0] == 200 if _up(bot_port) else False, "bot up", 15)
        st, h = call("GET", f"{api}/health")
        check("backend health (Test button)", st == 200 and h.get("ok"))

        st, b = call("POST", f"{api}/meetings", alice, {"meeting_url": "https://meet.jit.si/e2e-room", "display_name": "Alice", "consent": False})
        check("consent is required", st == 400 and b["error"]["code"] == "CONSENT_REQUIRED")
        st, b = call("POST", f"{api}/meetings", alice, {"meeting_url": "https://meet.jit.si/..", "consent": True})
        check("'..' room rejected at the backend (INVALID_MEETING_LINK)", st == 400 and b["error"]["code"] == "INVALID_MEETING_LINK", b)

        st, b = call("POST", f"{api}/meetings", alice, {"meeting_url": "https://meet.jit.si/e2e-room", "display_name": "Alice", "consent": True})
        mid = b["meeting"]["id"]
        check("meeting created", st == 201)
        st, b2 = call("POST", f"{api}/meetings", alice, {"meeting_url": "https://meet.jit.si/e2e-room", "consent": True})
        check("same room does not create a duplicate record", st == 200 and b2["reused"] and b2["meeting"]["id"] == mid)

        st, b = call("POST", f"{api}/meetings/{mid}/bot/join", alice, {})
        check("join request accepted by the bot (202)", st == 202, b)

        seen = []
        def status():
            s = call("GET", f"{api}/meetings/{mid}/status", alice)[1]
            if not seen or seen[-1] != s["status"]:
                seen.append(s["status"])
            return s
        s = wait_for(lambda: (x := status())["status"] in ("capturing",) and x, "capturing")
        check("backend confirmed audio (capturing, audio_confirmed)", s["audio_confirmed"] is True, s)
        check("participant metadata reached the backend", any(p.get("name") == "Alice Example" for p in s["participants"]), s["participants"])

        st, _ = call("GET", f"{api}/meetings/{mid}", mallory)
        check("another user gets 404 for the meeting", st == 404)
        st, _ = call("GET", f"{api}/meetings/{mid}/minutes", mallory)
        check("another user cannot read the minutes", st == 404)
        st, _ = call("POST", f"{api}/meetings/{mid}/stop", mallory)
        check("another user cannot stop the meeting", st == 404)

        audio_dir = Path(cfg.temp_dir) / mid
        check("raw audio is buffered temporarily while capturing", audio_dir.exists() and any(audio_dir.iterdir()))

        st, _ = call("POST", f"{api}/meetings/{mid}/stop", alice)
        check("stop accepted", st == 202)
        wait_for(lambda: status()["status"] == "completed", "completed", 90)
        order = ["created", "bot_joining", "waiting", "joined", "capturing", "processing", "completed"]
        idx = [order.index(x) for x in seen if x in order]
        check("states only moved forward and ended completed", seen[-1] == "completed" and idx == sorted(idx), seen)
        check("no unexpected states (failed/disconnected)", all(x in order for x in seen), seen)

        st, doc = call("GET", f"{api}/meetings/{mid}/minutes", alice)
        check("minutes retrievable", st == 200 and doc["transcript"])
        check("transcript carries speaker + timestamps", all(t["speaker"] and "start_time" in t for t in doc["transcript"]), doc["transcript"][:1])
        check("speaker is the real participant name", any(t["speaker"] == "Alice Example" for t in doc["transcript"]))
        check("decision extracted", any("beta" in d["description"].lower() for d in doc["decisions"]), doc["decisions"])
        check("action item extracted, deadline from transcript only",
              any("release notes" in a["task"].lower() for a in doc["action_items"]), doc["action_items"])
        check("no invented assignee/deadline fields missing", all("assignee" in a and "deadline" in a for a in doc["action_items"]))
        st, txt = call("GET", f"{api}/meetings/{mid}/export?format=txt", alice, raw=True)
        check("txt export works", st == 200 and b"release" in txt.lower())
        st, _ = call("GET", f"{api}/meetings/{mid}/export?format=txt", mallory, raw=True)
        check("another user cannot export", st == 404)
        st, lst = call("GET", f"{api}/meetings", alice)
        check("history lists the meeting for its owner only", st == 200 and any(m["id"] == mid for m in lst["meetings"]))
        st, lst = call("GET", f"{api}/meetings", mallory)
        check("history for another user is empty", st == 200 and not lst["meetings"])

        time.sleep(1)
        check("temporary audio deleted after processing", not audio_dir.exists())

        st, again = call("POST", f"{api}/meetings/{mid}/stop", alice)
        check("late stop after completion is harmless", st == 200)
    except Exception as exc:  # noqa: BLE001
        check(f"harness error: {exc}", False)
    finally:
        bot.terminate()
        try:
            out = bot.communicate(timeout=70)[0]
        except subprocess.TimeoutExpired:
            bot.kill()
            out = bot.communicate()[0]
        server.shutdown()
        check("bot sent 'complete' exactly once", hits["complete"] == 1, hits)
        (tmp / "bot.log").write_text(out or "")
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("bot log tail:\n" + "\n".join((out or "").splitlines()[-25:]))
    sys.exit(1 if failed else 0)


def _up(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    main()

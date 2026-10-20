import json
import tempfile
import unittest
from dataclasses import replace

from app import create_app
from app.asr import ASREngine
from app.bot_client import BotRejected, BotUnreachable
from app.config import Config
from app.generator import LLMError
from app.pipeline import SyncExecutor
from app.store import MemoryStore


def seg(start, end, text, logprob=-0.2, no_speech=0.01):
    return {"start": start, "end": end, "text": text, "avg_logprob": logprob, "no_speech_prob": no_speech}


def audio(*segments):
    """Test 'audio' is the JSON the fake ASR will return for it."""
    return json.dumps(list(segments)).encode()


class FakeASR(ASREngine):
    def __init__(self):
        self.calls = []
        self.fail = False

    def is_available(self):
        return True

    def transcribe(self, path, language=None):
        self.calls.append(path)
        if self.fail:
            raise RuntimeError("asr boom")
        with open(path, "rb") as f:
            return json.loads(f.read().decode())


class FakeBot:
    def __init__(self):
        self.joins, self.leaves, self.unreachable, self.reject = [], [], False, False

    def join(self, url, payload):
        if self.unreachable:
            raise BotUnreachable(url)
        if self.reject:
            raise BotRejected(403, "no")
        self.joins.append((url, payload))
        return {}

    def leave(self, url, meeting_id):
        if self.unreachable:
            raise BotUnreachable(url)
        self.leaves.append(meeting_id)
        return {}


class ScriptedProvider:
    """Returns queued responses (dicts) or raises queued exceptions."""
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def extract(self, system, user, schema, name):
        self.calls.append({"system": system, "user": user, "name": name})
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


def fake_probe(path):
    with open(path, "rb") as f:
        return -91.0 if f.read(6).startswith(b"SILENT") else -20.0


GOOD_EXTRACTION = {
    "summary": "The team reviewed the release plan.",
    "key_points": [{"topic": "Release plan", "details": "Release scheduled after testing.", "segment_ids": [1]}],
    "decisions": [{"description": "Ship version 2 on the 15th.", "speaker": "Alice", "context": "Release", "segment_ids": [1]}],
    "action_items": [{"task": "Send the release notes", "assignee": "Bob", "deadline": "by Friday", "segment_ids": [2]}],
}


class AppCase(unittest.TestCase):
    provider = ScriptedProvider(GOOD_EXTRACTION)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = Config(auth_mode="dev", store_backend="memory", temp_dir=self._tmp.name, env="test", min_transcript_words=3,
                          bot_server_url="http://10.0.2.2:3000")
        self.asr, self.bot, self.store = FakeASR(), FakeBot(), MemoryStore()
        self.provider = ScriptedProvider(GOOD_EXTRACTION)
        self.build()

    def build(self, **kw):
        self.app = create_app(kw.pop("cfg", self.cfg), store=self.store, asr=self.asr, provider=kw.pop("provider", self.provider),
                              bot_client=self.bot, resolver=lambda h: ["8.8.8.8"], probe=fake_probe,
                              executor=SyncExecutor(), start_background=False)
        self.client = self.app.test_client()
        self.pipeline = self.app.extensions["smmg"].pipeline

    # -- helpers
    def hdr(self, uid="alice"):
        return {"Authorization": f"Bearer dev:{uid}"}

    def create(self, uid="alice", url="https://meet.jit.si/standup-room", **extra):
        r = self.client.post("/api/meetings", json={"meeting_url": url, "consent": True, **extra}, headers=self.hdr(uid))
        return r

    def start(self, uid="alice", url="https://meet.jit.si/standup-room"):
        """Create a meeting and send the bot; returns (meeting_id, bot_token)."""
        mid = self.create(uid, url).get_json()["meeting"]["id"]
        r = self.client.post(f"/api/meetings/{mid}/bot/join", json={}, headers=self.hdr(uid))
        assert r.status_code == 202, r.get_json()
        return mid, self.bot.joins[-1][1]["bot_token"]

    def bot_h(self, token):
        return {"Authorization": f"Bot {token}"}

    def event(self, mid, token, **ev):
        return self.client.post(f"/api/bot/meetings/{mid}/events", json=ev, headers=self.bot_h(token))

    def upload(self, mid, token, payload, pid="p1", name="Alice", seq=0, offset_ms=0, duration_ms=30000):
        import io
        data = {"file": (io.BytesIO(payload), "chunk.webm"), "participant_id": pid, "participant_name": name,
                "seq": str(seq), "offset_ms": str(offset_ms), "duration_ms": str(duration_ms), "rms": "0.1"}
        return self.client.post(f"/api/bot/meetings/{mid}/audio", data=data, headers=self.bot_h(token), content_type="multipart/form-data")

    def complete(self, mid, token, reason="meeting_ended"):
        return self.client.post(f"/api/bot/meetings/{mid}/complete", json={"reason": reason}, headers=self.bot_h(token))

    def status(self, mid, uid="alice"):
        return self.client.get(f"/api/meetings/{mid}/status", headers=self.hdr(uid)).get_json()

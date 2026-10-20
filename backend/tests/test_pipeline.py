import os
import time
import unittest
from datetime import datetime, timedelta, timezone

from tests.helpers import GOOD_EXTRACTION, AppCase, ScriptedProvider, audio, seg
from app.generator import LLMError

SPEECH = audio(seg(0, 4, "We decided to ship version two on the fifteenth."), seg(5, 9, "I will send the release notes by Friday."))


class FailureHandlingTests(AppCase):
    def finish(self, payload=SPEECH):
        mid, token = self.start()
        self.event(mid, token, type="joined")
        if payload is not None:
            self.upload(mid, token, payload, name="Alice")
        self.complete(mid, token)
        return mid

    def err(self, mid):
        return self.status(mid)["error"]

    def test_no_audio_at_all(self):
        mid = self.finish(None)
        s = self.status(mid)
        self.assertEqual((s["status"], s["error"]["code"]), ("failed", "NO_AUDIO"))
        self.assertEqual(self.asr.calls, [])

    def test_empty_or_unintelligible_transcript_is_not_turned_into_minutes(self):
        mid = self.finish(audio(seg(0, 3, "[BLANK_AUDIO]"), seg(3, 6, "um"), seg(6, 9, "Thank you.", logprob=-2.0, no_speech=0.95)))
        self.assertEqual(self.err(mid)["code"], "EMPTY_TRANSCRIPT")
        self.assertEqual(self.provider.calls, [])                       # no AI call, nothing fabricated
        self.assertIsNone(self.store.get_output(mid))
        self.assertFalse(os.path.exists(self.pipeline.meeting_dir(mid)))

    def test_low_confidence_speech_is_kept_but_flagged_uncertain(self):
        mid = self.finish(audio(seg(0, 4, "We maybe ship on friday possibly", logprob=-1.4, no_speech=0.1), seg(5, 9, "Clear sentence about the budget review.")))
        doc = self.client.get(f"/api/meetings/{mid}/minutes", headers=self.hdr()).get_json()
        self.assertTrue(doc["transcript"][0]["uncertain"])

    def test_asr_failure_is_retryable_and_audio_kept_only_until_retry(self):
        self.asr.fail = True
        mid = self.finish()
        e = self.err(mid)
        self.assertEqual((e["code"], e["retryable"]), ("ASR_FAILED", True))
        self.assertTrue(os.path.isdir(self.pipeline.meeting_dir(mid)))   # kept briefly so the retry can work
        self.asr.fail = False
        r = self.client.post(f"/api/meetings/{mid}/retry", headers=self.hdr())
        self.assertEqual(r.status_code, 202)
        self.assertEqual(self.status(mid)["status"], "completed")
        self.assertFalse(os.path.exists(self.pipeline.meeting_dir(mid)))
        self.assertEqual(self.client.post(f"/api/meetings/{mid}/retry", headers=self.hdr()).status_code, 409)   # not retryable twice

    def test_non_retryable_failures_cannot_be_retried(self):
        mid = self.finish(None)
        self.assertEqual(self.client.post(f"/api/meetings/{mid}/retry", headers=self.hdr()).status_code, 409)

    def test_partial_asr_failure_adds_warning_but_completes(self):
        mid, token = self.start()
        self.event(mid, token, type="joined")
        self.upload(mid, token, SPEECH, seq=0)
        self.upload(mid, token, b"not json at all", seq=1, offset_ms=30000)   # this chunk will make the fake ASR raise
        self.complete(mid, token)
        doc = self.client.get(f"/api/meetings/{mid}/minutes", headers=self.hdr()).get_json()
        self.assertTrue(any("could not be transcribed" in w for w in doc["warnings"]))

    def test_ai_service_failure_still_produces_labelled_minutes(self):
        self.provider.responses = [LLMError("down")]
        mid = self.finish()
        doc = self.client.get(f"/api/meetings/{mid}/minutes", headers=self.hdr()).get_json()
        self.assertEqual(doc["generated_by"], "rule_based")
        self.assertTrue(doc["warnings"])
        self.assertEqual(doc["action_items"][0]["assignee"], "Alice")        # explicit "I will ..." by Alice
        self.assertEqual(doc["action_items"][0]["deadline"], "by Friday")

    def test_database_failure_is_reported_and_retryable(self):
        original = self.store.save_output
        self.store.save_output = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down"))
        mid = self.finish()
        self.assertEqual((self.err(mid)["code"], self.err(mid)["retryable"]), ("DATABASE_ERROR", True))
        self.store.save_output = original
        self.assertEqual(self.client.post(f"/api/meetings/{mid}/retry", headers=self.hdr()).status_code, 202)
        self.assertEqual(self.status(mid)["status"], "completed")

    def test_unexpected_crash_never_leaves_meeting_stuck_in_processing(self):
        self.pipeline.generator.generate = lambda *a, **k: (_ for _ in ()).throw(ZeroDivisionError())
        mid = self.finish()
        self.assertEqual(self.status(mid)["status"], "failed")

    def test_duplicate_display_names_get_distinct_speaker_labels(self):
        mid, token = self.start()
        self.event(mid, token, type="joined")
        self.upload(mid, token, audio(seg(0, 4, "Hello from the first person here today.")), pid="p1", name="Sam")
        self.upload(mid, token, audio(seg(0, 4, "Hello from the second person here today.")), pid="p2", name="Sam", offset_ms=20000)
        self.complete(mid, token)
        doc = self.client.get(f"/api/meetings/{mid}/minutes", headers=self.hdr()).get_json()
        self.assertEqual([t["speaker"] for t in doc["transcript"]], ["Sam", "Sam (2)"])


class SweepTests(AppCase):
    def later(self, minutes):
        return datetime.now(timezone.utc) + timedelta(minutes=minutes)

    def test_silent_bot_with_audio_is_finalized_automatically(self):
        mid, token = self.start()
        self.event(mid, token, type="joined")
        self.upload(mid, token, SPEECH)
        self.pipeline.sweep(now=self.later(5))
        self.assertEqual(self.status(mid)["status"], "capturing")           # still within the idle window
        self.pipeline.sweep(now=self.later(30))
        self.assertEqual(self.status(mid)["status"], "completed")           # meeting ended unexpectedly: minutes still generated

    def test_bot_that_never_joined_fails_cleanly(self):
        mid, _ = self.start()
        self.pipeline.sweep(now=self.later(30))
        s = self.status(mid)
        self.assertEqual((s["status"], s["error"]["code"]), ("failed", "BOT_TIMEOUT"))

    def test_expired_temp_data_is_purged_but_active_meetings_are_not(self):
        self.asr.fail = True
        failed = self.start("alice", "https://meet.jit.si/will-fail")
        self.upload(failed[0], failed[1], SPEECH)
        self.complete(*failed)                                              # retryable failure keeps temp data
        live = self.start("alice", "https://meet.jit.si/still-live")
        self.upload(live[0], live[1], SPEECH)
        self.assertTrue(os.path.isdir(self.pipeline.meeting_dir(failed[0])))
        old = time.time() - 3 * 3600
        for d in (failed[0], live[0]):
            os.utime(self.pipeline.meeting_dir(d), (old, old))
        self.store.mutate_meeting(live[0], lambda m: {"last_bot_activity": datetime.now(timezone.utc).isoformat()})
        self.pipeline.sweep(now=datetime.now(timezone.utc))
        self.assertFalse(os.path.exists(self.pipeline.meeting_dir(failed[0])))
        self.assertTrue(os.path.isdir(self.pipeline.meeting_dir(live[0])))


class ExportTests(AppCase):
    def test_exports_include_all_sections_and_handle_special_characters(self):
        from app.export import render_pdf, render_text
        doc = {"meeting_title": "R&D <sync>", "meeting_date": "2026-10-19", "duration_seconds": 125, "participants": [{"name": "Alice"}],
               "summary": "A & B", "key_points": [{"topic": "T", "details": "D", "segment_ids": []}], "decisions": [],
               "action_items": [{"task": "Do it", "assignee": None, "deadline": None, "status": "pending"}],
               "transcript": [{"id": 1, "speaker": "Alice", "text": "Hi <b>", "start_time": 3, "end_time": 4, "uncertain": True}], "warnings": ["w"]}
        text = render_text(doc)
        for needle in ("Meeting Summary", "Key Discussion Points", "Decisions Made", "None recorded.", "unassigned", "unspecified", "(unclear)", "00:00:03"):
            self.assertIn(needle, text)
        self.assertTrue(render_pdf(doc).startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()

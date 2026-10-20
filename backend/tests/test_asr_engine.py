"""WhisperEngine: how it behaves when the model cannot be loaded, and how it shapes the engine's output.

The real Whisper model cannot be downloaded in the build sandbox, so faster_whisper.WhisperModel is replaced by a fake. This tests OUR code
around the engine (error mapping, caching, result shape), not recognition quality.
"""
import importlib.util
import unittest
from types import SimpleNamespace
from unittest import mock

from app.asr import ASRUnavailable, WhisperEngine, filter_segments

from .helpers import AppCase, audio, seg

HAS_FASTER = importlib.util.find_spec("faster_whisper") is not None


class FakeModel:
    def __init__(self, name, compute_type=None):
        self.name = name

    def transcribe(self, path, language=None, vad_filter=False):
        self.last = (path, language, vad_filter)
        segments = [SimpleNamespace(start=0.0, end=2.5, text="  Hello team  ", avg_logprob=-0.2, no_speech_prob=0.01),
                    SimpleNamespace(start=2.5, end=4.0, text="we ship Friday", avg_logprob=-1.4, no_speech_prob=0.1)]
        return iter(segments), SimpleNamespace(language="en")


@unittest.skipUnless(HAS_FASTER, "faster-whisper is not installed")
class EngineTests(unittest.TestCase):
    def test_result_shape_is_what_the_pipeline_expects(self):
        with mock.patch("faster_whisper.WhisperModel", FakeModel):
            out = WhisperEngine("tiny").transcribe("a.wav", "en")
        self.assertEqual(out[0], {"start": 0.0, "end": 2.5, "text": "Hello team", "avg_logprob": -0.2, "no_speech_prob": 0.01})
        self.assertEqual([s["text"] for s in out], ["Hello team", "we ship Friday"])

    def test_language_is_passed_and_empty_means_auto_detect(self):
        engine = WhisperEngine("tiny")
        with mock.patch("faster_whisper.WhisperModel", FakeModel):
            engine.transcribe("a.wav", "")
            self.assertEqual(engine._model.last, ("a.wav", None, True))
            engine.transcribe("a.wav", "ml")
            self.assertEqual(engine._model.last[1], "ml")

    def test_the_model_is_loaded_once(self):
        loads = []
        class Counting(FakeModel):
            def __init__(self, *a, **k):
                loads.append(1)
                super().__init__(*a, **k)
        engine = WhisperEngine("tiny")
        with mock.patch("faster_whisper.WhisperModel", Counting):
            engine.transcribe("a.wav")
            engine.transcribe("b.wav")
        self.assertEqual(len(loads), 1)

    def test_a_model_that_cannot_be_loaded_is_unavailable_not_a_bad_chunk(self):
        def broken(*a, **k):
            raise OSError("cannot reach https://huggingface.co with token hf_secret123")
        with mock.patch("faster_whisper.WhisperModel", broken), self.assertLogs("app.asr", level="ERROR") as logs:
            with self.assertRaises(ASRUnavailable) as ctx:
                WhisperEngine("base").transcribe("a.wav")
        self.assertIn("'base'", str(ctx.exception))
        self.assertNotIn("hf_secret123", str(ctx.exception) + "\n".join(logs.output))   # the library's message (may hold tokens/URLs) is not kept

    def test_loading_is_retried_after_a_failure_and_then_works(self):
        attempts = []
        def flaky(name, compute_type=None):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("offline")
            return FakeModel(name)
        engine = WhisperEngine("tiny")
        with mock.patch("faster_whisper.WhisperModel", flaky):
            with self.assertRaises(ASRUnavailable):
                engine.transcribe("a.wav")
            self.assertEqual(engine.transcribe("a.wav")[0]["text"], "Hello team")   # internet came back: no restart needed

    def test_no_engine_installed_is_unavailable(self):
        with mock.patch("importlib.util.find_spec", return_value=None):
            engine = WhisperEngine("tiny")
            self.assertFalse(engine.is_available())
            with self.assertRaises(ASRUnavailable):
                engine.transcribe("a.wav")

    def test_only_segments_that_are_both_probably_silence_and_low_confidence_are_dropped(self):
        kept = filter_segments([
            {"start": 0, "end": 1, "text": "Hello there everyone", "avg_logprob": -0.1, "no_speech_prob": 0.0},
            {"start": 1, "end": 2, "text": "mumble", "avg_logprob": -1.6, "no_speech_prob": 0.1},               # low confidence: kept, flagged
            {"start": 2, "end": 3, "text": "Thanks for watching", "avg_logprob": -2.0, "no_speech_prob": 0.95},  # silence hallucination: dropped
            {"start": 3, "end": 4, "text": "quiet but clear speech", "avg_logprob": -0.3, "no_speech_prob": 0.95},  # confident text: kept, flagged
            {"start": 4, "end": 5, "text": "   ", "avg_logprob": -0.1, "no_speech_prob": 0.0}])                  # empty: dropped
        self.assertEqual([s["text"] for s in kept], ["Hello there everyone", "mumble", "quiet but clear speech"])
        self.assertEqual([s["uncertain"] for s in kept], [False, True, True])


class SetupFailureReachesTheUser(AppCase):
    def test_unavailable_engine_gives_a_retryable_asr_unavailable_and_keeps_the_audio_for_the_retry(self):
        import os
        from app.asr import ASREngine

        class Offline(ASREngine):
            def is_available(self):
                return True

            def transcribe(self, path, language=None):
                raise ASRUnavailable("The speech model 'base' could not be loaded.")

        self.asr = Offline()
        self.build()
        mid, token = self.start()
        self.upload(mid, token, audio(seg(0, 4, "We will ship version two on the fifteenth.")))
        self.complete(mid, token)
        status = self.status(mid)
        self.assertEqual((status["status"], status["error"]["code"], status["error"]["retryable"]), ("failed", "ASR_UNAVAILABLE", True))
        self.assertTrue(os.path.isdir(self.pipeline.meeting_dir(mid)))                       # kept so a retry can use it
        # internet is back: the engine works, the user taps "Retry processing", and the minutes appear
        from .helpers import FakeASR
        self.asr_fixed = FakeASR()
        self.app.extensions["smmg"].pipeline.asr = self.asr_fixed
        r = self.client.post(f"/api/meetings/{mid}/retry", json={}, headers=self.hdr())
        self.assertEqual(r.status_code, 202)
        self.assertEqual(self.status(mid)["status"], "completed")
        self.assertFalse(os.path.exists(self.pipeline.meeting_dir(mid)))                     # raw audio removed after success


if __name__ == "__main__":
    unittest.main()

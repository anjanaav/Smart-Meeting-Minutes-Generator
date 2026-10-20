"""ASR engine (Module 3): speech-to-text with timestamps.

Uses faster-whisper when installed, otherwise openai-whisper. Models load lazily on first use.
"""
from __future__ import annotations

import importlib.util
import logging
import threading
from typing import Optional

# Segments that are almost certainly silence/hallucination are dropped; weak ones are flagged "uncertain".
DROP_NO_SPEECH = 0.85
UNCERTAIN_NO_SPEECH = 0.6
UNCERTAIN_LOGPROB = -1.0


log = logging.getLogger(__name__)


class ASRUnavailable(RuntimeError):
    pass


class ASREngine:
    def is_available(self) -> bool:
        raise NotImplementedError

    def transcribe(self, path: str, language: Optional[str] = None) -> list:
        """Return [{"start", "end", "text", "avg_logprob", "no_speech_prob"}] with times relative to the file."""
        raise NotImplementedError


class WhisperEngine(ASREngine):
    def __init__(self, model_name: str = "base"):
        self._model_name = model_name
        self._lock = threading.Lock()
        self._model = None
        self._flavour = None

    def is_available(self) -> bool:
        return any(importlib.util.find_spec(m) for m in ("faster_whisper", "whisper"))

    def _load(self):
        with self._lock:
            if self._model is not None:
                return
            try:
                if importlib.util.find_spec("faster_whisper"):
                    from faster_whisper import WhisperModel
                    self._model = WhisperModel(self._model_name, compute_type="int8")
                    self._flavour = "faster"
                elif importlib.util.find_spec("whisper"):
                    import whisper
                    self._model = whisper.load_model(self._model_name)
                    self._flavour = "openai"
                else:
                    raise ASRUnavailable("No speech recognition engine installed (pip install faster-whisper).")
            except ASRUnavailable:
                raise
            except Exception as exc:
                # Typically a first run without internet access (the model is downloaded once), a wrong WHISPER_MODEL name,
                # or too little memory. That is a setup problem, not a bad audio chunk, so it must not be counted as one.
                self._model = None
                log.error("speech model %r could not be loaded: %s", self._model_name, type(exc).__name__)
                raise ASRUnavailable(f"The speech model '{self._model_name}' could not be loaded.") from None

    def transcribe(self, path, language=None):
        self._load()
        if self._flavour == "faster":
            segments, _info = self._model.transcribe(path, language=language or None, vad_filter=True)
            return [
                {"start": s.start, "end": s.end, "text": s.text.strip(),
                 "avg_logprob": s.avg_logprob, "no_speech_prob": s.no_speech_prob}
                for s in segments
            ]
        result = self._model.transcribe(path, language=language or None, fp16=False)
        return [
            {"start": s["start"], "end": s["end"], "text": s["text"].strip(),
             "avg_logprob": s.get("avg_logprob", 0.0), "no_speech_prob": s.get("no_speech_prob", 0.0)}
            for s in result["segments"]
        ]


def filter_segments(raw: list) -> list:
    """Drop near-certain silence hallucinations; flag low-confidence segments as uncertain."""
    out = []
    for s in raw:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        no_speech = s.get("no_speech_prob", 0.0) or 0.0
        logprob = s.get("avg_logprob", 0.0) or 0.0
        if no_speech >= DROP_NO_SPEECH and logprob < UNCERTAIN_LOGPROB:
            continue
        out.append({**s, "uncertain": no_speech >= UNCERTAIN_NO_SPEECH or logprob < UNCERTAIN_LOGPROB})
    return out

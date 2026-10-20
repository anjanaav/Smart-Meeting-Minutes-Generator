"""NLP processor (Module 4): clean and segment the recognised transcript without changing its meaning.

Removes ASR artefacts and disfluencies; keeps names, numbers, dates and technical terms untouched.
"""
from __future__ import annotations

import re

_ARTEFACTS = re.compile(
    r"\[(?:blank_audio|music|noise|silence|applause|laughter|inaudible)\]|\((?:music|noise|silence|applause|laughter)\)|[♪♫]+",
    re.IGNORECASE,
)
_FILLERS = re.compile(r"\b(?:u+m+|u+h+|e+r+m*|h+m+|m+h*m+)\b[,.]?\s*", re.IGNORECASE)
# Only collapse repeats of short function words; "had had" / "that that" / "is is" can be legitimate.
_STUTTER_WORDS = r"(?:the|a|an|i|we|you|he|she|it|they|to|and|of|in|on|so|but|if|at|my|our|your)"
_STUTTER = re.compile(rf"\b({_STUTTER_WORDS})(?:\s+\1\b)+", re.IGNORECASE)
_SPACES = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.!?;:])")
_REPEATED_DOTS = re.compile(r"\.{4,}")


def clean_text(text: str) -> str:
    t = _ARTEFACTS.sub(" ", text or "")
    t = re.sub(r"^\s*>>\s*", "", t)
    t = _FILLERS.sub(" ", t)
    t = _STUTTER.sub(lambda m: m.group(1), t)
    t = _REPEATED_DOTS.sub("...", t)
    t = _SPACE_BEFORE_PUNCT.sub(r"\1", t)
    t = _SPACES.sub(" ", t).strip(" ,;")
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    return t


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text or ""))


def clean_segments(segments: list) -> list:
    """Clean each segment's text; drop segments that are empty afterwards."""
    out = []
    for seg in segments:
        text = clean_text(seg.get("text", ""))
        if not re.search(r"\w", text):
            continue
        out.append({**seg, "text": text})
    return out


def merge_adjacent(segments: list, max_gap: float = 2.0, max_chars: int = 600) -> list:
    """Merge consecutive segments of the same speaker into readable paragraphs and number them 1..n."""
    merged: list = []
    for seg in sorted(segments, key=lambda s: (s["start_time"], s["end_time"])):
        prev = merged[-1] if merged else None
        if (
            prev
            and prev["speaker"] == seg["speaker"]
            and seg["start_time"] - prev["end_time"] <= max_gap
            and len(prev["text"]) + len(seg["text"]) < max_chars
        ):
            prev["text"] = f'{prev["text"]} {seg["text"]}'
            prev["end_time"] = max(prev["end_time"], seg["end_time"])
            prev["uncertain"] = prev["uncertain"] or seg.get("uncertain", False)
        else:
            merged.append({
                "speaker": seg["speaker"], "text": seg["text"],
                "start_time": seg["start_time"], "end_time": seg["end_time"],
                "uncertain": bool(seg.get("uncertain", False)),
            })
    for i, seg in enumerate(merged, start=1):
        seg["id"] = i
    return merged

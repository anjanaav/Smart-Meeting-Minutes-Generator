"""Minutes generator (Module 5).

Quality rules enforced here in code, not only in the prompt:
  * every decision / action item must cite transcript segment ids that really exist, otherwise it is dropped;
  * evidence text is copied from the transcript by id (the model cannot fabricate it);
  * an assignee is accepted only if it matches a participant or is named in the cited segments, else null;
  * a deadline is accepted only if its wording appears verbatim in the cited segments, else null;
  * long meetings are split into overlapping windows and merged, never truncated;
  * the final document is validated against the schema before it is returned.
"""
from __future__ import annotations

import json
import logging
import re
import socket
import time
import urllib.error
import urllib.request
from collections import Counter
from difflib import SequenceMatcher
from typing import Optional

from .schemas import LLM_EXTRACTION_SCHEMA, REDUCE_SCHEMA, SCHEMA_VERSION, validate_llm_extraction, validate_minutes

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """The AI service failed (network, auth, rate limit, refusal, truncation)."""


class LLMMalformed(LLMError):
    """The AI service answered, but not with output matching the schema."""


class MinutesValidationError(RuntimeError):
    def __init__(self, problems):
        super().__init__("; ".join(problems[:5]))
        self.problems = problems


# --------------------------------------------------------------------------- prompts
SYSTEM_EXTRACT = """You write factual meeting minutes from a speaker-attributed transcript.
Transcript lines look like: [12] 00:03:21 Alice: text   (the number in brackets is the segment id).

Rules:
1. Use ONLY what is in the transcript. Never invent statements, people, decisions, tasks, numbers, dates or deadlines.
2. A decision is something the participants explicitly agreed or confirmed ("we'll go with X", "agreed, let's do Y").
   Ideas that were only proposed, suggested, asked about or left open are NOT decisions: mention them in key_points
   and say they were proposed or undecided.
3. An action item is a specific task a person explicitly committed to, or was explicitly asked to do and accepted.
   General discussion, wishes and "maybe we should..." are not action items.
4. assignee: the participant who committed or was assigned, written as in the participant list or as named in the
   transcript. For a first-person commitment ("I will..."), use the speaker's name. If it is unclear, use null. Never guess.
5. deadline: copy the wording exactly as spoken (for example "by Friday"). If none was stated, use null. Never compute dates.
6. Every key point, decision and action item must cite the supporting segment ids in segment_ids. Cite only ids that exist.
7. Keep names, numbers, dates and technical terms exactly as written. Lines marked [unclear audio] may be mis-transcribed:
   do not base a decision or task on them alone.
8. Be concise and avoid redundancy. Write in the language of the transcript.
9. If there are no decisions or no action items, return empty arrays.
The summary is a short, neutral overview (a few sentences) of what was discussed and concluded."""

SYSTEM_REDUCE = """You combine partial summaries of consecutive parts of one meeting into a single concise, non-redundant
summary (a few sentences to two short paragraphs). Use only the material provided; do not add facts."""


# --------------------------------------------------------------------------- transcript helpers
def fmt_ts(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def format_line(seg: dict) -> str:
    flag = " [unclear audio]" if seg.get("uncertain") else ""
    return f'[{seg["id"]}] {fmt_ts(seg["start_time"])} {seg["speaker"]}{flag}: {seg["text"]}'


def format_transcript(segments: list) -> str:
    return "\n".join(format_line(s) for s in segments)


def split_windows(segments: list, max_chars: int, overlap: int) -> list:
    """Split into windows of at most ``max_chars`` (a segment is never cut), each starting ``overlap`` segments early."""
    if not segments:
        return []
    sizes = [len(format_line(s)) + 1 for s in segments]
    windows, start, n = [], 0, len(segments)
    while start < n:
        total, end = 0, start
        while end < n and (end == start or total + sizes[end] <= max_chars):
            total += sizes[end]
            end += 1
        windows.append(segments[start:end])
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return windows


# --------------------------------------------------------------------------- grounding checks
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


_UNASSIGNED = {"", "unassigned", "unknown", "none", "n/a", "na", "null", "everyone", "everybody", "team", "all", "we", "us", "someone", "somebody"}
_FIRST_PERSON = {"i", "me", "myself"}


def resolve_assignee(candidate: Optional[str], participants: list, evidence_text: str, speaker: Optional[str]) -> Optional[str]:
    """Accept an assignee only if it is supported by participant metadata or by the cited transcript text."""
    cand = _norm(candidate or "")
    if cand in _UNASSIGNED:
        return None
    if cand in _FIRST_PERSON:
        return speaker
    exact = [p for p in participants if _norm(p) == cand]
    if len(exact) == 1:
        return exact[0]
    cand_tokens = set(cand.split())
    partial = [p for p in participants if cand_tokens and cand_tokens <= set(_norm(p).split())]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        return None  # ambiguous: refuse to guess
    if re.search(rf"\b{re.escape(cand)}\b", evidence_text, re.IGNORECASE):
        return candidate.strip()  # a person named in the discussion who is not in the participant list
    return None


_DEADLINE_LEAD = re.compile(r"^(?:by|before|on|until|till|due|no later than)\s+", re.IGNORECASE)


def verify_deadline(deadline: Optional[str], evidence_text: str) -> Optional[str]:
    """A deadline must be quoted from the cited segments; otherwise it is treated as unspecified."""
    if not deadline:
        return None
    core = _DEADLINE_LEAD.sub("", deadline.strip()).strip(" .")
    if len(core) < 2:
        return None
    if re.search(re.escape(_norm(core)), _norm(evidence_text)):
        return deadline.strip()
    return None


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def sanitize_extraction(raw: dict, by_id: dict, participant_names: list, valid_ids: Optional[set] = None):
    """Turn raw model/rule output into grounded items. Returns (partial, warnings)."""
    valid = set(by_id) if valid_ids is None else valid_ids
    warnings: list = []

    def ids_of(item):
        return [i for i in dict.fromkeys(item.get("segment_ids", [])) if i in valid and i in by_id]

    def evidence_of(ids):
        text = " ".join(f'{by_id[i]["speaker"]}: {by_id[i]["text"]}' for i in ids)
        return text[:600]

    def plain_text(ids):
        return " ".join(by_id[i]["text"] for i in ids)

    key_points = []
    for kp in raw.get("key_points", []):
        topic, details = (kp.get("topic") or "").strip(), (kp.get("details") or "").strip()
        if topic and details:
            key_points.append({"topic": topic[:150], "details": details[:1200], "segment_ids": ids_of(kp)})

    decisions, dropped = [], 0
    for d in raw.get("decisions", []):
        ids, desc = ids_of(d), (d.get("description") or "").strip()
        if not desc or not ids:
            dropped += 1
            continue
        speakers = list(dict.fromkeys(by_id[i]["speaker"] for i in ids))
        speaker = resolve_assignee(d.get("speaker"), speakers, plain_text(ids), None) if d.get("speaker") else None
        decisions.append({
            "description": desc[:400], "speaker": speaker, "context": (d.get("context") or "").strip()[:600],
            "evidence": evidence_of(ids), "segment_ids": ids,
        })

    actions = []
    for a in raw.get("action_items", []):
        ids, task = ids_of(a), (a.get("task") or "").strip()
        if not task or not ids:
            dropped += 1
            continue
        text = plain_text(ids)
        actions.append({
            "task": task[:300],
            "assignee": resolve_assignee(a.get("assignee"), participant_names, text, by_id[ids[0]]["speaker"]),
            "deadline": verify_deadline(a.get("deadline"), text),
            "status": "pending", "evidence": evidence_of(ids), "segment_ids": ids,
        })
    if dropped:
        warnings.append(f"{dropped} extracted item(s) were discarded because they could not be tied to the transcript.")
    partial = {"summary": (raw.get("summary") or "").strip(), "key_points": key_points, "decisions": decisions, "action_items": actions}
    return partial, warnings


def merge_partials(partials: list) -> dict:
    """Combine per-window results, removing the duplicates caused by window overlap."""
    def dedupe(items, text_key, threshold=0.85):
        kept: list = []
        for it in items:
            dup = None
            for k in kept:
                shares = set(k["segment_ids"]) & set(it["segment_ids"])
                sim = _similar(k[text_key], it[text_key])
                if sim >= threshold or (shares and sim >= 0.6):
                    dup = k
                    break
            if dup is None:
                kept.append(dict(it))
            else:
                dup["segment_ids"] = sorted(set(dup["segment_ids"]) | set(it["segment_ids"]))
                if dup.get("assignee") is None and it.get("assignee"):
                    dup["assignee"] = it["assignee"]
                if dup.get("deadline") is None and it.get("deadline"):
                    dup["deadline"] = it["deadline"]
        kept.sort(key=lambda x: min(x["segment_ids"]) if x["segment_ids"] else 10**9)
        return kept

    return {
        "key_points": dedupe([k for p in partials for k in p["key_points"]], "topic"),
        "decisions": dedupe([d for p in partials for d in p["decisions"]], "description"),
        "action_items": dedupe([a for p in partials for a in p["action_items"]], "task"),
    }


# --------------------------------------------------------------------------- rule-based fallback
_STOP = set("""the and for that this with from have will would there their about could should been were which into them they then than also very
just your here when what where while because once after before our are was can you he she it its of in on at to as an or is be by so if not no
yes okay yeah like going think know want need make get let one thing things really actually""".split())
_WEEKDAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
_DEADLINE_RX = re.compile(
    rf"\b(?:(?:by|before|until|on|due)\s+)?(?:(?:next|this)\s+(?:week|month|{_WEEKDAYS})|tomorrow|tonight|today|"
    rf"end of (?:the )?(?:day|week|month|quarter|year)|(?:{_WEEKDAYS})|"
    rf"\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MONTHS})|(?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?)\b", re.IGNORECASE)
_HEDGE = re.compile(r"\b(?:should we|shall we|do we|can we|could we|maybe|perhaps|might|not sure|thinking about|wondering|what if|if we|unless)\b|\?", re.IGNORECASE)
_DECISION = re.compile(
    r"\bwe(?:'ve| have)?\s+(?:decided|agreed|concluded|settled)\b|\blet'?s go with\b|\bwe(?:'ll| will) go with\b|"
    r"\bthe decision is\b|\bdecision(?: is)? made\b|\b(?:it'?s|that'?s) (?:decided|agreed|settled)\b|\bwe(?:'re| are) going with\b", re.IGNORECASE)
_COMMIT_FIRST = re.compile(r"^(?:(?:ok(?:ay)?|sure|yes|yeah|alright)[,.]?\s+)?(?:i(?:'ll| will|'m going to| am going to| can take)|let me)\s+(?P<task>.+)$", re.IGNORECASE)
_COMMIT_NAMED = re.compile(r"^(?P<who>[A-Z][a-z]+)(?:\s+[A-Z][a-z]+)?\s+(?:will|is going to|'ll|has agreed to)\s+(?P<task>.+)$")
_SENT = re.compile(r"(?<=[.!?])\s+")


def _sentences(text: str) -> list:
    return [s.strip() for s in _SENT.split(text) if s.strip()]


def _keywords(text: str, n: int) -> list:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-']+", text.lower()) if w not in _STOP and len(w) > 3]
    return [w for w, _ in Counter(words).most_common(n)]


def _merge_confirmations(actions: list) -> list:
    """Merge "Meera will draft the release by Monday" with Meera's own "I'll share the draft on Monday" a moment later.

    Deliberately strict, because losing a real task is worse than listing one twice: same assignee, nearby lines, and at least
    three content words in common. The merged item cites both lines, so nothing is hidden."""
    merged: list = []
    for a in actions:
        twin = None
        for m in merged:
            near = abs(min(a["segment_ids"]) - max(m["segment_ids"])) <= 2
            if a["assignee"] and a["assignee"] == m["assignee"] and near and len(set(_keywords(a["task"], 12)) & set(_keywords(m["task"], 12))) >= 3:
                twin = m
                break
        if twin is None:
            merged.append(dict(a))
        else:
            twin["segment_ids"] = sorted(set(twin["segment_ids"]) | set(a["segment_ids"]))
            twin["deadline"] = twin["deadline"] or a["deadline"]
    return merged


def rule_based_extract(segments: list, participant_names: list) -> dict:
    """Conservative extraction from explicit cues only. Used when the AI service is unavailable."""
    first_names = {_norm(p).split()[0]: p for p in participant_names if p.strip()}
    decisions, actions = [], []
    for seg in segments:
        for sent in _sentences(seg["text"]):
            if _HEDGE.search(sent):
                continue
            if _DECISION.search(sent):
                decisions.append({"description": sent, "speaker": seg["speaker"], "context": "", "segment_ids": [seg["id"]]})
                continue
            m, assignee = _COMMIT_FIRST.match(sent), None
            if m:
                assignee = seg["speaker"]
            else:
                m = _COMMIT_NAMED.match(sent)
                if m and _norm(m.group("who")) in first_names:
                    assignee = first_names[_norm(m.group("who"))]
                else:
                    m = None
            if m:
                dl = _DEADLINE_RX.search(sent)
                actions.append({"task": sent, "assignee": assignee, "deadline": dl.group(0).strip() if dl else None, "segment_ids": [seg["id"]]})

    actions = _merge_confirmations(actions)

    # key points: one per block of consecutive segments, headed by its most frequent keywords
    key_points = []
    for i in range(0, len(segments), 8):
        block = segments[i:i + 8]
        text = " ".join(s["text"] for s in block)
        kws = _keywords(text, 3)
        sents = [(s, seg) for seg in block for s in _sentences(seg["text"]) if len(s.split()) >= 6]
        if not kws or not sents:
            continue
        best, seg = max(sents, key=lambda sp: sum(w in sp[0].lower() for w in kws))
        key_points.append({"topic": ", ".join(kws).title(), "details": f'{seg["speaker"]}: {best}', "segment_ids": [seg["id"]]})

    # extractive summary: highest-scoring sentences in their original order
    all_text = " ".join(s["text"] for s in segments)
    freq = Counter(w for w in re.findall(r"[a-z][a-z\-']+", all_text.lower()) if w not in _STOP and len(w) > 3)
    scored = []
    for idx, s in enumerate(x for seg in segments for x in _sentences(seg["text"])):
        words = re.findall(r"[a-z][a-z\-']+", s.lower())
        if len(words) >= 6 and not s.rstrip().endswith("?"):  # a question is not a conclusion
            scored.append((sum(freq[w] for w in words) / (len(words) ** 0.5), idx, s))
    top = sorted(sorted(scored, reverse=True)[:5], key=lambda t: t[1])
    summary = " ".join(s for _, _, s in top)
    return {"summary": summary, "key_points": key_points, "decisions": decisions, "action_items": actions}


# --------------------------------------------------------------------------- OpenAI provider (REST, stdlib only)
class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.openai.com/v1", timeout: int = 120, urlopen=None, sleep=time.sleep):
        self._key, self._model, self._url = api_key, model, base_url.rstrip("/") + "/chat/completions"
        self._timeout, self._urlopen, self._sleep = timeout, urlopen or urllib.request.urlopen, sleep

    def extract(self, system: str, user: str, schema: dict, schema_name: str) -> dict:
        body = json.dumps({
            "model": self._model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
        }).encode("utf-8")
        req = urllib.request.Request(self._url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._key}"})
        payload = None
        for attempt in range(3):  # retrying a stateless completion request is safe
            try:
                with self._urlopen(req, timeout=self._timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                try:
                    exc.close()  # release the connection; the body is never read or logged
                except Exception:
                    pass
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    self._sleep(2 ** attempt)
                    continue
                raise LLMError(f"AI service returned HTTP {exc.code}") from None  # body deliberately not logged
            except (urllib.error.URLError, socket.timeout, TimeoutError):
                if attempt < 2:
                    self._sleep(2 ** attempt)
                    continue
                raise LLMError("AI service unreachable") from None
        try:
            choice = payload["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMMalformed("unexpected response shape") from None
        if message.get("refusal"):
            raise LLMError("AI service refused the request")
        if choice.get("finish_reason") == "length":
            raise LLMError("AI response was truncated")
        try:
            return json.loads(message["content"])
        except (KeyError, TypeError, ValueError):
            raise LLMMalformed("response was not valid JSON") from None


def build_provider(cfg):
    if cfg.llm_provider == "openai" and cfg.openai_api_key:
        return OpenAIProvider(cfg.openai_api_key, cfg.openai_model, cfg.openai_base_url, cfg.llm_timeout_seconds)
    return None  # rule-based extraction only


# --------------------------------------------------------------------------- generator
def _validate_reduce(obj) -> list:
    return [] if isinstance(obj, dict) and isinstance(obj.get("summary"), str) else ["summary: must be a string"]


class MinutesGenerator:
    def __init__(self, provider=None, window_chars: int = 24000, overlap_segments: int = 6):
        self.provider = provider
        self.window_chars = window_chars
        self.overlap = overlap_segments

    # -- LLM path
    _REPAIR_NOTE = "\n\nYour previous answer did not match the required JSON schema. Return only valid JSON that matches it."

    def _call(self, system, user, schema, name, validate):
        """Call the model; retry once if the output is malformed. Raises LLMMalformed after the second failure."""
        for attempt in range(2):
            try:
                result = self.provider.extract(system, user + (self._REPAIR_NOTE if attempt else ""), schema, name)
            except LLMMalformed:
                if attempt:
                    raise
                continue
            if not validate(result):
                return result
        raise LLMMalformed("model output failed schema validation twice")

    def _with_llm(self, transcript, names, by_id, warnings):
        windows = split_windows(transcript, self.window_chars, self.overlap)
        partials, summaries = [], []
        for n, win in enumerate(windows, start=1):
            part_note = "" if len(windows) == 1 else (
                f"This is part {n} of {len(windows)} of a longer meeting; other parts are processed separately. "
                "The first lines may repeat the end of the previous part.\n")
            user = f"Participants: {', '.join(names) or 'unknown'}\n{part_note}\nTranscript:\n{format_transcript(win)}"
            raw = self._call(SYSTEM_EXTRACT, user, LLM_EXTRACTION_SCHEMA, "meeting_extraction", validate_llm_extraction)
            partial, w = sanitize_extraction(raw, by_id, names, {s["id"] for s in win})
            warnings.extend(w)
            partials.append(partial)
            summaries.append(partial["summary"])
        merged = merge_partials(partials)
        if len(windows) == 1:
            summary = summaries[0]
        else:
            topics = "; ".join(k["topic"] for k in merged["key_points"][:40])
            user = "Partial summaries in order:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(summaries, 1)) + f"\n\nKey topics: {topics}"
            try:
                summary = self._call(SYSTEM_REDUCE, user, REDUCE_SCHEMA, "meeting_summary", _validate_reduce)["summary"].strip()
            except (LLMError, KeyError):
                summary = " ".join(s for s in summaries if s)
                warnings.append("The overall summary was assembled from part summaries because the AI service failed on the final step.")
        return merged, summary

    # -- public
    def generate(self, meeting: dict, transcript: list, participants: list) -> dict:
        names = [p["name"] for p in participants]
        by_id = {s["id"]: s for s in transcript}
        warnings: list = []
        generated_by, merged, summary = "llm", None, ""
        if self.provider is not None:
            try:
                merged, summary = self._with_llm(transcript, names, by_id, warnings)
            except LLMError as exc:
                log.warning("AI service failed (%s); using rule-based extraction", type(exc).__name__)
                warnings.clear()
                warnings.append("The AI service was unavailable, so these minutes use rule-based extraction and may be less complete.")
                merged = None
        if merged is None:
            generated_by = "rule_based"
            if self.provider is None:
                warnings.append("No AI service is configured, so these minutes use rule-based extraction and may be less complete.")
            partial, w = sanitize_extraction(rule_based_extract(transcript, names), by_id, names)
            warnings.extend(w)
            merged, summary = partial, partial["summary"]

        doc = {
            "schema_version": SCHEMA_VERSION,
            "meeting_id": meeting["id"],
            "meeting_title": meeting.get("title") or meeting.get("room_name") or "Meeting",
            "meeting_date": meeting.get("started_at") or meeting.get("created_at"),
            "duration_seconds": meeting.get("duration_seconds") or (transcript[-1]["end_time"] if transcript else 0),
            "summary": summary,
            "key_points": merged["key_points"],
            "decisions": merged["decisions"],
            "action_items": merged["action_items"],
            "participants": [{"name": n} for n in names],
            "transcript": [{k: s[k] for k in ("id", "speaker", "text", "start_time", "end_time")} | {"uncertain": bool(s.get("uncertain"))} for s in transcript],
            "generated_by": generated_by,
            "warnings": warnings,
        }
        problems = validate_minutes(doc)
        if problems:
            raise MinutesValidationError(problems)
        return doc

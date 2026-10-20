import io
import json
import unittest
import urllib.error

from app.generator import (LLMError, LLMMalformed, MinutesGenerator, OpenAIProvider, resolve_assignee, rule_based_extract,
                           sanitize_extraction, split_windows, verify_deadline)
from app.schemas import validate_minutes
from tests.helpers import ScriptedProvider


def T(i, speaker, text, start=None, uncertain=False):
    start = i * 10 if start is None else start
    return {"id": i, "speaker": speaker, "text": text, "start_time": start, "end_time": start + 5, "uncertain": uncertain}


TRANSCRIPT = [
    T(1, "Alice", "We decided to ship version two on the fifteenth."),
    T(2, "Bob", "I will send the release notes by Friday."),
    T(3, "Alice", "Should we move to Postgres? Maybe we could consider it next quarter."),
    T(4, "Carol", "Dana will update the documentation."),
]
PEOPLE = ["Alice", "Bob", "Carol"]
MEETING = {"id": "m1", "title": "Release sync", "started_at": "2026-10-19T10:00:00+00:00", "created_at": "2026-10-19T09:59:00+00:00"}


class GroundingTests(unittest.TestCase):
    by_id = {t["id"]: t for t in TRANSCRIPT}

    def run_sanitize(self, **raw):
        base = {"summary": "s", "key_points": [], "decisions": [], "action_items": []}
        return sanitize_extraction({**base, **raw}, self.by_id, PEOPLE)

    def test_items_citing_nonexistent_segments_are_dropped(self):
        part, warnings = self.run_sanitize(
            decisions=[{"description": "Invented", "speaker": None, "context": "", "segment_ids": [99]}],
            action_items=[{"task": "Invented task", "assignee": "Bob", "deadline": None, "segment_ids": []}])
        self.assertEqual((part["decisions"], part["action_items"]), ([], []))
        self.assertTrue(warnings)

    def test_evidence_is_copied_from_transcript_not_from_model(self):
        part, _ = self.run_sanitize(decisions=[{"description": "Ship v2", "speaker": "Alice", "context": "", "segment_ids": [1]}])
        self.assertEqual(part["decisions"][0]["evidence"], "Alice: We decided to ship version two on the fifteenth.")

    def test_unsupported_assignee_becomes_unassigned(self):
        part, _ = self.run_sanitize(action_items=[{"task": "Do X", "assignee": "Zed", "deadline": None, "segment_ids": [2]}])
        self.assertIsNone(part["action_items"][0]["assignee"])

    def test_person_named_in_transcript_but_not_a_participant_is_accepted(self):
        part, _ = self.run_sanitize(action_items=[{"task": "Update docs", "assignee": "Dana", "deadline": None, "segment_ids": [4]}])
        self.assertEqual(part["action_items"][0]["assignee"], "Dana")

    def test_first_person_assignee_resolves_to_speaker(self):
        part, _ = self.run_sanitize(action_items=[{"task": "Send notes", "assignee": "I", "deadline": "by Friday", "segment_ids": [2]}])
        self.assertEqual(part["action_items"][0]["assignee"], "Bob")

    def test_invented_deadline_is_discarded_and_stated_one_kept(self):
        part, _ = self.run_sanitize(action_items=[
            {"task": "Send notes", "assignee": "Bob", "deadline": "next Tuesday", "segment_ids": [2]},
            {"task": "Send notes again", "assignee": "Bob", "deadline": "by Friday", "segment_ids": [2]}])
        self.assertEqual([a["deadline"] for a in part["action_items"]], [None, "by Friday"])

    def test_ambiguous_first_name_is_not_guessed(self):
        self.assertIsNone(resolve_assignee("Sam", ["Sam Lee", "Sam Cole"], "something", None))

    def test_team_or_everyone_is_unassigned(self):
        for who in ("everyone", "the team", "Team", None, ""):
            self.assertIsNone(resolve_assignee(who, PEOPLE, "", None))

    def test_deadline_verification(self):
        self.assertEqual(verify_deadline("by Friday", "send it Friday please"), "by Friday")
        self.assertIsNone(verify_deadline("tomorrow", "send it Friday"))
        self.assertIsNone(verify_deadline(None, "x"))


class RuleBasedTests(unittest.TestCase):
    def test_extracts_only_explicit_cues(self):
        out = rule_based_extract(TRANSCRIPT, PEOPLE)
        self.assertEqual([d["segment_ids"] for d in out["decisions"]], [[1]])            # the "Should we...?" proposal is excluded
        tasks = {a["segment_ids"][0]: a for a in out["action_items"]}
        self.assertEqual((tasks[2]["assignee"], tasks[2]["deadline"]), ("Bob", "by Friday"))
        self.assertNotIn(4, tasks)                                                       # Dana is not a participant: not guessed

    def test_named_participant_commitment(self):
        out = rule_based_extract([T(1, "Alice", "Carol will review the contract tomorrow.")], PEOPLE)
        self.assertEqual((out["action_items"][0]["assignee"], out["action_items"][0]["deadline"]), ("Carol", "tomorrow"))

    def test_general_discussion_is_not_an_action_item(self):
        out = rule_based_extract([T(1, "Alice", "We should probably look at the logs sometime."), T(2, "Bob", "Someone needs to fix that.")], PEOPLE)
        self.assertEqual(out["action_items"], [])


class GeneratorTests(unittest.TestCase):
    GOOD = {"summary": "Overview.", "key_points": [{"topic": "Release", "details": "Plan.", "segment_ids": [1]}],
            "decisions": [{"description": "Ship v2", "speaker": "Alice", "context": "", "segment_ids": [1]}],
            "action_items": [{"task": "Send notes", "assignee": "Bob", "deadline": "by Friday", "segment_ids": [2]}]}

    def gen(self, provider, **kw):
        return MinutesGenerator(provider, **kw).generate(MEETING, TRANSCRIPT, [{"name": n} for n in PEOPLE])

    def test_llm_path_produces_valid_grounded_document(self):
        doc = self.gen(ScriptedProvider(self.GOOD))
        self.assertEqual(validate_minutes(doc), [])
        self.assertEqual(doc["generated_by"], "llm")
        self.assertEqual(doc["action_items"][0]["status"], "pending")
        self.assertEqual(doc["meeting_title"], "Release sync")

    def test_no_provider_uses_labelled_rule_based_fallback(self):
        doc = self.gen(None)
        self.assertEqual(doc["generated_by"], "rule_based")
        self.assertTrue(any("rule-based" in w for w in doc["warnings"]))
        self.assertEqual(validate_minutes(doc), [])

    def test_service_failure_falls_back_without_crashing(self):
        doc = self.gen(ScriptedProvider(LLMError("down")))
        self.assertEqual(doc["generated_by"], "rule_based")
        self.assertTrue(any("unavailable" in w for w in doc["warnings"]))

    def test_malformed_then_valid_is_retried_once(self):
        provider = ScriptedProvider({"summary": 5}, self.GOOD)
        doc = self.gen(provider)
        self.assertEqual(doc["generated_by"], "llm")
        self.assertEqual(len(provider.calls), 2)
        self.assertIn("did not match", provider.calls[1]["user"])

    def test_malformed_twice_falls_back(self):
        doc = self.gen(ScriptedProvider({"summary": 5}))
        self.assertEqual(doc["generated_by"], "rule_based")

    def test_uncertain_audio_is_marked_for_the_model_and_kept_in_transcript(self):
        t = [T(1, "Alice", "We will probably ship friday", uncertain=True)]
        provider = ScriptedProvider(self.GOOD)
        doc = MinutesGenerator(provider).generate(MEETING, t, [{"name": "Alice"}])
        self.assertIn("[unclear audio]", provider.calls[0]["user"])
        self.assertTrue(doc["transcript"][0]["uncertain"])


class LongMeetingTests(unittest.TestCase):
    def long_transcript(self, n=60):
        return [T(i, "Alice" if i % 2 else "Bob", f"This is discussion line number {i} about item {i}.", start=i * 10) for i in range(1, n + 1)]

    def test_windows_cover_every_segment_and_overlap(self):
        segs = self.long_transcript()
        windows = split_windows(segs, max_chars=600, overlap=3)
        self.assertGreater(len(windows), 2)
        covered = {s["id"] for w in windows for s in w}
        self.assertEqual(covered, {s["id"] for s in segs})                 # nothing silently truncated
        for a, b in zip(windows, windows[1:]):
            self.assertTrue({s["id"] for s in a} & {s["id"] for s in b})   # consecutive windows overlap
        self.assertTrue(all(sum(len(s["text"]) for s in w) < 900 for w in windows))

    def test_oversized_single_segment_still_progresses(self):
        windows = split_windows([T(1, "A", "x" * 5000), T(2, "B", "short")], max_chars=100, overlap=2)
        self.assertEqual({s["id"] for w in windows for s in w}, {1, 2})

    def test_multi_window_results_are_merged_without_duplicates(self):
        segs = self.long_transcript()
        dup_decision = {"description": "Adopt the new process", "speaker": None, "context": "", "segment_ids": [10]}
        w1 = {"summary": "Part one.", "key_points": [], "decisions": [dup_decision], "action_items": []}
        w2 = {"summary": "Part two.", "key_points": [], "decisions": [dict(dup_decision)], "action_items": []}  # repeated via overlap
        reduce = {"summary": "Whole meeting."}
        provider = ScriptedProvider(w1, w2, w1, w2, reduce)
        gen = MinutesGenerator(provider, window_chars=900, overlap_segments=4)
        windows = split_windows(segs, 900, 4)
        provider.responses = [w1 if i % 2 == 0 else w2 for i in range(len(windows))] + [reduce]
        doc = gen.generate(MEETING, segs, [{"name": "Alice"}, {"name": "Bob"}])
        self.assertEqual(len(doc["decisions"]), 1)
        self.assertEqual(doc["summary"], "Whole meeting.")
        self.assertEqual(provider.calls[-1]["name"], "meeting_summary")
        self.assertEqual(len(provider.calls), len(windows) + 1)
        self.assertEqual(len(doc["transcript"]), 60)


class FakeResp:
    def __init__(self, payload): self._b = json.dumps(payload).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def ok_payload(content):
    return {"choices": [{"message": {"content": json.dumps(content)}, "finish_reason": "stop"}]}


class OpenAIProviderTests(unittest.TestCase):
    def provider(self, responses, sleeps=None):
        calls = []
        sleeps = [] if sleeps is None else sleeps

        def urlopen(req, timeout):
            calls.append(req)
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return FakeResp(r)
        p = OpenAIProvider("sk-test-secret", "gpt-test", urlopen=urlopen, sleep=sleeps.append)
        return p, calls

    def http_error(self, code):
        return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b'{"echo":"sensitive transcript"}'))

    def test_request_uses_bearer_key_and_strict_json_schema(self):
        p, calls = self.provider([ok_payload({"summary": "x"})])
        p.extract("sys", "usr", {"type": "object"}, "name")
        req = calls[0]
        self.assertEqual(req.get_header("Authorization"), "Bearer sk-test-secret")
        body = json.loads(req.data)
        self.assertEqual(body["response_format"]["json_schema"]["strict"], True)
        self.assertEqual(body["model"], "gpt-test")

    def test_rate_limit_is_retried_then_succeeds(self):
        sleeps = []
        p, calls = self.provider([self.http_error(429), self.http_error(503), ok_payload({"summary": "x"})], sleeps)
        self.assertEqual(p.extract("s", "u", {}, "n"), {"summary": "x"})
        self.assertEqual((len(calls), len(sleeps)), (3, 2))

    def test_auth_error_is_not_retried_and_message_hides_body_and_key(self):
        p, calls = self.provider([self.http_error(401)])
        with self.assertRaises(LLMError) as cm:
            p.extract("s", "u", {}, "n")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("sk-test-secret", str(cm.exception))
        self.assertNotIn("sensitive", str(cm.exception))

    def test_truncated_refused_and_non_json_responses(self):
        for payload, exc in [
            ({"choices": [{"message": {"content": "{}"}, "finish_reason": "length"}]}, LLMError),
            ({"choices": [{"message": {"content": None, "refusal": "no"}, "finish_reason": "stop"}]}, LLMError),
            ({"choices": [{"message": {"content": "not json"}, "finish_reason": "stop"}]}, LLMMalformed),
            ({"weird": True}, LLMMalformed),
        ]:
            p, _ = self.provider([payload])
            with self.assertRaises(exc):
                p.extract("s", "u", {}, "n")

    def test_network_failure_gives_llm_error(self):
        p, _ = self.provider([urllib.error.URLError("down")] * 3)
        with self.assertRaises(LLMError):
            p.extract("s", "u", {}, "n")


if __name__ == "__main__":
    unittest.main()

"""Quality of the rule-based fallback on a realistic, messy meeting. It must stay conservative: a missed item is acceptable,
an invented one is not (see the minutes quality rules in the project brief)."""
import unittest

from app.generator import MinutesGenerator, rule_based_extract

LINES = [
    ("Priya", "Okay, let's get started. The main thing today is the Q3 launch date and the pricing page."),
    ("Rahul", "I think we should maybe move the launch to the 20th, but I'm not sure the backend will be ready."),
    ("Priya", "Can we do the 15th instead? Anil, what's the status on the payment integration?"),
    ("Anil", "Payment integration is about 80 percent done. I will finish it by Thursday."),
    ("Rahul", "Okay then we'll go with the 15th for the launch."),
    ("Priya", "Great. Decision made, the launch is on the 15th. Rahul, can you update the pricing page?"),
    ("Rahul", "Sure, I'll update the pricing page before the launch."),
    ("Meera", "We should probably also think about the press release at some point."),
    ("Priya", "Right. Meera will draft the press release and share it next Monday."),
    ("Meera", "Yes, I'll share the draft with everyone on Monday."),
    ("Anil", "Also the budget is 4,500 dollars for ads, and nobody objected to that."),
    ("Priya", "Alright, that's all. Thanks everyone."),
]
PEOPLE = ["Priya", "Rahul", "Anil", "Meera"]


def segments(lines=LINES):
    return [{"id": i + 1, "speaker": s, "text": t, "start_time": i * 20.0, "end_time": i * 20.0 + 15, "uncertain": False} for i, (s, t) in enumerate(lines)]


def generate(lines=LINES):
    return MinutesGenerator(None).generate({"id": "m", "title": "Launch sync", "started_at": "2026-10-19T10:00:00+00:00"}, segments(lines),
                                           [{"name": n} for n in PEOPLE])


class RuleBasedQualityTests(unittest.TestCase):
    def setUp(self):
        self.doc = generate()
        self.decisions = [d["description"] for d in self.doc["decisions"]]
        self.actions = {a["task"]: a for a in self.doc["action_items"]}

    def test_confirmed_decisions_are_found_and_proposals_are_not(self):
        joined = " ".join(self.decisions)
        self.assertIn("go with the 15th", joined)
        self.assertIn("Decision made, the launch is on the 15th", joined)
        for hedged in ("maybe move the launch to the 20th", "Can we do the 15th instead", "press release at some point", "nobody objected"):
            self.assertNotIn(hedged, joined)

    def test_hedged_and_question_lines_are_never_action_items(self):
        text = " ".join(self.actions)
        for hedged in ("probably also think", "can you update", "maybe move"):
            self.assertNotIn(hedged, text)

    def test_owners_and_deadlines_are_only_those_stated(self):
        by_who = {a["assignee"]: a for a in self.doc["action_items"]}
        self.assertEqual(by_who["Anil"]["deadline"], "by Thursday")
        self.assertIsNone(by_who["Rahul"]["deadline"])                     # "before the launch" is not a date: left unspecified
        self.assertEqual(by_who["Meera"]["deadline"], "next Monday")
        self.assertEqual(set(by_who), {"Anil", "Rahul", "Meera"})

    def test_a_commitment_and_its_confirmation_are_one_item_citing_both_lines(self):
        meera = [a for a in self.doc["action_items"] if a["assignee"] == "Meera"]
        self.assertEqual(len(meera), 1)
        self.assertEqual(meera[0]["segment_ids"], [9, 10])
        self.assertIn("Meera: Yes, I'll share the draft", meera[0]["evidence"])

    def test_two_different_tasks_by_the_same_person_are_not_merged(self):
        lines = [("Rahul", "I will update the pricing page tomorrow."), ("Rahul", "I will also update the pricing copy on Friday.")]
        doc = generate(lines)
        self.assertEqual(len(doc["action_items"]), 2)                      # only two shared words: stays separate

    def test_the_summary_contains_no_questions(self):
        for sentence in self.doc["summary"].replace("? ", "?\n").splitlines():
            self.assertFalse(sentence.strip().endswith("?"), sentence)
        self.assertTrue(self.doc["summary"])

    def test_nothing_is_invented(self):
        transcript = " ".join(t for _, t in LINES).lower()
        for item in self.doc["decisions"] + self.doc["action_items"]:
            self.assertTrue(item["evidence"], item)
            self.assertTrue(item["segment_ids"])
        for a in self.doc["action_items"]:
            if a["deadline"]:
                self.assertIn(a["deadline"].lower().split(" ", 1)[-1], transcript)

    def test_extraction_on_a_transcript_without_cues_is_empty_not_made_up(self):
        doc = generate([("Priya", "We looked at the dashboard and talked about the new colours for a while."), ("Rahul", "It was a long discussion with many opinions.")])
        self.assertEqual((doc["decisions"], doc["action_items"]), ([], []))


if __name__ == "__main__":
    unittest.main()

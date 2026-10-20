import unittest

from app.nlp import clean_segments, clean_text, merge_adjacent


class CleanTextTests(unittest.TestCase):
    def test_removes_fillers_and_artifacts_but_keeps_content(self):
        self.assertEqual(clean_text("um, we uh need to ship [BLANK_AUDIO] on March 15th"), "We need to ship on March 15th")

    def test_collapses_function_word_stutter_only(self):
        self.assertEqual(clean_text("the the budget is 4,500 dollars"), "The budget is 4,500 dollars")
        self.assertEqual(clean_text("I had had enough"), "I had had enough")  # legitimate repetition preserved

    def test_numbers_names_and_case_preserved(self):
        self.assertEqual(clean_text("Priya will email Acme Corp about invoice INV-2041"), "Priya will email Acme Corp about invoice INV-2041")

    def test_empty_after_cleaning_is_dropped(self):
        self.assertEqual(clean_segments([{"text": "[MUSIC]"}, {"text": "um"}, {"text": "Hello"}]), [{"text": "Hello"}])


class MergeTests(unittest.TestCase):
    def s(self, sp, a, b, t, u=False):
        return {"speaker": sp, "start_time": a, "end_time": b, "text": t, "uncertain": u}

    def test_merges_same_speaker_and_numbers_segments(self):
        out = merge_adjacent([self.s("A", 0, 2, "Hello."), self.s("A", 2.5, 4, "How are you?"), self.s("B", 4.2, 5, "Fine.")])
        self.assertEqual([(o["id"], o["speaker"], o["text"]) for o in out], [(1, "A", "Hello. How are you?"), (2, "B", "Fine.")])
        self.assertEqual((out[0]["start_time"], out[0]["end_time"]), (0, 4))

    def test_does_not_merge_across_long_gap_and_keeps_uncertain(self):
        out = merge_adjacent([self.s("A", 0, 2, "One.", True), self.s("A", 30, 32, "Two.")])
        self.assertEqual(len(out), 2)
        self.assertTrue(out[0]["uncertain"])

    def test_orders_by_time_across_speakers(self):
        out = merge_adjacent([self.s("B", 10, 11, "Later."), self.s("A", 1, 2, "Earlier.")])
        self.assertEqual([o["speaker"] for o in out], ["A", "B"])


if __name__ == "__main__":
    unittest.main()

"""serve/test_class_profile.py: unit tests for the in-memory traffic-class profile.

Run: .venv/bin/python -m unittest serve.test_class_profile -v
"""
import unittest

from serve.class_profile import ClassProfile, _looks_noise, fingerprint


def fake_decode(ids):
    # ids encode text: 1..9 are letters a-i, 10 is a space, 100+ are digits ("2026" tokens)
    out = []
    for t in ids:
        if 1 <= t <= 9:
            out.append(chr(ord("a") + t - 1))
        elif t == 10:
            out.append(" ")
        else:
            out.append(str(t - 100))
    return "".join(out)


class Numeric(unittest.TestCase):
    def test_numbers_and_separators_mask(self):
        for s in ("2026", "11", "-", ": 03", "1.44", "12-31"):
            self.assertTrue(_looks_noise(s), s)

    def test_words_do_not(self):
        for s in ("system", "q", "you", "", "5th"):
            self.assertFalse(_looks_noise(s), s)


class Families(unittest.TestCase):
    def setUp(self):
        self.p = ClassProfile(decode=fake_decode, cap=8)

    def record(self, ids, reused, total):
        self.p.record(ids, reused, total or len(ids))

    def test_stable_heads_group(self):
        head = [1, 10, 2, 10, 3, 10, 4, 10, 5, 10, 6, 10, 7, 10, 8, 10,
                1, 10, 2, 10, 3, 10, 4, 10, 5, 10, 6, 10, 7, 10, 8]
        self.record(head, 0, 1000)
        self.record(head, 900, 1000)
        self.assertEqual(len(self.p.families), 1)
        row = self.p.snapshot()[0]
        self.assertEqual(row["n"], 2)
        self.assertGreater(row["reuse_ewm"], 0.1)   # EWMA moved toward 0.9, starts at 0

    def test_numeric_drift_stays_one_family(self):
        base = [1, 10, 2, 10, 3, 10, 4, 10, 5, 10, 6, 10, 7, 10, 8, 10,
                1, 10, 2, 10, 3, 10, 4, 10, 5, 10, 6, 10, 7, 10, 8]
        monday = list(base)
        monday[3] = 150 + 11          # "date" tokens in the head: masked to wildcards
        tuesday = list(base)
        tuesday[3] = 150 + 12
        self.record(base, 0, 900)
        self.record(monday, 800, 900)
        self.record(tuesday, 850, 900)
        self.assertEqual(len(self.p.families), 1, "date drift must not fork the family")

    def test_different_heads_split(self):
        a = [1, 10, 2, 10, 3, 10, 4, 10, 5, 10, 6, 10, 7, 10, 8, 10, 1, 10, 2, 10,
             3, 10, 4, 10, 5, 10, 6, 10, 7, 10, 8, 10, 1, 10, 2, 10, 3, 10, 4, 10, 5]
        b = [9, 10, 8, 10, 7, 10, 6, 10, 5, 10, 4, 10, 3, 10, 2, 10, 9, 10, 8, 10,
             7, 10, 6, 10, 5, 10, 4, 10, 3, 10, 2, 10, 9, 10, 8, 10, 7, 10, 6, 10, 5]
        self.record(a, 0, 500)
        self.record(b, 0, 500)
        self.assertEqual(len(self.p.families), 2)

    def test_short_prompts_under_min_lcp_get_own_rows(self):
        self.record([1, 10, 2, 10, 3], 0, 3)
        self.record([1, 10, 2, 10, 4], 3, 5)
        self.assertEqual(len(self.p.families), 2, "heads shorter than MIN_LCP never group")

    def test_reused_none_counts_as_zero(self):
        head = [1, 10, 2, 10, 3, 10, 4, 10, 5, 10, 6, 10, 7, 10, 8, 10, 1, 10, 2, 10,
                3, 10, 4, 10, 5, 10, 6, 10, 7, 10, 8]
        self.record(head, None, 800)
        self.record(head, None, 800)
        self.assertEqual(self.p.snapshot()[0]["reuse_ewm"], 0.0)

    def test_cap_evicts_idle_lowreuse_first(self):
        p = ClassProfile(decode=fake_decode, cap=8)
        hot = [1, 10, 2, 10, 3, 10, 4, 10, 5, 10, 6, 10, 7, 10, 8, 10, 1, 10, 2, 10,
               3, 10, 4, 10, 5, 10, 6, 10, 7, 10, 8]
        p.record(hot, 990, 1000)          # hot family: reused
        for i in range(1, 9):            # nine cold one-shot families
            cold = [i % 9 + 1, 10, 9 - (i % 9), 10, i % 7 + 1, 10, (i * 3) % 9 + 1, 10,
                    (i * 5) % 9 + 1, 10, i % 8 + 1, 10, (i * 7) % 9 + 1, 10, i % 6 + 1, 10,
                    (i * 2) % 9 + 1, 10, i % 4 + 1, 10, (i * 6) % 9 + 1, 10, i % 9 + 1, 10,
                    (i * 4) % 9 + 1, 10, i % 5 + 1, 10, (i * 8) % 9 + 1, 10, i % 3 + 1]
            p.record(cold, 0, 400)
        self.assertLessEqual(len(p.families), 8)
        self.assertTrue(any(f.reuse_ewm > 0.5 for f in p.families), "the hot family survives eviction")

    def test_snapshot_shape(self):
        head = [1, 10, 2, 10, 3, 10, 4, 10, 5, 10, 6, 10, 7, 10, 8, 10, 1, 10, 2, 10,
                3, 10, 4, 10, 5, 10, 6, 10, 7, 10, 8]
        self.record(head, 400, 1000)
        row = self.p.snapshot()[0]
        for key in ("head", "n", "reuse_ewm", "avg_prompt", "idle_s", "score"):
            self.assertIn(key, row)

    def test_empty_prompt_ignored(self):
        self.p.record([], 0, 0)
        self.p.record(None, 0, 10)
        self.assertEqual(self.p.families, [])


class Priority(unittest.TestCase):
    HEAD = [1, 10, 2, 10, 3, 10, 4, 10, 5, 10, 6, 10, 7, 10, 8, 10, 1, 10, 2, 10,
            3, 10, 4, 10, 5, 10, 6, 10, 7, 10, 8]

    def setUp(self):
        self.p = ClassProfile(decode=fake_decode, cap=8)

    def test_cold_table_is_never_dead(self):
        self.assertEqual(self.p.priority(self.HEAD), 1)
        for _ in range(4):                        # four one-shots: not enough evidence yet
            self.p.record(self.HEAD, 0, 900)
            self.assertEqual(self.p.priority(self.HEAD), 1)

    def test_five_zero_reuse_visits_declare_dead(self):
        for _ in range(5):
            self.p.record(self.HEAD, 0, 900)
        self.assertEqual(self.p.priority(self.HEAD), 0)

    def test_proven_reuse_is_warm(self):
        self.p.record(self.HEAD, 0, 900)          # first visit, nothing reused
        for _ in range(6):
            self.p.record(self.HEAD, 880, 900)    # ~0.98 reused every time
        self.assertEqual(self.p.priority(self.HEAD), 2)

    def test_unknown_head_stays_neutral(self):
        self.assertEqual(self.p.priority([9, 10, 8, 10, 7]), 1)


class Fingerprint(unittest.TestCase):
    def test_numeric_tokens_become_wildcards(self):
        fp = fingerprint([1, 150 + 2026, 2], fake_decode)
        self.assertEqual(fp, [1, 0, 2])


if __name__ == "__main__":
    unittest.main()

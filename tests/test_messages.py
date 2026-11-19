import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import dual
import job_picker
import messages as M
import device_tools as T
import verify as V
import ngpcl


class FieldNamesTest(unittest.TestCase):
    def test_numbering_follows_order(self):
        self.assertEqual(M.build_fields(["A", "B", "C"]),
                         [("text1", "A"), ("text2", "B"), ("text3", "C")])
        self.assertEqual(M.build_fields([]), [])

    def test_friendly_names(self):
        self.assertEqual(M.friendly("text1"), "메세지 1")
        self.assertEqual(M.friendly("text12"), "메세지 12")
        self.assertEqual(M.friendly("textual"), "textual")     # 약속 밖 이름은 그대로
        self.assertEqual(M.friendly("darkness"), "darkness")

    def test_leading_zeros_and_order_are_preserved_in_message(self):
        req = dual.Request("update", "J1", "0", M.build_fields(["00123", "2026-11-17"]), True)
        self.assertEqual(req.message(), "~JU1|J1|0|text1|00123|text2|2026-11-17|")

    def test_dual_compare_uses_position(self):
        a = dual.Request("update", "J1", "0", M.build_fields(["A", "B"]), True)
        same = dual.Request("update", "J1", "0", M.build_fields(["A", "B"]), True)
        swapped = dual.Request("update", "J1", "0", M.build_fields(["B", "A"]), True)
        other_job = dual.Request("update", "J2", "0", M.build_fields(["A", "B"]), True)
        self.assertEqual(a.canonical(), same.canonical())
        self.assertNotEqual(a.canonical(), swapped.canonical())      # 순서가 다르면 불일치
        self.assertNotEqual(a.canonical(), other_job.canonical())    # 작업이 다르면 불일치


class JobPickerTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.environ["MARKEM_DEV"] = "1"
        os.environ["MARKEM_DATA_DIR"] = self.d

    def tearDown(self):
        os.environ.pop("MARKEM_DATA_DIR", None)
        os.environ.pop("MARKEM_DEV", None)

    def test_recent_jobs_remembered_without_duplicates(self):
        job_picker.remember_job("A")
        job_picker.remember_job("B")
        job_picker.remember_job("A")
        job_picker.remember_job("  ")
        self.assertEqual(job_picker.recent_jobs(), ["A", "B"])

    def test_recent_jobs_capped(self):
        for i in range(30):
            job_picker.remember_job("J%d" % i)
        self.assertEqual(len(job_picker.recent_jobs()), job_picker.MAX_RECENT)
        self.assertEqual(job_picker.recent_jobs()[0], "J29")

    def test_read_current_job(self):
        self.assertEqual(job_picker.read_current_job(lambda m, t: "~JN0|J1|"), "J1")
        self.assertEqual(job_picker.read_current_job(lambda m, t: "~JN1|"), "")
        self.assertEqual(job_picker.read_current_job(lambda m, t: "~JNJ2|", False), "J2")
        with self.assertRaises(ngpcl.NgpclError):
            job_picker.read_current_job(lambda m, t: "~NK1|")


def ds(producing, state):
    return "~DS0|%d|0|0|1|2|0|000000000|%s|0|" % (producing, state)


class StopPrintingTest(unittest.TestCase):
    def run_seq(self, replies):
        seq = iter(replies)
        sent = []

        def xchg(msg, timeout):
            sent.append(msg)
            return next(seq)
        return T.stop_printing(xchg, True, tries=3, sleep=lambda s: None), sent

    def test_stops_and_confirms(self):
        (ok, note, light), sent = self.run_seq([ds(1, "06"), "~ST0|", ds(0, "11")])
        self.assertEqual((ok, light), (True, V.GREEN))
        self.assertEqual(sent, ["~DR|", "~ST|11|", "~DR|"])

    def test_already_held_sends_nothing(self):
        (ok, note, light), sent = self.run_seq([ds(0, "11")])
        self.assertTrue(ok)
        self.assertEqual(sent, ["~DR|"])

    def test_rejected_is_red(self):
        (ok, note, light), sent = self.run_seq([ds(1, "06"), "~ST1|"])
        self.assertEqual((ok, light), (False, V.RED))

    def test_never_stops_is_yellow(self):
        (ok, note, light), sent = self.run_seq([ds(1, "06"), "~ST0|", ds(1, "06"), ds(1, "06"), ds(1, "06")])
        self.assertEqual((ok, light), (False, V.YELLOW))


class ExtraFieldsTest(unittest.TestCase):
    def test_unsent_text_fields_are_flagged_yellow(self):
        rows = [("text1", "A"), ("text2", "B"), ("text3", "OLD"), ("other", "x")]
        cs = V.extra_field_checks(rows, 2)
        self.assertEqual([c.name for c in cs], ["text3"])
        self.assertEqual(cs[0].light, V.YELLOW)
        self.assertEqual(cs[0].got, "OLD")

    def test_nothing_extra_when_all_sent(self):
        self.assertEqual(V.extra_field_checks([("text1", "A"), ("text2", "B")], 2), [])
        self.assertEqual(V.extra_field_checks([], 3), [])
        self.assertEqual(V.extra_field_checks([("textual", "x"), ("text", "y")], 1), [])

    def test_extra_makes_overall_yellow(self):
        checks = [V.Check("text1", "A", "A", V.GREEN, "")] + V.extra_field_checks([("text2", "OLD")], 1)
        self.assertEqual(V.overall(checks), V.YELLOW)


if __name__ == "__main__":
    unittest.main()

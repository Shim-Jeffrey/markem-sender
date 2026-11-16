import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import dual as D
import ngpcl
import store as S


def U(i, role):
    return S.User(i, "u%d" % i, "이름%d" % i, role, False)


def R(job="J1", fields=(("LOT", "L1"), ("EXP", "20281031")), kind="update", alloc="0"):
    return D.Request(kind, job, alloc, list(fields))


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


class DualTest(unittest.TestCase):
    def setUp(self):
        self.clk = Clock()
        self.d = D.DualEntry(clock=self.clk)
        self.w, self.m = U(1, "worker"), U(2, "manager")

    def test_match_gives_one_time_approval(self):
        self.d.submit(self.w, R())
        r = self.d.verify(self.m, R(fields=(("EXP", "20281031"), ("LOT", "L1"))))  # 필드 순서 무관
        self.assertEqual(r.status, "match")
        self.assertFalse(self.d.has_pending())
        req = r.approval.consume()
        self.assertEqual(req.job, "J1")
        with self.assertRaises(D.DualError):
            r.approval.consume()

    def test_three_wrong_resets_worker_input(self):
        self.d.submit(self.w, R())
        r1 = self.d.verify(self.m, R(fields=(("LOT", "XX"), ("EXP", "20281031"))))
        self.assertEqual((r1.status, r1.tries, r1.left), ("retry", 1, 2))
        self.assertEqual(r1.fields, ["필드 LOT"])
        r2 = self.d.verify(self.m, R(job="J2"))
        self.assertEqual((r2.status, r2.left), ("retry", 1))
        self.assertTrue(self.d.has_pending())            # 1차 입력은 유지
        r3 = self.d.verify(self.m, R(job="J3"))
        self.assertEqual((r3.status, r3.tries), ("reset", 3))
        self.assertFalse(self.d.has_pending())           # 3회째에 1차 입력 폐기
        with self.assertRaises(D.DualError):
            self.d.verify(self.m, R())                   # 더 이상 확인할 입력이 없다

    def test_two_wrong_then_right_matches(self):
        self.d.submit(self.w, R())
        self.d.verify(self.m, R(job="A"))
        self.d.verify(self.m, R(job="B"))
        self.assertEqual(self.d.verify(self.m, R()).status, "match")

    def test_value_difference_by_one_char_or_space(self):
        self.d.submit(self.w, R())
        self.assertEqual(self.d.verify(self.m, R(fields=(("LOT", "L1 "), ("EXP", "20281031")))
                                       ).status, "retry")
        self.assertEqual(self.d.verify(self.m, R(fields=(("LOT", "l1"), ("EXP", "20281031")))
                                       ).status, "retry")

    def test_kind_and_extra_field_mismatch(self):
        self.d.submit(self.w, R())
        r = self.d.verify(self.m, R(kind="update_print"))
        self.assertEqual((r.status, r.fields), ("retry", ["작업 종류"]))
        r = self.d.verify(self.m, R(fields=(("LOT", "L1"), ("EXP", "20281031"), ("X", "1"))))
        self.assertEqual(r.fields, ["필드 X"])

    def test_expiry(self):
        self.d.submit(self.w, R())
        self.assertEqual(self.d.seconds_left(), 600)
        self.clk.t += 601
        self.assertTrue(self.d.expired())
        self.assertEqual(self.d.verify(self.m, R()).status, "expired")
        self.assertFalse(self.d.has_pending())

    def test_same_account_and_wrong_roles_rejected(self):
        self.d.submit(self.w, R())
        with self.assertRaises(D.DualError):
            self.d.verify(U(1, "manager"), R())          # 같은 계정 번호
        with self.assertRaises(D.DualError):
            self.d.verify(U(3, "worker"), R())
        with self.assertRaises(D.DualError):
            self.d.submit(self.m, R())                    # 관리자는 1차 입력 불가
        self.assertTrue(self.d.has_pending())             # 잘못된 시도는 횟수에 안 센다
        self.assertEqual(self.d.tries(), 0)

    def test_master_can_confirm_but_not_submit(self):
        self.d.submit(self.w, R())
        self.assertEqual(self.d.verify(U(5, "master"), R()).status, "match")
        with self.assertRaises(D.DualError):
            self.d.submit(U(5, "master"), R())            # 1차는 작업자만

    def test_new_submit_replaces_and_reports_old(self):
        self.d.submit(self.w, R(job="OLD"))
        old = self.d.submit(self.w, R(job="NEW"))
        self.assertEqual(old["request"].job, "OLD")
        self.assertEqual(self.d.verify(self.m, R(job="NEW")).status, "match")

    def test_request_validation(self):
        with self.assertRaises(D.DualError):
            D.Request("update", "J", "0", [])                       # 갱신인데 필드 없음
        with self.assertRaises(D.DualError):
            D.Request("update", "J", "0", [("A", "1"), ("A", "2")])  # 중복 필드
        with self.assertRaises(D.DualError):
            D.Request("update", "J", "0", [("A", "a|b")])           # 구분자
        D.Request("select", "J", "", [])                            # 선택은 필드 없어도 됨

    def test_approval_cannot_be_forged(self):
        with self.assertRaises(D.DualError):
            D.Approval(object(), R(), self.w, self.m)


class FakeClient:
    status_on = True


class ClockTest(unittest.TestCase):
    def test_default_clock_is_monotonic(self):
        import time
        self.assertIs(D.DualEntry()._clock, time.monotonic)


class RunTest(unittest.TestCase):
    def approval(self, kind="update"):
        d = D.DualEntry()
        w, m = U(1, "worker"), U(2, "manager")
        d.submit(w, R(kind=kind))
        return d.verify(m, R(kind=kind)).approval

    def test_update_print_sequence(self):
        sent = []
        replies = iter(["~JU0|", "~PR0|0|", "~PG0|"])
        ok, why = D.run(FakeClient(), self.approval("update_print"),
                        lambda m, t: (sent.append(m), next(replies))[1])
        self.assertEqual((ok, why), (True, ""))
        self.assertEqual([s[:3] for s in sent], ["~JU", "~PS", "~PG"])

    def test_device_failure_reasons(self):
        ok, why = D.run(FakeClient(), self.approval(), lambda m, t: "~JU1|")
        self.assertFalse(ok)
        self.assertIn("장비 응답", why)

        def boom(m, t):
            raise ngpcl.NgpclError("응답 시간 초과(60초)")
        ok, why = D.run(FakeClient(), self.approval(), boom)
        self.assertFalse(ok)
        self.assertIn("시간 초과", why)

    def test_cannot_run_twice(self):
        a = self.approval()
        D.run(FakeClient(), a, lambda m, t: "~JU0|")
        with self.assertRaises(D.DualError):
            D.run(FakeClient(), a, lambda m, t: "~JU0|")

    def test_not_ready_to_print(self):
        replies = iter(["~JU0|", "~PR0|2|"])
        ok, why = D.run(FakeClient(), self.approval("update_print"), lambda m, t: next(replies))
        self.assertFalse(ok)
        self.assertIn("인쇄할 수 없는 상태", why)


class SettingKindTest(unittest.TestCase):
    """설정 변경(~SC)도 이중 입력: 같은 이름·값을 두 사람이 입력해야 승인표가 나온다."""

    def setUp(self):
        self.d = D.DualEntry(clock=Clock())
        self.w, self.m = U(1, "worker"), U(2, "manager")

    @staticmethod
    def S(name="darkness", value="90"):
        return D.Request("setting", "무시됨", "5", [(name, value)], False)

    def test_message_and_normalised_fields(self):
        r = self.S()
        self.assertEqual(r.message(), "~SCdarkness|90|")        # 매뉴얼 5.16 예제
        self.assertEqual((r.job, r.alloc, r.imaged), ("", "0", True))

    def test_exactly_one_pair(self):
        with self.assertRaises(D.DualError):
            D.Request("setting", "", "0", [], True)
        with self.assertRaises(D.DualError):
            D.Request("setting", "", "0", [("a", "1"), ("b", "2")], True)

    def test_match_gives_approval(self):
        self.d.submit(self.w, self.S())
        r = self.d.verify(self.m, self.S())
        self.assertEqual(r.status, "match")
        self.assertEqual(r.approval.consume().kind, "setting")

    def test_name_or_value_mismatch_blocks(self):
        self.d.submit(self.w, self.S())
        self.assertEqual(self.d.verify(self.m, self.S(value="91")).status, "retry")
        self.assertEqual(self.d.verify(self.m, self.S(name="Darkness")).status, "retry")
        self.assertEqual(self.d.verify(self.m, self.S(value="80")).status, "reset")   # 3회

    def test_run_refuses_setting(self):
        self.d.submit(self.w, self.S())
        appr = self.d.verify(self.m, self.S()).approval
        sent = []
        ok, why = D.run(None, appr, lambda m, t: sent.append(m) or "~SC0|")
        self.assertFalse(ok)
        self.assertEqual(sent, [])          # 전용 경로 밖에서는 장비로 보내지 않는다

    def test_bad_characters_blocked(self):
        with self.assertRaises(D.DualError):
            D.Request("setting", "", "0", [("dark|ness", "1")], True)


if __name__ == "__main__":
    unittest.main()

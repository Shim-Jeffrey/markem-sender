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
            self.d.verify(U(4, "master"), R())
        with self.assertRaises(D.DualError):
            self.d.submit(self.m, R())                    # 관리자는 1차 입력 불가
        self.assertTrue(self.d.has_pending())             # 잘못된 시도는 횟수에 안 센다
        self.assertEqual(self.d.tries(), 0)

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


if __name__ == "__main__":
    unittest.main()

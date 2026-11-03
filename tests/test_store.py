import csv
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import store as S


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "t.db")
        self.s = S.Store(self.db)
        self.s.create_first_master("boss", "마스터", "Master1234")
        self.m = self.s.login("boss", "Master1234")

    def mk(self, name, role="worker", pw="Temp12345"):
        self.s.create_user(self.m, name, name + "이름", role, pw)
        return self.s.login(name, pw)

    def test_first_master_only_once(self):
        with self.assertRaises(S.AuthError):
            self.s.create_first_master("boss2", "x", "Master1234")

    def test_password_not_plaintext(self):
        with sqlite3.connect(self.db) as c:
            h = c.execute("SELECT pw_hash FROM users WHERE username='boss'").fetchone()[0]
        self.assertTrue(h.startswith("scrypt$"))
        self.assertNotIn("Master1234", h)

    def test_policy(self):
        for bad in ("short1", "onlyletters", "12345678", "boss"):
            with self.assertRaises(S.AuthError):
                S.check_policy(bad, "boss")

    def test_login_fail_same_message_and_lock(self):
        self.mk("kim")
        with self.assertRaises(S.AuthError) as a:
            self.s.login("nobody", "x")
        with self.assertRaises(S.AuthError) as b:
            self.s.login("kim", "wrong")
        self.assertEqual(str(a.exception), str(b.exception))
        for _ in range(S.MAX_FAILED - 1):
            with self.assertRaises(S.AuthError):
                self.s.login("kim", "wrong")
        with self.assertRaises(S.AuthError) as c:   # 잠금 뒤에는 맞는 비밀번호도 거부
            self.s.login("kim", "Temp12345")
        self.assertIn("잠겼", str(c.exception))
        k = [u for u in self.s.list_users(self.m) if u.username == "kim"][0]
        self.s.unlock(self.m, k.id)
        self.assertEqual(self.s.login("kim", "Temp12345").username, "kim")

    def test_must_change_and_change(self):
        u = self.mk("kim")
        self.assertTrue(u.must_change)
        with self.assertRaises(S.AuthError):
            self.s.change_password(u, "bad", "NewPass123")
        with self.assertRaises(S.AuthError):
            self.s.change_password(u, "Temp12345", "Temp12345")
        self.s.change_password(u, "Temp12345", "NewPass123")
        self.assertFalse(self.s.login("kim", "NewPass123").must_change)

    def test_only_master_manages(self):
        w = self.mk("kim")
        with self.assertRaises(S.AuthError):
            self.s.create_user(w, "lee", "이", "worker", "Temp12345")
        with self.assertRaises(S.AuthError):
            self.s.list_users(w)
        fake = S.User(w.id, "boss", "x", "master", False)   # 화면 객체를 위조해도 DB로 확인
        with self.assertRaises(S.AuthError):
            self.s.delete_user(fake, self.m.id)

    def test_cannot_reuse_name(self):
        u = self.mk("kim")
        self.s.delete_user(self.m, u.id)
        with self.assertRaises(S.AuthError):
            self.s.login("kim", "Temp12345")
        with self.assertRaises(S.AuthError):
            self.s.create_user(self.m, "kim", "새김", "worker", "Temp12345")

    def test_last_master_and_self_protected(self):
        with self.assertRaises(S.AuthError):
            self.s.delete_user(self.m, self.m.id)
        with self.assertRaises(S.AuthError):
            self.s.set_active(self.m, self.m.id, False)

    def test_deactivate_blocks_login(self):
        u = self.mk("kim")
        self.s.set_active(self.m, u.id, False)
        with self.assertRaises(S.AuthError):
            self.s.login("kim", "Temp12345")
        self.s.set_active(self.m, u.id, True)
        self.assertEqual(self.s.login("kim", "Temp12345").username, "kim")

    def test_reset_password(self):
        u = self.mk("kim")
        self.s.reset_password(self.m, u.id, "Reset12345")
        with self.assertRaises(S.AuthError):
            self.s.login("kim", "Temp12345")
        self.assertTrue(self.s.login("kim", "Reset12345").must_change)

    def test_chain_ok_and_tamper_detected(self):
        self.mk("kim")
        self.assertEqual(self.s.verify_chain(self.m), (True, None))
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE events SET actor='zzz' WHERE id=2")
        ok, why = self.s.verify_chain(self.m)
        self.assertFalse(ok)
        self.assertIn("2번", why)

    def test_chain_delete_detected(self):
        self.mk("kim")
        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM events WHERE id=2")
        self.assertFalse(self.s.verify_chain(self.m)[0])

    def test_sql_injection_username(self):
        with self.assertRaises(S.AuthError):
            self.s.login("boss' OR '1'='1", "x")
        with self.assertRaises(S.AuthError):
            self.s.create_user(self.m, "a'; DROP TABLE users;--", "x", "worker", "Temp12345")
        self.assertTrue(self.s.has_master())

    def test_tail_truncation_detected(self):
        self.mk("kim")
        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM events WHERE id=(SELECT MAX(id) FROM events)")
        ok, why = self.s.verify_chain(self.m)
        self.assertFalse(ok)
        self.assertIn("끝부분", why)

    def test_events_and_verify_need_master(self):
        w = self.mk("kim")
        with self.assertRaises(S.AuthError):
            self.s.events(w)
        with self.assertRaises(S.AuthError):
            self.s.verify_chain(w)
        self.assertTrue(self.s.events(self.m))

    def test_change_password_failures_count_lock_and_log(self):
        u = self.mk("kim")
        for _ in range(S.MAX_FAILED):
            with self.assertRaises(S.AuthError):
                self.s.change_password(u, "wrong", "NewPass123")
        with self.assertRaises(S.AuthError):
            self.s.login("kim", "Temp12345")          # 잠김
        types = [e["type"] for e in self.s.events(self.m)]
        self.assertIn("password_change_fail", types)
        self.assertIn("account_locked", types)

    def test_locked_master_cannot_manage(self):
        m2 = self.s.login("boss", "Master1234")
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE users SET locked=1 WHERE username='boss'")
        with self.assertRaises(S.AuthError):
            self.s.list_users(m2)

    def test_unknown_login_name_masked(self):
        with self.assertRaises(S.AuthError):
            self.s.login("Secret-Pass-1234", "x")
        actors = [e["actor"] for e in self.s.events(self.m)]
        self.assertNotIn("secret-pass-1234", actors)
        self.assertIn("(없는 아이디)", actors)

    def test_env_override_ignored_without_dev(self):
        d = tempfile.mkdtemp()
        old = {k: os.environ.get(k) for k in ("MARKEM_DATA_DIR", "MARKEM_DEV")}
        try:
            os.environ["MARKEM_DATA_DIR"] = d
            os.environ.pop("MARKEM_DEV", None)
            self.assertNotEqual(os.path.abspath(S.default_data_dir()), os.path.abspath(d))
            os.environ["MARKEM_DEV"] = "1"
            self.assertEqual(S.default_data_dir(), d)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_temp_password_ok(self):
        for _ in range(50):
            S.check_policy(S.gen_temp_password())


class MasterAndWorkLogTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "t.db")
        self.s = S.Store(self.db)
        self.s.create_first_master("boss", "타스코마스터", "Master1234")
        self.t = self.s.login("boss", "Master1234")
        self.s.create_user(self.t, "cust", "거래처마스터", "master", "Cust12345")
        self.c = self.s.login("cust", "Cust12345")
        self.s.create_user(self.c, "kim", "김작업", "worker", "Temp12345")
        self.s.create_user(self.c, "lee", "이관리", "manager", "Temp12345")
        self.w = self.s.login("kim", "Temp12345")
        self.g = self.s.login("lee", "Temp12345")

    def ids(self, actor):
        return {u.username: u.id for u in self.s.list_users(actor)}

    def test_two_masters_and_labels(self):
        self.assertEqual(self.t.label, "마스터(타스코)")
        self.assertEqual(self.c.label, "마스터(거래처)")
        self.assertEqual(self.c.org, "customer")

    def test_only_one_customer_master(self):
        with self.assertRaises(S.AuthError):
            self.s.create_user(self.t, "cust2", "또", "master", "Cust12345")

    def test_customer_master_cannot_create_master_or_touch_tasco(self):
        with self.assertRaises(S.AuthError):
            self.s.create_user(self.c, "cust2", "또", "master", "Cust12345")
        ids = self.ids(self.c)
        for fn in (lambda: self.s.set_active(self.c, ids["boss"], False),
                   lambda: self.s.delete_user(self.c, ids["boss"]),
                   lambda: self.s.reset_password(self.c, ids["boss"], "Reset12345"),
                   lambda: self.s.unlock(self.c, ids["boss"])):
            with self.assertRaises(S.AuthError):
                fn()
        self.assertEqual(self.s.login("boss", "Master1234").username, "boss")
        # 거부 시도도 기록에 남는다
        types = [e["type"] for e in self.s.events(self.t)]
        self.assertGreaterEqual(types.count("permission_denied"), 5)

    def test_tasco_master_manages_customer_master(self):
        ids = self.ids(self.t)
        self.s.reset_password(self.t, ids["cust"], "Reset12345")
        self.assertTrue(self.s.login("cust", "Reset12345").must_change)
        self.s.set_active(self.t, ids["cust"], False)
        with self.assertRaises(S.AuthError):
            self.s.login("cust", "Reset12345")
        self.s.delete_user(self.t, ids["cust"])
        self.s.create_user(self.t, "cust3", "새거래처", "master", "Cust12345")   # 삭제 뒤 새로 가능

    def test_customer_master_manages_workers(self):
        ids = self.ids(self.c)
        self.s.set_active(self.c, ids["kim"], False)
        self.s.set_active(self.c, ids["kim"], True)
        self.s.reset_password(self.c, ids["lee"], "Reset12345")

    def test_master_changes_logged_with_who_and_before_after(self):
        ids = self.ids(self.t)
        self.s.set_active(self.t, ids["kim"], False)
        ev = {e["type"]: json.loads(e["detail"]) for e in self.s.events(self.c)}
        d = ev["user_deactivated"]
        self.assertEqual((d["이전"], d["이후"], d["소속"], d["실행자"]),
                         ("사용", "비활성", "타스코", "타스코마스터"))
        created = [json.loads(e["detail"]) for e in self.s.events(self.c)
                   if e["type"] == "user_created"]
        self.assertTrue(any(x["대상"] == "kim" and x["소속"] == "거래처" for x in created))

    def test_master_lock_auto_releases(self):
        for _ in range(S.MAX_FAILED):
            with self.assertRaises(S.AuthError):
                self.s.login("boss", "wrong")
        with self.assertRaises(S.AuthError) as e:
            self.s.login("boss", "Master1234")
        self.assertIn("뒤 다시", str(e.exception))
        with sqlite3.connect(self.db) as c:          # 시간이 지난 것으로 만든다
            c.execute("UPDATE users SET locked_until='2000-01-01T00:00:00' WHERE username='boss'")
        self.assertEqual(self.s.login("boss", "Master1234").username, "boss")

    def test_work_log_and_csv(self):
        self.s.log_work("work_retry", self.w, self.g, tries=1, fields=["LOT"], job="JOB-A")
        self.s.log_work("work_ok", self.w, self.g, job="JOB-A", data="LOT=L1; EXP=20281031")
        self.s.log_work("work_fail", self.w, None, job="JOB-B", reason="1차 입력 만료")
        p = os.path.join(self.dir, "work.csv")
        n = self.s.export_work_csv(self.c, p)
        self.assertEqual(n, 2)                       # 최종 결과(성공·실패)만
        rows = list(csv.reader(open(p, encoding="utf-8-sig")))
        self.assertEqual(rows[0][3], "결과")
        self.assertEqual((rows[1][3], rows[1][4], rows[1][5]),
                         ("작업 성공", "김작업(kim)", "이관리(lee)"))
        self.assertEqual(rows[1][8], "LOT=L1; EXP=20281031")
        self.assertEqual((rows[2][3], rows[2][5], rows[2][10]), ("작업 실패", "없음", "1차 입력 만료"))

    def test_csv_injection_guard_and_event_csv(self):
        self.s.log_work("work_ok", self.w, self.g, job="JOB", data="=cmd|' /C calc'!A0")
        p = os.path.join(self.dir, "w.csv")
        self.s.export_work_csv(self.t, p)
        self.assertTrue(list(csv.reader(open(p, encoding="utf-8-sig")))[1][8].startswith("'="))
        p2 = os.path.join(self.dir, "e.csv")
        self.assertGreater(self.s.export_event_csv(self.t, p2, "계정 변경"), 0)
        self.assertIn("타스코마스터", open(p2, encoding="utf-8-sig").read())

    def test_export_and_work_need_valid_actors(self):
        with self.assertRaises(S.AuthError):
            self.s.export_work_csv(self.w, os.path.join(self.dir, "x.csv"))
        with self.assertRaises(S.AuthError):
            self.s.log_work("work_ok", self.g, self.w)          # 역할이 뒤바뀌면 기록 불가

    def test_work_detail_readable(self):
        self.s.log_work("work_ok", self.w, self.g, job="JOB-A", kind="갱신 후 인쇄",
                        data="LOT=L1")
        e = [x for x in self.s.events(self.t, group="작업")][0]
        txt = S.format_detail(e["detail"], e["type"])
        self.assertTrue(txt.startswith("1차 김작업(kim) / 2차 이관리(lee)"))
        self.assertIn("작업명=JOB-A", txt)
        self.assertIn("데이터=LOT=L1", txt)

    def test_event_groups_filter(self):
        work = self.s.events(self.t, group="작업")
        self.assertEqual(work, [])
        self.s.log_work("work_request", self.w, job="J")
        self.assertEqual(len(self.s.events(self.t, group="작업")), 1)
        self.assertTrue(all(e["type"].startswith("user_") or e["type"] in
                            ("master_created", "permission_denied", "password_changed",
                             "password_reset", "account_unlocked", "password_change_fail", "export")
                            for e in self.s.events(self.t, group="계정 변경")))


if __name__ == "__main__":
    unittest.main()

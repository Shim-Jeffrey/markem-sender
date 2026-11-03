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

    def test_cannot_create_master_or_reuse_name(self):
        with self.assertRaises(S.AuthError):
            self.s.create_user(self.m, "x1x", "x", "master", "Temp12345")
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
        self.assertEqual(self.s.verify_chain(), (True, None))
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE events SET actor='zzz' WHERE id=2")
        ok, bad = self.s.verify_chain()
        self.assertFalse(ok)
        self.assertEqual(bad, 2)

    def test_chain_delete_detected(self):
        self.mk("kim")
        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM events WHERE id=2")
        self.assertFalse(self.s.verify_chain()[0])

    def test_sql_injection_username(self):
        with self.assertRaises(S.AuthError):
            self.s.login("boss' OR '1'='1", "x")
        with self.assertRaises(S.AuthError):
            self.s.create_user(self.m, "a'; DROP TABLE users;--", "x", "worker", "Temp12345")
        self.assertTrue(self.s.has_master())

    def test_temp_password_ok(self):
        for _ in range(50):
            S.check_policy(S.gen_temp_password())


if __name__ == "__main__":
    unittest.main()

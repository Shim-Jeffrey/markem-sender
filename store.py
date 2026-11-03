"""계정·기록 저장 (로컬 SQLite). 설계: docs/설계-이중입력.md

- 비밀번호는 scrypt 해시로만 저장한다(원문 저장 없음).
- 모든 사건(로그인·계정 변경)은 해시 체인 기록에 남긴다(변조 감지용).
- 계정 변경은 호출자를 DB에서 다시 확인한다(화면의 사용자 객체를 믿지 않는다).
"""
import base64
import contextlib
import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass

ROLES = ("worker", "manager", "master")
ROLE_LABEL = {"worker": "1차 작업자", "manager": "2차 관리자", "master": "마스터"}
MAX_FAILED = 5
_USER_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
_SCRYPT = (2 ** 14, 8, 1)
GENERIC_FAIL = "아이디 또는 비밀번호가 올바르지 않습니다"


class AuthError(Exception):
    """화면에 그대로 보여줄 수 있는 한국어 메시지를 담는다."""


@dataclass
class User:
    id: int
    username: str
    display_name: str
    role: str
    must_change: bool
    active: bool = True
    locked: bool = False
    deleted: bool = False


def default_data_dir():
    # 개발·시험 전용: 일반 실행에서는 환경변수로 저장 위치를 바꿀 수 없다(빈 DB로 우회 방지)
    override = os.environ.get("MARKEM_DATA_DIR") if os.environ.get("MARKEM_DEV") == "1" else None
    if override:
        return override
    base = os.environ.get("ProgramData")
    if base:
        d = os.path.join(base, "TASCO", "MarkemSender")
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".w")
            with open(probe, "w") as f:
                f.write("1")
            os.remove(probe)
            return d
        except OSError:
            pass
    d = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "TASCO",
                     "MarkemSender")
    os.makedirs(d, exist_ok=True)
    return d


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ---- 비밀번호 ----

def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = _SCRYPT
    h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (n, r, p, base64.b64encode(salt).decode(),
                                      base64.b64encode(h).decode())


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode("utf-8"), salt=base64.b64decode(salt), n=int(n),
                              r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except Exception:
        return False


def check_policy(pw: str, username=""):
    if len(pw) < 8:
        raise AuthError("비밀번호는 8자 이상이어야 합니다")
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)):
        raise AuthError("비밀번호에 영문자와 숫자를 모두 넣어 주세요")
    if username and pw.lower() == username.lower():
        raise AuthError("비밀번호는 아이디와 달라야 합니다")


def gen_temp_password() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(10))
        if re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw):
            return pw


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('worker','manager','master')),
  pw_hash TEXT NOT NULL,
  must_change INTEGER NOT NULL DEFAULT 1,
  active INTEGER NOT NULL DEFAULT 1,
  failed INTEGER NOT NULL DEFAULT 0,
  locked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS meta(
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  type TEXT NOT NULL,
  actor TEXT NOT NULL,
  detail TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path=None):
        self.path = path or os.path.join(default_data_dir(), "data.db")
        with self._conn() as c:
            c.executescript(_SCHEMA)
        # 존재하지 않는 계정도 같은 시간이 걸리도록 쓰는 가짜 해시
        self._dummy = hash_password("dummy-password-0")

    @contextlib.contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    # ---- 기록(해시 체인) ----

    @staticmethod
    def _digest(prev, ts, typ, actor, detail):
        raw = json.dumps([prev, ts, typ, actor, detail], ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _log(self, c, typ, actor, detail):
        row = c.execute("SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
        prev = row["hash"] if row else "0" * 64
        ts = now()
        d = json.dumps(detail, ensure_ascii=False, sort_keys=True)
        h = self._digest(prev, ts, typ, actor, d)
        c.execute("INSERT INTO events(ts,type,actor,detail,prev_hash,hash) VALUES(?,?,?,?,?,?)",
                  (ts, typ, actor, d, prev, h))
        n = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        for k, v in (("head", h), ("count", str(n))):
            c.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                      (k, v))

    def log_event(self, typ, actor, detail):
        with self._conn() as c:
            self._log(c, typ, actor, detail)

    def events(self, actor, limit=500):
        with self._conn() as c:
            self._require_master(c, actor)
            return [dict(r) for r in c.execute(
                "SELECT ts,type,actor,detail FROM events ORDER BY id DESC LIMIT ?", (limit,))]

    def verify_chain(self, actor):
        """기록이 중간에 고쳐지거나 지워졌는지 확인. (정상 여부, 문제 설명)

        끝부분 삭제도 잡도록 마지막 해시·건수를 별도 기준값(meta)과 대조한다.
        한계: DB 파일을 쓸 수 있는 사람이 기록과 기준값을 함께 새로 만들면 감지할 수 없다.
        """
        with self._conn() as c:
            self._require_master(c, actor)
            prev, n = "0" * 64, 0
            for r in c.execute("SELECT * FROM events ORDER BY id"):
                if r["prev_hash"] != prev or r["hash"] != self._digest(
                        r["prev_hash"], r["ts"], r["type"], r["actor"], r["detail"]):
                    return False, "%d번 기록부터 변조 또는 삭제" % r["id"]
                prev, n = r["hash"], n + 1
            meta = {r["k"]: r["v"] for r in c.execute("SELECT k,v FROM meta")}
            if "head" not in meta:
                if n:   # 기준값이 없던 예전 DB: 지금 상태를 기준으로 삼는다
                    c.execute("INSERT INTO meta(k,v) VALUES('head',?),('count',?)", (prev, str(n)))
                return True, None
            if meta["head"] != prev or meta["count"] != str(n):
                return False, "끝부분 기록이 지워졌거나 기준값과 다릅니다"
            return True, None

    # ---- 조회 ----

    @staticmethod
    def _user(r):
        return User(r["id"], r["username"], r["display_name"], r["role"], bool(r["must_change"]),
                    bool(r["active"]), bool(r["locked"]), r["deleted_at"] is not None)

    def has_master(self):
        with self._conn() as c:
            return c.execute("SELECT 1 FROM users WHERE role='master' AND active=1 "
                             "AND deleted_at IS NULL").fetchone() is not None

    def _require_master(self, c, actor):
        r = c.execute("SELECT * FROM users WHERE id=?", (actor.id,)).fetchone()
        if not r or r["role"] != "master" or not r["active"] or r["deleted_at"] or r["locked"]:
            raise AuthError("마스터 권한이 필요합니다")
        return self._user(r)

    def list_users(self, actor):
        with self._conn() as c:
            self._require_master(c, actor)
            return [self._user(r) for r in c.execute(
                "SELECT * FROM users WHERE deleted_at IS NULL ORDER BY role DESC, username")]

    # ---- 첫 마스터 ----

    def create_first_master(self, username, display_name, password):
        username = self._check_username(username)
        check_policy(password, username)
        with self._conn() as c:
            if c.execute("SELECT 1 FROM users WHERE role='master' AND deleted_at IS NULL"
                         ).fetchone():
                raise AuthError("이미 마스터 계정이 있습니다")
            c.execute("INSERT INTO users(username,display_name,role,pw_hash,must_change,created_at)"
                      " VALUES(?,?,?,?,0,?)",
                      (username, display_name.strip() or username, "master",
                       hash_password(password), now()))
            self._log(c, "master_created", username, {"username": username})

    @staticmethod
    def _check_username(username):
        username = (username or "").strip().lower()
        if not _USER_RE.match(username):
            raise AuthError("아이디는 영문·숫자·._- 로 3~32자여야 합니다")
        return username

    # ---- 로그인 ----

    def login(self, username, password) -> User:
        # 실패 기록(횟수·잠금·이력)이 롤백되지 않도록, 예외는 저장이 끝난 뒤에 던진다.
        uname = (username or "").strip().lower()
        err = None
        user = None
        with self._conn() as c:
            r = c.execute("SELECT * FROM users WHERE username=?", (uname,)).fetchone()
            usable = r is not None and bool(r["active"]) and not r["deleted_at"]                 and bool(r["pw_hash"])
            ok = verify_password(password or "", r["pw_hash"] if usable else self._dummy)                 and usable
            if not usable:
                known = r is not None
                self._log(c, "login_fail", uname if known else "(없는 아이디)",
                          {"reason": "비활성 또는 삭제" if known else "없는 아이디"})
                err = GENERIC_FAIL
            elif r["locked"]:
                self._log(c, "login_fail", uname, {"reason": "잠김"})
                err = "계정이 잠겼습니다. 마스터에게 문의하세요"
            elif not ok:
                failed = r["failed"] + 1
                lock = 1 if failed >= MAX_FAILED else 0
                c.execute("UPDATE users SET failed=?, locked=? WHERE id=?", (failed, lock, r["id"]))
                self._log(c, "login_fail", uname, {"reason": "비밀번호 불일치", "횟수": failed})
                if lock:
                    self._log(c, "account_locked", uname, {})
                    err = "로그인 %d회 실패로 계정이 잠겼습니다. 마스터에게 문의하세요" % MAX_FAILED
                else:
                    err = GENERIC_FAIL
            else:
                c.execute("UPDATE users SET failed=0 WHERE id=?", (r["id"],))
                self._log(c, "login_ok", uname, {"role": r["role"], "name": r["display_name"]})
                user = self._user(r)
        if err:
            raise AuthError(err)
        return user

    def logout(self, user):
        self.log_event("logout", user.username, {})

    def change_password(self, user, old, new):
        # 실패 기록이 롤백되지 않도록 예외는 저장이 끝난 뒤에 던진다.
        err = None
        with self._conn() as c:
            r = c.execute("SELECT * FROM users WHERE id=?", (user.id,)).fetchone()
            if not r or not r["active"] or r["deleted_at"] or r["locked"]:
                err = "비밀번호를 바꿀 수 없는 계정입니다. 마스터에게 문의하세요"
            elif not verify_password(old, r["pw_hash"]):
                failed = r["failed"] + 1
                lock = 1 if failed >= MAX_FAILED else 0
                c.execute("UPDATE users SET failed=?, locked=? WHERE id=?", (failed, lock, r["id"]))
                self._log(c, "password_change_fail", r["username"], {"횟수": failed})
                if lock:
                    self._log(c, "account_locked", r["username"], {})
                    err = "실패 %d회로 계정이 잠겼습니다. 마스터에게 문의하세요" % MAX_FAILED
                else:
                    err = "현재 비밀번호가 올바르지 않습니다"
            else:
                try:
                    check_policy(new, r["username"])
                    if verify_password(new, r["pw_hash"]):
                        raise AuthError("새 비밀번호는 현재 비밀번호와 달라야 합니다")
                except AuthError as e:
                    err = str(e)
                else:
                    c.execute("UPDATE users SET pw_hash=?, must_change=0, failed=0 WHERE id=?",
                              (hash_password(new), r["id"]))
                    self._log(c, "password_changed", r["username"], {})
        if err:
            raise AuthError(err)
        user.must_change = False

    # ---- 계정 관리(마스터 전용) ----

    def create_user(self, actor, username, display_name, role, temp_password):
        if role not in ("worker", "manager"):
            raise AuthError("마스터는 첫 실행 때만 만들 수 있습니다")
        username = self._check_username(username)
        check_policy(temp_password, username)
        if not display_name.strip():
            raise AuthError("이름을 입력하세요")
        with self._conn() as c:
            m = self._require_master(c, actor)
            if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise AuthError("이미 사용했던 아이디입니다(삭제된 아이디도 다시 쓸 수 없습니다)")
            c.execute("INSERT INTO users(username,display_name,role,pw_hash,must_change,created_at)"
                      " VALUES(?,?,?,?,1,?)",
                      (username, display_name.strip(), role, hash_password(temp_password), now()))
            self._log(c, "user_created", m.username,
                      {"대상": username, "이름": display_name.strip(), "role": role})

    def _target(self, c, m, user_id):
        t = c.execute("SELECT * FROM users WHERE id=? AND deleted_at IS NULL", (user_id,)).fetchone()
        if not t:
            raise AuthError("계정을 찾을 수 없습니다")
        return t

    def _guard_last_master(self, c, t, what):
        if t["role"] == "master":
            n = c.execute("SELECT COUNT(*) FROM users WHERE role='master' AND active=1 "
                          "AND deleted_at IS NULL AND id<>?", (t["id"],)).fetchone()[0]
            if n == 0:
                raise AuthError("마지막 마스터 계정은 %s할 수 없습니다" % what)

    def set_active(self, actor, user_id, active):
        with self._conn() as c:
            m = self._require_master(c, actor)
            t = self._target(c, m, user_id)
            if not active:
                if t["id"] == m.id:
                    raise AuthError("자기 자신은 비활성화할 수 없습니다")
                self._guard_last_master(c, t, "비활성화")
            c.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, t["id"]))
            self._log(c, "user_activated" if active else "user_deactivated", m.username,
                      {"대상": t["username"]})

    def delete_user(self, actor, user_id):
        """삭제: 로그인 불가 + 목록에서 제거. 아이디와 과거 기록은 남긴다."""
        with self._conn() as c:
            m = self._require_master(c, actor)
            t = self._target(c, m, user_id)
            if t["id"] == m.id:
                raise AuthError("자기 자신은 삭제할 수 없습니다")
            self._guard_last_master(c, t, "삭제")
            c.execute("UPDATE users SET deleted_at=?, active=0, pw_hash='' WHERE id=?",
                      (now(), t["id"]))
            self._log(c, "user_deleted", m.username,
                      {"대상": t["username"], "이름": t["display_name"]})

    def reset_password(self, actor, user_id, temp_password):
        with self._conn() as c:
            m = self._require_master(c, actor)
            t = self._target(c, m, user_id)
            check_policy(temp_password, t["username"])
            c.execute("UPDATE users SET pw_hash=?, must_change=1, failed=0, locked=0 WHERE id=?",
                      (hash_password(temp_password), t["id"]))
            self._log(c, "password_reset", m.username, {"대상": t["username"]})

    def unlock(self, actor, user_id):
        with self._conn() as c:
            m = self._require_master(c, actor)
            t = self._target(c, m, user_id)
            c.execute("UPDATE users SET failed=0, locked=0 WHERE id=?", (t["id"],))
            self._log(c, "account_unlocked", m.username, {"대상": t["username"]})

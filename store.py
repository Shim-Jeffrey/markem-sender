"""계정·기록 저장 (로컬 SQLite). 설계: docs/설계-이중입력.md

- 비밀번호는 scrypt 해시로만 저장한다(원문 저장 없음).
- 모든 사건(로그인·계정 변경·작업 결과)은 해시 체인 기록에 남긴다(변조 감지용).
- 계정 변경은 호출자를 DB에서 다시 확인한다(화면의 사용자 객체를 믿지 않는다).
- 마스터는 2개: 타스코(tasco) 마스터가 상위, 거래처(customer) 마스터는 1개만.
"""
import base64
import contextlib
import csv
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
ORG_LABEL = {"tasco": "타스코", "customer": "거래처"}
MAX_FAILED = 5
MASTER_LOCK_MINUTES = 15
SELF_MANAGED_HOURS = 24           # 마스터가 이 시간 안에 만들거나 초기화한 작업자를 직접 승인하면 표시          # 마스터는 서로 풀 수 없으므로 잠금이 시간이 지나면 풀린다
_USER_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
_SCRYPT = (2 ** 14, 8, 1)
GENERIC_FAIL = "아이디 또는 비밀번호가 올바르지 않습니다"

EVENT_LABEL = {
    "login_ok": "로그인", "login_fail": "로그인 실패", "logout": "로그아웃",
    "account_locked": "계정 잠김", "account_unlocked": "잠금 해제",
    "account_unlocked_auto": "잠금 자동 해제",
    "user_created": "계정 추가", "user_deleted": "계정 삭제",
    "user_activated": "계정 활성화", "user_deactivated": "계정 비활성화",
    "password_changed": "비밀번호 변경", "password_reset": "비밀번호 초기화",
    "password_change_fail": "비밀번호 변경 실패", "master_created": "마스터 생성",
    "permission_denied": "권한 없음(시도)", "export": "기록 내보내기",
    "work_request": "1차 입력 접수", "work_retry": "2차 불일치(재입력)",
    "work_ok": "작업 성공", "work_fail": "작업 실패", "work_log_error": "작업 기록 오류",
    "print_control": "인쇄 시작·정지", "app_exit": "프로그램 종료",
}
EVENT_GROUPS = {
    "전체": None,
    "로그인": ("login_ok", "login_fail", "logout", "account_locked", "account_unlocked_auto",
            "app_exit"),
    "계정 변경": ("user_created", "user_deleted", "user_activated", "user_deactivated",
              "password_changed", "password_reset", "password_change_fail", "account_unlocked",
              "master_created", "permission_denied", "export"),
    "작업": ("work_request", "work_retry", "work_ok", "work_fail", "work_log_error",
           "print_control"),
}
_ADMIN_TYPES = EVENT_GROUPS["계정 변경"]


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
    org: str = "tasco"
    login_ts: str = ""

    @property
    def label(self):
        """화면 표시용 역할 이름. 마스터는 소속을 붙인다."""
        if self.role == "master":
            return "마스터(%s)" % ORG_LABEL.get(self.org, self.org)
        return ROLE_LABEL[self.role]


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


def format_detail(detail_json: str, typ: str = "") -> str:
    """기록의 내용(JSON)을 읽기 쉽게 바꾼다. 작업 기록은 '1차 누구 / 2차 누구 / 작업명 …' 형태."""
    try:
        d = json.loads(detail_json)
    except ValueError:
        return detail_json
    if not isinstance(d, dict):
        return str(d)
    if typ.startswith("work_"):
        if d.get("2차", "없음") != "없음":
            role = d.get("2차역할", "")
            tag = role or d.get("2차소속", "")
            two = "2차 %s(%s%s)" % (d.get("2차이름", ""), d["2차"], ", " + tag if tag else "")
        else:
            two = "2차 없음"
        org1 = d.get("1차소속", "")
        head = ["1차 %s(%s%s)" % (d.get("1차이름", ""), d.get("1차", ""), ", " + org1 if org1 else ""),
                two]
        if d.get("주의"):
            head.insert(0, "[주의] " + d["주의"])
        used = {"결과", "1차", "1차이름", "1차소속", "2차", "2차이름", "2차역할", "2차소속", "1차접속",
                "2차접속", "주의"}
        rest = ["%s=%s" % (k, ", ".join(v) if isinstance(v, list) else v)
                for k, v in d.items() if k not in used and v not in ("", None, [])]
        return " / ".join(head + rest)
    return "; ".join("%s=%s" % (k, ", ".join(v) if isinstance(v, list) else v)
                     for k, v in d.items() if v not in ("", None, []))


def _csv_safe(v):
    """엑셀이 수식으로 실행하지 않도록 = + - @ 로 시작하는 값 앞에 ' 를 붙인다."""
    v = "" if v is None else str(v)
    return "'" + v if v.lstrip(" \"'")[:1] in ("=", "+", "-", "@", "\t", "\r") else v


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
  deleted_at TEXT,
  org TEXT NOT NULL DEFAULT 'tasco',
  locked_until TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_customer_master ON users(org)
  WHERE role='master' AND org='customer' AND deleted_at IS NULL;
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
            cols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
            for name, ddl in (("org", "TEXT NOT NULL DEFAULT 'tasco'"), ("locked_until", "TEXT")):
                if name not in cols:      # 이전 버전 DB 이전
                    c.execute("ALTER TABLE users ADD COLUMN %s %s" % (name, ddl))
        # 존재하지 않는 계정도 같은 시간이 걸리도록 쓰는 가짜 해시
        self._dummy = hash_password("dummy-password-0")

    @contextlib.contextmanager
    def _conn(self, keep=False):
        """keep=True: 권한 거부(AuthError)로 끝나도 그 전까지 남긴 기록은 저장한다.
        (거부 시도 기록용. 쓰는 곳은 거부 전에 다른 변경을 하지 않는다.)"""
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except AuthError:
            if keep:
                c.commit()
            else:
                c.rollback()
            raise
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

    def events(self, actor, limit=500, group="전체"):
        types = EVENT_GROUPS[group]
        with self._conn() as c:
            self._require_master(c, actor)
            if types is None:
                rows = c.execute("SELECT ts,type,actor,detail FROM events ORDER BY id DESC LIMIT ?",
                                 (limit,))
            else:
                q = ",".join("?" * len(types))
                rows = c.execute("SELECT ts,type,actor,detail FROM events WHERE type IN (%s) "
                                 "ORDER BY id DESC LIMIT ?" % q, (*types, limit))
            return [dict(r) for r in rows]

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

    # ---- 작업 기록 (이중 입력 결과) ----

    def log_work(self, typ, worker, manager=None, **info):
        """작업 로그. typ: work_request / work_retry / work_ok / work_fail.

        worker·manager 는 DB에서 다시 확인한다(역할·상태가 맞지 않으면 기록하지 않는다).
        info: job(작업명), data(데이터), reason(사유), tries(불일치 횟수), fields(불일치 필드명)
        """
        if typ not in ("work_request", "work_retry", "work_ok", "work_fail"):
            raise ValueError(typ)
        with self._conn() as c:
            w = self._load(c, worker.id, "worker")
            m = self._load(c, manager.id, ("manager", "master")) if manager else None
            d = {"결과": EVENT_LABEL[typ],
                 "1차": w["username"], "1차이름": w["display_name"],
                 "2차": m["username"] if m else "없음", "2차이름": m["display_name"] if m else "",
                 "1차접속": getattr(worker, "login_ts", ""),
                 "2차접속": getattr(manager, "login_ts", "") if manager else ""}
            d["1차소속"] = ORG_LABEL.get(w["org"], w["org"])
            if m is not None:
                d["2차소속"] = ORG_LABEL.get(m["org"], m["org"])
            if m is not None and m["role"] == "master":    # 마스터가 2차를 맡은 건은 표시해 둔다
                d["2차역할"] = "마스터(%s)" % ORG_LABEL.get(m["org"], m["org"])
                if self._recently_managed(c, m["username"], w["username"]):
                    d["주의"] = ("2차 마스터가 %d시간 안에 이 1차 계정을 만들거나 비밀번호를 초기화함"
                               % SELF_MANAGED_HOURS)
            names = {"job": "작업명", "kind": "작업종류", "data": "데이터", "reason": "사유",
                     "tries": "불일치횟수", "fields": "불일치필드", "verify": "수신확인"}
            for k, v in info.items():
                if k not in names:
                    raise ValueError(k)
                if v not in (None, "", []):
                    d[names[k]] = v
            self._log(c, typ, w["username"], d)

    def _recently_managed(self, c, master_name, worker_name):
        """마스터가 최근 그 작업자 계정을 만들었거나 비밀번호를 초기화했는지(임시 비밀번호를 앎)."""
        since = (datetime.datetime.now() - datetime.timedelta(hours=SELF_MANAGED_HOURS)
                 ).isoformat(timespec="seconds")
        for r in c.execute("SELECT detail FROM events WHERE actor=? AND ts>=? AND type IN "
                           "('user_created','password_reset')", (master_name, since)):
            try:
                if json.loads(r["detail"]).get("대상") == worker_name:
                    return True
            except ValueError:
                pass
        return False

    @staticmethod
    def _load(c, uid, role):
        roles = (role,) if isinstance(role, str) else tuple(role)
        r = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not r or r["role"] not in roles:
            raise AuthError("계정 정보를 확인할 수 없습니다")
        return r

    def user_usable(self, user_id, role):
        """계정이 지금도 쓸 수 있는 상태(존재·역할·활성·미삭제·미잠금)인지 DB에서 확인."""
        with self._conn() as c:
            r = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            return bool(r and r["role"] == role and r["active"] and not r["deleted_at"]
                        and not r["locked"])

    def log_work_safe(self, typ, worker, manager=None, **info):
        """작업 기록. 실패해도 전송 흐름을 멈추지 않고, 실패 사실을 별도 기록으로 남긴다."""
        try:
            self.log_work(typ, worker, manager, **info)
        except Exception as e:
            try:
                d = {"유형": EVENT_LABEL.get(typ, typ), "사유": "%s" % e,
                     "작업명": info.get("job", ""), "데이터": info.get("data", "")}
                self.log_event("work_log_error", getattr(worker, "username", "?"), d)
            except Exception:
                pass

    # ---- 조회 ----

    @staticmethod
    def _user(r, login_ts=""):
        return User(r["id"], r["username"], r["display_name"], r["role"], bool(r["must_change"]),
                    bool(r["active"]), bool(r["locked"]), r["deleted_at"] is not None,
                    r["org"], login_ts)

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
                "SELECT * FROM users WHERE deleted_at IS NULL ORDER BY role DESC, org, username")]

    # ---- 첫 마스터 ----

    def create_first_master(self, username, display_name, password):
        username = self._check_username(username)
        check_policy(password, username)
        with self._conn() as c:
            if c.execute("SELECT 1 FROM users WHERE role='master' AND deleted_at IS NULL"
                         ).fetchone():
                raise AuthError("이미 마스터 계정이 있습니다")
            c.execute("INSERT INTO users(username,display_name,role,pw_hash,must_change,created_at,"
                      "org) VALUES(?,?,?,?,0,?,'tasco')",
                      (username, display_name.strip() or username, "master",
                       hash_password(password), now()))
            self._log(c, "master_created", username, {"대상": username, "소속": "타스코"})

    @staticmethod
    def _check_username(username):
        username = (username or "").strip().lower()
        if not _USER_RE.match(username):
            raise AuthError("아이디는 영문·숫자·._- 로 3~32자여야 합니다")
        return username

    # ---- 로그인 ----

    def _record_failure(self, c, r, typ, base_detail):
        """실패 횟수를 올리고 한도에 닿으면 잠근다. 잠겼으면 True."""
        failed = r["failed"] + 1
        lock = failed >= MAX_FAILED
        until = None
        if lock and r["role"] == "master":
            until = (datetime.datetime.now() + datetime.timedelta(minutes=MASTER_LOCK_MINUTES)
                     ).isoformat(timespec="seconds")
        c.execute("UPDATE users SET failed=?, locked=?, locked_until=? WHERE id=?",
                  (failed, 1 if lock else 0, until, r["id"]))
        d = dict(base_detail)
        d["횟수"] = failed
        self._log(c, typ, r["username"], d)
        if lock:
            self._log(c, "account_locked", r["username"],
                      {"자동해제": "%d분 뒤" % MASTER_LOCK_MINUTES} if until else {})
        return lock

    def login(self, username, password) -> User:
        # 실패 기록(횟수·잠금·이력)이 롤백되지 않도록, 예외는 저장이 끝난 뒤에 던진다.
        uname = (username or "").strip().lower()
        err = None
        user = None
        with self._conn() as c:
            r = c.execute("SELECT * FROM users WHERE username=?", (uname,)).fetchone()
            usable = (r is not None and bool(r["active"]) and not r["deleted_at"]
                      and bool(r["pw_hash"]))
            ok = usable and verify_password(password or "", r["pw_hash"])
            if not usable:
                verify_password(password or "", self._dummy)    # 시간 차이로 계정 유무가 드러나지 않게
                known = r is not None
                self._log(c, "login_fail", uname if known else "(없는 아이디)",
                          {"reason": "비활성 또는 삭제" if known else "없는 아이디"})
                err = GENERIC_FAIL
            else:
                if r["locked"] and r["locked_until"] and now() >= r["locked_until"]:
                    c.execute("UPDATE users SET locked=0, failed=0, locked_until=NULL WHERE id=?",
                              (r["id"],))
                    self._log(c, "account_unlocked_auto", uname, {})
                    r = c.execute("SELECT * FROM users WHERE id=?", (r["id"],)).fetchone()
                if r["locked"]:
                    self._log(c, "login_fail", uname, {"reason": "잠김"})
                    err = "계정이 잠겼습니다. 마스터에게 문의하세요" if r["role"] != "master" \
                        else "계정이 잠겼습니다. %d분 뒤 다시 시도하세요" % MASTER_LOCK_MINUTES
                elif not ok:
                    if self._record_failure(c, r, "login_fail", {"reason": "비밀번호 불일치"}):
                        err = "로그인 %d회 실패로 계정이 잠겼습니다" % MAX_FAILED
                    else:
                        err = GENERIC_FAIL
                else:
                    c.execute("UPDATE users SET failed=0 WHERE id=?", (r["id"],))
                    ts = now()
                    self._log(c, "login_ok", uname, {"role": r["role"], "name": r["display_name"],
                                                      "소속": ORG_LABEL.get(r["org"], r["org"])})
                    user = self._user(r, ts)
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
                if self._record_failure(c, r, "password_change_fail", {}):
                    err = "실패 %d회로 계정이 잠겼습니다" % MAX_FAILED
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

    @staticmethod
    def _who(m):
        return {"실행자": m.display_name, "소속": ORG_LABEL.get(m.org, m.org)}

    def _can_manage(self, c, m, t, what):
        """타스코 마스터는 모두, 거래처 마스터는 작업자·관리자만 관리한다."""
        if t["role"] == "master":
            allowed = m.org == "tasco" and t["org"] == "customer" and t["id"] != m.id
        else:   # 작업자·관리자: 타스코 마스터는 모두, 거래처 마스터는 자기 소속만
            allowed = m.org == "tasco" or t["org"] == m.org
        if not allowed:
            d = {"작업": what, "대상": t["username"]}
            d.update(self._who(m))
            self._log(c, "permission_denied", m.username, d)
            raise AuthError("이 계정은 관리할 수 없습니다(권한 없음)")

    def create_user(self, actor, username, display_name, role, temp_password):
        if role not in ("worker", "manager", "master"):
            raise AuthError("역할이 올바르지 않습니다")
        username = self._check_username(username)
        check_policy(temp_password, username)
        if not display_name.strip():
            raise AuthError("이름을 입력하세요")
        with self._conn(keep=True) as c:
            m = self._require_master(c, actor)
            org = m.org
            if role == "master":
                if m.org != "tasco":
                    d = {"작업": "마스터 추가"}
                    d.update(self._who(m))
                    self._log(c, "permission_denied", m.username, d)
                    raise AuthError("거래처 마스터는 타스코 마스터만 만들 수 있습니다")
                if c.execute("SELECT 1 FROM users WHERE role='master' AND org='customer' "
                             "AND deleted_at IS NULL").fetchone():
                    raise AuthError("거래처 마스터는 1개만 만들 수 있습니다(먼저 기존 계정을 삭제하세요)")
                org = "customer"
            if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise AuthError("이미 사용했던 아이디입니다(삭제된 아이디도 다시 쓸 수 없습니다)")
            try:
                c.execute("INSERT INTO users(username,display_name,role,pw_hash,must_change,"
                          "created_at,org) VALUES(?,?,?,?,1,?,?)",
                          (username, display_name.strip(), role, hash_password(temp_password),
                           now(), org if role == "master" else m.org))
            except sqlite3.IntegrityError:
                raise AuthError("거래처 마스터는 1개만 만들 수 있습니다")
            label = "마스터(%s)" % ORG_LABEL[org] if role == "master" else ROLE_LABEL[role]
            d = {"대상": username, "이름": display_name.strip(), "역할": label}
            d.update(self._who(m))
            self._log(c, "user_created", m.username, d)

    def _target(self, c, user_id):
        t = c.execute("SELECT * FROM users WHERE id=? AND deleted_at IS NULL", (user_id,)).fetchone()
        if not t:
            raise AuthError("계정을 찾을 수 없습니다")
        return t

    def _guard_last_master(self, c, t, what):
        if t["role"] == "master" and t["org"] == "tasco":
            n = c.execute("SELECT COUNT(*) FROM users WHERE role='master' AND org='tasco' "
                          "AND active=1 AND deleted_at IS NULL AND id<>?", (t["id"],)).fetchone()[0]
            if n == 0:
                raise AuthError("마지막 타스코 마스터 계정은 %s할 수 없습니다" % what)

    def set_active(self, actor, user_id, active):
        with self._conn(keep=True) as c:
            m = self._require_master(c, actor)
            t = self._target(c, user_id)
            if not active and t["id"] == m.id:
                raise AuthError("자기 자신은 비활성화할 수 없습니다")
            self._can_manage(c, m, t, "활성화" if active else "비활성화")
            if not active:
                self._guard_last_master(c, t, "비활성화")
            c.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, t["id"]))
            d = {"대상": t["username"], "이름": t["display_name"],
                 "이전": "사용" if t["active"] else "비활성", "이후": "사용" if active else "비활성"}
            d.update(self._who(m))
            self._log(c, "user_activated" if active else "user_deactivated", m.username, d)

    def delete_user(self, actor, user_id):
        """삭제: 로그인 불가 + 목록에서 제거. 아이디와 과거 기록은 남긴다."""
        with self._conn(keep=True) as c:
            m = self._require_master(c, actor)
            t = self._target(c, user_id)
            if t["id"] == m.id:
                raise AuthError("자기 자신은 삭제할 수 없습니다")
            self._can_manage(c, m, t, "삭제")
            self._guard_last_master(c, t, "삭제")
            c.execute("UPDATE users SET deleted_at=?, active=0, pw_hash='' WHERE id=?",
                      (now(), t["id"]))
            lab = "마스터(%s)" % ORG_LABEL[t["org"]] if t["role"] == "master" else ROLE_LABEL[t["role"]]
            d = {"대상": t["username"], "이름": t["display_name"], "역할": lab}
            d.update(self._who(m))
            self._log(c, "user_deleted", m.username, d)

    def reset_password(self, actor, user_id, temp_password):
        with self._conn(keep=True) as c:
            m = self._require_master(c, actor)
            t = self._target(c, user_id)
            self._can_manage(c, m, t, "비밀번호 초기화")
            check_policy(temp_password, t["username"])
            c.execute("UPDATE users SET pw_hash=?, must_change=1, failed=0, locked=0, "
                      "locked_until=NULL WHERE id=?", (hash_password(temp_password), t["id"]))
            d = {"대상": t["username"], "이름": t["display_name"]}
            d.update(self._who(m))
            self._log(c, "password_reset", m.username, d)

    def unlock(self, actor, user_id):
        with self._conn(keep=True) as c:
            m = self._require_master(c, actor)
            t = self._target(c, user_id)
            self._can_manage(c, m, t, "잠금 해제")
            c.execute("UPDATE users SET failed=0, locked=0, locked_until=NULL WHERE id=?",
                      (t["id"],))
            d = {"대상": t["username"], "이름": t["display_name"]}
            d.update(self._who(m))
            self._log(c, "account_unlocked", m.username, d)

    # ---- CSV 내보내기 (마스터 전용) ----

    def export_work_csv(self, actor, path, since=None, until=None):
        """작업 로그(최종 결과만). since/until: 'YYYY-MM-DD' 문자열(포함)."""
        head = ["1차 접속시간", "2차 접속시간", "처리시각", "결과", "1차 담당자", "2차 담당자",
                "작업명", "작업 종류", "데이터", "불일치 횟수", "실패 사유", "1차 소속", "2차 소속",
                "주의", "수신 확인"]
        rows = []
        for e in reversed(self._events_between(actor, ("work_ok", "work_fail"), since, until)):
            d = json.loads(e["detail"])
            rows.append([d.get("1차접속", ""), d.get("2차접속", ""), e["ts"].replace("T", " "),
                         d.get("결과", ""),
                         "%s(%s)" % (d.get("1차이름", ""), d.get("1차", "")),
                         "없음" if d.get("2차", "없음") == "없음"
                         else "%s(%s%s)" % (d.get("2차이름", ""), d.get("2차", ""),
                                            ", " + d["2차역할"] if d.get("2차역할") else ""),
                         d.get("작업명", ""), d.get("작업종류", ""), d.get("데이터", ""),
                         d.get("불일치횟수", ""), d.get("사유", ""), d.get("1차소속", ""),
                         d.get("2차소속", ""), d.get("주의", ""), d.get("수신확인", "")])
        return self._write_csv(actor, path, head, rows, "작업 로그")

    def export_event_csv(self, actor, path, group="계정 변경", since=None, until=None):
        """로그인·계정 변경 기록. 마스터가 바꾼 내역(실행자·소속·이전/이후)이 내용에 들어간다."""
        types = EVENT_GROUPS[group]
        head = ["시각", "종류", "계정", "내용"]
        rows = [[e["ts"].replace("T", " "), EVENT_LABEL.get(e["type"], e["type"]), e["actor"],
                 format_detail(e["detail"], e["type"])]
                for e in reversed(self._events_between(actor, types, since, until))]
        return self._write_csv(actor, path, head, rows, "기록(%s)" % group)

    def _events_between(self, actor, types, since, until):
        with self._conn() as c:
            self._require_master(c, actor)
            q, args = "SELECT ts,type,actor,detail FROM events WHERE 1=1", []
            if types:
                q += " AND type IN (%s)" % ",".join("?" * len(types))
                args += list(types)
            if since:
                q += " AND ts >= ?"
                args.append(since)
            if until:
                q += " AND ts < ?"
                args.append((datetime.date.fromisoformat(until) + datetime.timedelta(days=1)
                             ).isoformat())
            return [dict(r) for r in c.execute(q + " ORDER BY id DESC", args)]

    def _write_csv(self, actor, path, head, rows, kind):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(head)
            for r in rows:
                w.writerow([_csv_safe(x) for x in r])
        with self._conn() as c:
            m = self._require_master(c, actor)
            d = {"종류": kind, "건수": len(rows)}
            d.update(self._who(m))
            self._log(c, "export", m.username, d)
        return len(rows)

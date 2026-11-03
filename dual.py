"""이중 입력: 1차(작업자) 입력은 메모리에만 두고, 2차(관리자)가 같은 내용을 입력해야 전송한다.

규칙(docs/설계-이중입력.md)
- 2차가 같은 1차 입력에 대해 MAX_TRIES(3)회 틀리면 1차 입력을 폐기한다(1차부터 다시).
- 1차 입력은 EXPIRE_SECONDS(10분)이 지나면 폐기한다.
- 작업자와 관리자는 다른 계정이어야 한다.
- 장비로 보내는 함수(run)는 일치 판정이 만든 1회용 승인표(Approval) 없이는 실행되지 않는다.
- 비교는 장비로 나갈 내용 기준: 작업 종류·작업 이름·할당 수·응답 시점·필드(이름순 정렬, 값은 한 글자도
  다르면 불일치). 필드 순서는 NGPCL에서 의미가 없으므로 비교에서 무시한다.
"""
import time
from collections import namedtuple

import ngpcl

MAX_TRIES = 3
EXPIRE_SECONDS = 600
KIND_LABEL = {"select": "작업 선택", "update": "필드 갱신", "update_print": "갱신 후 인쇄"}

Result = namedtuple("Result", "status tries left fields approval")
# status: match(일치, approval 있음) / retry(2차만 다시) / reset(3회 불일치, 1차 폐기) / expired(만료, 폐기)


class DualError(Exception):
    """화면에 그대로 보여줄 수 있는 한국어 메시지."""


class Request:
    def __init__(self, kind, job, alloc, fields, imaged=True):
        if kind not in KIND_LABEL:
            raise DualError("알 수 없는 작업 종류입니다")
        self.kind = kind
        self.job = (job or "").strip()
        self.alloc = (alloc or "").strip() or "0"
        self.imaged = bool(imaged)
        seen, flds = set(), []
        for name, value in fields:
            name = (name or "").strip()
            if not name:
                raise DualError("필드 이름이 비어 있습니다")
            if name in seen:
                raise DualError("같은 필드 이름이 두 번 있습니다: %s" % name)
            seen.add(name)
            flds.append((name, value or ""))
        if kind != "select" and not flds:
            raise DualError("갱신할 필드를 한 개 이상 입력하세요")
        self.fields = flds
        try:   # 구분자 등 장비로 보낼 수 없는 문자를 미리 걸러낸다
            self.message()
        except ngpcl.NgpclError as e:
            raise DualError(str(e))

    def message(self):
        build = ngpcl.job_select if self.kind == "select" else ngpcl.job_update
        return build(self.job, self.alloc, self.fields, self.imaged)

    def canonical(self):
        return (self.kind, self.job, self.alloc, self.imaged, tuple(sorted(self.fields)))

    def data_text(self):
        return "; ".join("%s=%s" % f for f in self.fields)

    def diff(self, other):
        """서로 다른 항목 이름(기록용 — 2차 화면에는 보여주지 않는다)."""
        out = []
        for label, a, b in (("작업 종류", self.kind, other.kind), ("작업 이름", self.job, other.job),
                            ("할당 수", self.alloc, other.alloc),
                            ("응답 시점", self.imaged, other.imaged)):
            if a != b:
                out.append(label)
        mine, theirs = dict(self.fields), dict(other.fields)
        for name in sorted(set(mine) | set(theirs)):
            if mine.get(name) != theirs.get(name):
                out.append("필드 " + name)
        return out


_MINT = object()


class Approval:
    """일치 판정이 발급하는 1회용 승인표. run() 은 이것 없이는 장비로 보내지 않는다."""

    def __init__(self, mint, request, worker, manager):
        if mint is not _MINT:
            raise DualError("승인표는 일치 판정으로만 만들 수 있습니다")
        self._request, self._used = request, False
        self.worker, self.manager = worker, manager

    def consume(self):
        if self._used:
            raise DualError("이미 사용한 승인표입니다")
        self._used = True
        return self._request


class DualEntry:
    def __init__(self, clock=time.time, max_tries=MAX_TRIES, expire=EXPIRE_SECONDS):
        self._clock, self.max_tries, self.expire = clock, max_tries, expire
        self._p = None

    # ---- 1차 ----
    def submit(self, worker, request):
        """1차 입력 접수. 이전에 대기 중이던 요청이 있으면 그 정보를 돌려준다(기록용)."""
        if worker.role != "worker":
            raise DualError("1차 입력은 작업자 계정만 할 수 있습니다")
        old = self.discard()
        self._p = {"worker": worker, "request": request, "tries": 0, "at": self._clock()}
        return old

    def has_pending(self):
        return self._p is not None

    def pending_worker(self):
        return self._p["worker"] if self._p else None

    def pending_kind(self):
        return self._p["request"].kind if self._p else None

    def tries(self):
        return self._p["tries"] if self._p else 0

    def seconds_left(self):
        if not self._p:
            return 0
        return max(0, int(self.expire - (self._clock() - self._p["at"])))

    def expired(self):
        return self._p is not None and self._clock() - self._p["at"] > self.expire

    def discard(self):
        """대기 중인 1차 입력을 버리고 그 정보를 돌려준다(없으면 None)."""
        p, self._p = self._p, None
        return p

    # ---- 2차 ----
    def verify(self, manager, request):
        p = self._p
        if p is None:
            raise DualError("확인할 1차 입력이 없습니다")
        if manager.role != "manager":
            raise DualError("2차 확인은 관리자 계정만 할 수 있습니다")
        if manager.id == p["worker"].id:
            raise DualError("1차 작업자와 2차 관리자는 서로 다른 계정이어야 합니다")
        if self.expired():
            self._p = None
            return Result("expired", p["tries"], 0, [], None)
        if request.canonical() == p["request"].canonical():
            self._p = None
            return Result("match", p["tries"], 0, [],
                          Approval(_MINT, p["request"], p["worker"], manager))
        p["tries"] += 1
        fields = p["request"].diff(request)
        if p["tries"] >= self.max_tries:
            self._p = None
            return Result("reset", p["tries"], 0, fields, None)
        return Result("retry", p["tries"], self.max_tries - p["tries"], fields, None)


def run(client, approval, xchg):
    """승인표를 소모해 장비로 보낸다. xchg(메시지, 대기초) -> 응답 문자열. (성공 여부, 실패 사유)"""
    req = approval.consume()
    wait = 60.0 if req.imaged else 10.0

    def ok(reply):
        return len(reply) >= 4 and reply[3] == "0"
    try:
        r = xchg(req.message(), wait)
        if not ok(r):
            return False, "장비 응답 %s" % r
        if req.kind == "update_print":
            r = xchg(ngpcl.print_status("", True), 60.0)
            _, st, f = ngpcl.parse_message(r, client.status_on)
            code = f[0] if f else st
            if code != "0":
                return False, "인쇄할 수 없는 상태(코드 %s: 1=상태 부적합, 2=인쇄 중, 3=오류)" % code
            r = xchg(ngpcl.print_go("", 0, 1), 10.0)
            if not ok(r):
                return False, "장비 응답 %s" % r
    except ngpcl.NgpclError as e:
        return False, "장비 오류: %s" % e
    return True, ""

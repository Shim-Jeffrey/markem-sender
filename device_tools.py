"""장비 조회·설정 도구: 필드 조회(`~FR`), 설정 조회(`~SR`), 설정 변경(`~SC`).

- 통신 모듈(ngpcl.py)은 고치지 않고 빌더·해석 함수만 쓴다.
- 장비 연결에 의존하지 않고 전송 함수 xchg(메시지, 대기초) -> 응답 문자열만 받는다.
  (여러 대 확장 시 장비마다 다른 xchg 를 넘기면 된다)
"""
import ngpcl
import verify


# Client.send 가 장비로 내보내기 전에 내는 오류 문구(ngpcl.py): 연결 없음, ASCII 로 못 보내는 글자
_NOT_SENT = ("연결되어 있지 않습니다", "보낼 수 없는 글자")


class ToolError(Exception):
    """화면에 그대로 보여줄 수 있는 한국어 메시지."""


def query_fields(xchg, job="", name="", status_on=True, timeout=10.0):
    """`~FR` 필드 조회. name 이 비면 작업의 전체 필드. [(필드 이름, 값)] 을 돌려준다(없으면 빈 목록)."""
    reply = xchg(ngpcl.simple("~FR", job, name), timeout)
    ident, status, f = ngpcl.parse_message(reply, status_on)
    if ident == "~NK":
        raise ToolError("장비가 조회를 거부했습니다(NAK)")
    if ident != "~FC":
        raise ToolError("예상하지 못한 응답: %s" % reply)
    if status == "1" or not f or not f[0]:
        return []
    return list(zip(f[0::2], f[1::2]))


def get_setting(xchg, name, status_on=True, timeout=10.0):
    """`~SR` 설정 조회 → 값. 장비가 설정을 모르면 ToolError."""
    if not name:
        raise ToolError("설정 이름을 입력하세요")
    reply = xchg(ngpcl.simple("~SR", name), timeout)
    ident, status, f = ngpcl.parse_message(reply, status_on)
    if ident == "~NK":
        raise ToolError("장비가 조회를 거부했습니다(NAK)")
    if ident != "~SV" or status == "1" or len(f) < 2 or not f[0]:
        raise ToolError("장비에서 설정 '%s'을(를) 찾지 못했습니다(이름 확인)" % name)
    if f[0] != name:
        raise ToolError("응답의 설정 이름이 다릅니다: %s" % f[0])
    return f[1]


def set_setting(xchg, name, value, status_on=True, timeout=10.0):
    """`~SC` 설정 변경 후 `~SR` 로 되읽어 확인한다.

    (결과, 설명, 이전 값, 되읽은 값, 신호) 를 돌려준다. 결과: True 성공 / False 장비가 거부 /
    None 보냈지만 응답을 확인하지 못함(장비에는 반영됐을 수 있음). 통신 오류는 밖으로 던지지 않는다.
    """
    if not name:
        raise ToolError("설정 이름을 입력하세요")
    try:
        ngpcl.simple("~SC", name, value)    # 구분자 등 보낼 수 없는 문자는 통신 전에 거른다
    except ngpcl.NgpclError as e:
        raise ToolError(str(e))
    try:
        before = get_setting(xchg, name, status_on, timeout)
    except ToolError:
        before = ""                     # 이전 값을 못 읽어도(이름 모름 등) 변경은 시도한다
    except ngpcl.NgpclError as e:       # 통신이 불안정하면 보내지 않고 끝낸다
        return False, "전송 전 연결 오류 — 설정을 변경하지 않았습니다: %s" % e, "", "", verify.RED
    try:
        reply = xchg(ngpcl.simple("~SC", name, value), timeout)
    except ngpcl.NgpclError as e:
        if "사용할 수 없는 문자" in str(e):        # 보내기 전에 막힌 입력
            raise ToolError(str(e))
        if any(k in str(e) for k in _NOT_SENT):   # 장비로 나가기 전에 실패
            return False, "전송하지 못했습니다 — 설정을 변경하지 않았습니다: %s" % e, \
                before, "", verify.RED
        return (None, "변경 응답을 확인하지 못했습니다(장비에는 반영됐을 수 있음): %s" % e,
                before, "", verify.YELLOW)
    if len(reply) < 4 or reply[:3] != "~SC" or reply[3] != "0":
        return (False, "장비가 변경을 거부했습니다(응답 %s) — 인쇄 중이거나 값이 맞지 않을 수 있습니다" % reply,
                before, "", verify.RED)
    try:
        after = get_setting(xchg, name, status_on, timeout)
    except (ToolError, ngpcl.NgpclError) as e:
        return True, "변경 응답은 성공, 되읽기 실패: %s" % e, before, "", verify.YELLOW
    light, note = verify.judge(value, after)
    return True, note or "변경되었습니다", before, after, light

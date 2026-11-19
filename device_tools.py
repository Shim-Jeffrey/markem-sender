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


PRODUCING = "06"        # PackML 상태 코드: Producing (ngpcl.PACKML_STATE, 매뉴얼 6장·예제 ~DS …|06|)


def _device_state(xchg, status_on, timeout):
    """`~DR` 로 (생산 중 여부, 상태 코드)를 읽는다."""
    reply = xchg(ngpcl.simple("~DR", ""), timeout)
    ident, status, f = ngpcl.parse_message(reply, status_on)
    if ident != "~DS" or status == "1" or len(f) < 8:
        raise ToolError("장비 상태 응답을 해석하지 못했습니다: %s" % reply)
    return f[0] == "1", f[7]


READY = "04"           # PackML 상태 코드: Ready(준비). 정지(Held) 뒤에는 Ready 를 거쳐야 다시 Producing 으로 갈 수 있다


def _wait_state(xchg, status_on, timeout, tries, pause, sleep, want):
    """`~DR` 로 상태 코드가 want(코드 모음)에 들어갈 때까지 최대 tries 번 확인한다. 마지막 (생산 여부, 코드)를 돌려준다."""
    producing, code = False, ""
    for i in range(tries):
        producing, code = _device_state(xchg, status_on, timeout)
        if code in want:
            break
        if i < tries - 1:
            sleep(pause)
    return producing, code


def start_producing(xchg, status_on=True, timeout=10.0, tries=5, pause=1.0, sleep=None):
    """작업 시작: 장비를 Producing(준비·인쇄 대기) 상태로 보낸다(`~ST|06|`) 후 `~DR` 로 확인한다.

    (성공 여부, 설명, 신호)를 돌려준다. 통신 오류는 밖으로 던지지 않는다.
    이미 생산 중이면(생산 플래그와 상태 코드 06 이 모두 맞을 때) 보내지 않고 성공으로 본다.
    정지(Held/Holding) 상태이면 먼저 Ready(`~ST|04|`)로 되돌린 뒤 Producing 으로 보낸다
    (정지 상태에서 바로 06 을 보내면 장비가 거부하기 때문). 상태가 바뀌는 데 시간이 걸릴 수 있어 `tries` 번까지 확인한다.
    """
    import time
    sleep = sleep or time.sleep
    try:
        producing, code = _device_state(xchg, status_on, timeout)
        if producing and code == PRODUCING:
            return True, "이미 작업 중(Producing) 상태입니다", verify.GREEN
        if code in HELD:      # 정지 상태: Ready 로 먼저 복귀
            reply = xchg(ngpcl.simple("~ST", "", READY), timeout)
            if len(reply) < 4 or reply[:3] != "~ST" or reply[3] != "0":
                return False, "장비가 준비(Ready) 전환을 거부했습니다(응답 %s). 프린터 화면의 상태를 확인하세요" % reply, verify.RED
            producing, code = _wait_state(xchg, status_on, timeout, tries, pause, sleep, (READY, PRODUCING))
            if code not in (READY, PRODUCING):
                name = ngpcl.PACKML_STATE.get(code, code)
                return False, "정지 상태에서 준비(Ready)로 바뀌지 않았습니다(현재 %s)" % name, verify.YELLOW
            if producing and code == PRODUCING:
                return True, "작업 시작됨(Producing)", verify.GREEN
        reply = xchg(ngpcl.simple("~ST", "", PRODUCING), timeout)
        if len(reply) < 4 or reply[:3] != "~ST" or reply[3] != "0":
            return False, "장비가 작업 시작을 거부했습니다(응답 %s). 프린터가 오류·경고 상태이거나 지금 시작할 수 없는 상태일 수 있습니다. 프린터 화면을 확인하세요" % reply, verify.RED
        producing, code = _wait_state(xchg, status_on, timeout, tries, pause, sleep, (PRODUCING,))
        if producing and code == PRODUCING:
            return True, "작업 시작됨(Producing)", verify.GREEN
        name = ngpcl.PACKML_STATE.get(code, code)
        return False, "작업 시작 명령은 받았지만 상태가 Producing 으로 바뀌지 않았습니다(현재 %s)" % name,             verify.YELLOW
    except (ToolError, ngpcl.NgpclError) as e:
        return False, "작업 시작 확인 실패: %s" % e, verify.RED


HELD = ("10", "11")      # PackML 상태 코드: 10 Holding(정지 중), 11 Held(정지됨) — 매뉴얼상 SmartDate 가 받는 목표 상태는 Ready·Producing·Held
STOP_TARGET = "11"


def stop_printing(xchg, status_on=True, timeout=10.0, tries=5, pause=1.0, sleep=None):
    """인쇄 정지: 장비를 Held(정지) 상태로 보낸다(`~ST|11|`) 후 `~DR` 로 확인한다.

    (성공 여부, 설명, 신호)를 돌려준다. 이미 정지(Holding/Held)면 보내지 않는다. 통신 오류는 던지지 않는다.
    """
    import time
    sleep = sleep or time.sleep
    try:
        producing, code = _device_state(xchg, status_on, timeout)
        if code in HELD:
            return True, "이미 정지 상태입니다", verify.GREEN
        reply = xchg(ngpcl.simple("~ST", "", STOP_TARGET), timeout)
        if len(reply) < 4 or reply[:3] != "~ST" or reply[3] != "0":
            return False, "장비가 정지 명령을 거부했습니다(응답 %s). 프린터 화면의 상태를 확인하세요" % reply, verify.RED
        for i in range(tries):
            producing, code = _device_state(xchg, status_on, timeout)
            if code in HELD:
                return True, "정지됨", verify.GREEN
            if i < tries - 1:
                sleep(pause)
        name = ngpcl.PACKML_STATE.get(code, code)
        return False, "정지 명령은 받았지만 상태가 정지로 바뀌지 않았습니다(현재 %s)" % name, verify.YELLOW
    except (ToolError, ngpcl.NgpclError) as e:
        return False, "정지 확인 실패: %s" % e, verify.RED

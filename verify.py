"""수신 확인: 장비로 보낸 필드 값을 `~FR`(필드 조회)로 되읽어 신호등으로 판정한다.

- 통신 모듈(ngpcl.py)은 고치지 않고, 이미 있는 빌더·해석 함수만 쓴다.
- 판정: GREEN(보낸 값과 같음) / YELLOW(되읽기 실패·대소문자·공백 차이) / RED(값이 다름)
- 되읽기로 확인되는 것은 "장비 작업의 필드 값이 바뀜"까지이며 실제 인쇄 결과 확인은 아니다.
"""
from collections import namedtuple

import ngpcl

GREEN, YELLOW, RED = "green", "yellow", "red"
LABEL = {GREEN: "일치", YELLOW: "확인 필요", RED: "불일치"}
SUMMARY = {GREEN: "초록(전체 일치)", YELLOW: "노랑(확인 필요)", RED: "빨강(불일치)"}

Check = namedtuple("Check", "name sent got light note")


def _strip(v):
    return (v or "").replace("\0", "").strip()


def _loose(v):
    return " ".join(_strip(v).split()).casefold()


def judge(sent, got):
    """보낸 값과 장비가 돌려준 값을 비교해 (신호, 설명)."""
    if _strip(sent) == _strip(got):
        return GREEN, ""
    if _loose(sent) == _loose(got):
        return YELLOW, "대소문자·공백 차이"
    return RED, "값이 다름"


def read_back(xchg, job, fields, status_on=True, timeout=10.0):
    """필드마다 `~FR` 로 되읽는다. xchg(메시지, 대기초) -> 응답 문자열(기존 전송과 같은 함수).

    통신 오류는 그 필드와 남은 필드를 YELLOW(되읽기 실패)로 두고 끝낸다.
    """
    out = []
    broken = None
    for name, sent in fields:
        if broken:
            out.append(Check(name, sent, "", YELLOW, broken))
            continue
        try:
            reply = xchg(ngpcl.simple("~FR", job, name), timeout)
            ident, status, f = ngpcl.parse_message(reply, status_on)
        except Exception as e:     # 통신 오류뿐 아니라 예기치 못한 오류도 노랑으로 처리
            broken = "되읽기 실패: %s" % e
            out.append(Check(name, sent, "", YELLOW, broken))
            continue
        if ident == "~NK":
            out.append(Check(name, sent, "", YELLOW, "장비가 조회를 거부함(NAK)"))
        elif ident != "~FC" or status == "1" or len(f) < 2 or not f[0]:
            out.append(Check(name, sent, "", YELLOW, "장비에서 필드를 찾지 못함(이름 확인)"))
        elif f[0] != name:
            out.append(Check(name, sent, f[1], YELLOW, "응답 필드 이름이 다름: %s" % f[0]))
        else:
            light, note = judge(sent, f[1])
            out.append(Check(name, sent, f[1], light, note))
    return out


def overall(checks):
    lights = {c.light for c in checks}
    if RED in lights:
        return RED
    if YELLOW in lights:
        return YELLOW
    return GREEN


def summary(checks):
    """작업 기록에 남길 한 줄."""
    if not checks:
        return ""
    bad = ["%s(%s)" % (c.name, LABEL[c.light]) for c in checks if c.light != GREEN]
    text = SUMMARY[overall(checks)]
    return text + (": " + ", ".join(bad) if bad else "")

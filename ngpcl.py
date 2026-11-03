"""NGPCL(Markem NextGen PCL) 통신 모듈. 근거: NGPCL Users Guide v25.

- 이더넷: TCP, 포트 21000 (장비와 노트북 모두 고정 IP)
- 패킷: 시작 + 메시지 + 끝. 기본(Printable) '{' '}', Traditional STX(2) ETX(3)
- 체크필드(옵션): 메시지 문자값 합 mod 1000, 3자리. 형식 '|' + 3자리
"""
import socket

PORT = 21000

CHARSETS = {
    "printable": ("{", "}"),
    "traditional": ("\x02", "\x03"),
}

# PackML 상태 코드 (6장)
PACKML_STATE = {
    "00": "Undefined", "01": "Off", "02": "Stopped", "03": "Starting",
    "04": "Ready", "05": "Standby", "06": "Producing", "07": "Stopping",
    "08": "Aborting", "09": "Aborted", "10": "Holding", "11": "Held",
}
PACKML_MODE = {"0": "Undefined", "1": "Automatic", "2": "Semi-Automatic",
               "3": "Manual", "4": "Idle"}
JOB_RESULT = {"0": "수신 없음", "1": "처리 중", "2": "완료", "3": "실패(할당 진행 중)",
              "4": "실패(파일 없음)"}


class NgpclError(Exception):
    pass


def checkfield(message: str) -> str:
    return "%03d" % (sum(ord(c) for c in message) % 1000)


def build_packet(message: str, charset="printable", use_check=False) -> str:
    start, end = CHARSETS[charset]
    if use_check:
        message = message + "|" + checkfield(message)
    return start + message + end


def clean(value: str) -> str:
    """필드 값에는 구분자·패킷 문자가 들어갈 수 없다(설계서 4장)."""
    for bad in ("|", "{", "}", "\x02", "\x03", "~"):
        if bad in value:
            raise NgpclError("값에 사용할 수 없는 문자가 있습니다: %r" % bad)
    return value


# ---- 메시지 만들기 (7장: 값은 모두 '|' 로 끝난다) ----

def job_select(job, allocation="0", fields=(), reply_imaged=True):
    m = "~JS%d|%s|%s|" % (1 if reply_imaged else 0, clean(job), clean(allocation))
    for k, v in fields:
        m += "%s|%s|" % (clean(k), clean(v))
    return m


def job_update(job, allocation="0", fields=(), reply_imaged=True):
    m = "~JU%d|%s|%s|" % (1 if reply_imaged else 0, clean(job), clean(allocation))
    for k, v in fields:
        m += "%s|%s|" % (clean(k), clean(v))
    return m


def print_go(line="", action=0, reply=1):
    return "~PG|%s|%d|%d|" % (clean(line), action, reply)


def print_status(line="", wait_ready=False):
    return "~PS|%s|%d|" % (clean(line), 1 if wait_ready else 0)


def state_change(line, code):
    return "~ST|%s|%s|" % (clean(line), code)


def simple(ident, *body):
    """요청류: ~JR|, ~DR|, ~CR|, ~VR, ~RC, ~PV|, ~AC| ... """
    return ident + "".join("%s|" % clean(b) for b in body)


# ---- 응답 해석 ----

def parse_message(text: str, status_on=True):
    """'~DS0|1|0|...|' -> (식별자, 상태, 필드목록). 상태 필드가 꺼져 있으면 상태는 None."""
    if not text.startswith("~") or len(text) < 3:
        raise NgpclError("해석할 수 없는 응답: %r" % text)
    ident, rest = text[:3], text[3:]
    if ident == "~NK":
        return ident, "1", []
    status = None
    if status_on and rest:
        status, rest = rest[0], rest[1:]
        rest = rest[1:] if rest.startswith("|") else rest
    fields = rest.split("|")
    if fields and fields[-1] == "":
        fields.pop()
    return ident, status, fields


class Client:
    """장비와 TCP 로 연결해 한 번에 한 메시지씩 주고받는다(링크는 장비당 1개)."""

    def __init__(self):
        self.sock = None
        self.charset = "printable"
        self.use_check = False
        self.status_on = True
        self.unicode = False
        self._buf = ""

    @property
    def connected(self):
        return self.sock is not None

    def connect(self, host, port=PORT, timeout=5.0):
        self.close()
        try:
            self.sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as e:
            self.sock = None
            raise NgpclError("연결 실패: %s" % e)
        self._buf = b"" if self.unicode else ""

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def _enc(self):
        return "utf-16-le" if self.unicode else "ascii"

    def send(self, message: str):
        """패킷으로 만들어 보내고, 실제로 나간 문자열을 돌려준다(로그용)."""
        if not self.sock:
            raise NgpclError("연결되어 있지 않습니다")
        packet = build_packet(message, self.charset, self.use_check)
        try:
            self.sock.sendall(packet.encode(self._enc()))
        except UnicodeEncodeError:
            raise NgpclError("현재 문자 설정(ASCII)으로 보낼 수 없는 글자가 있습니다. 유니코드를 켜세요")
        except OSError as e:
            self.close()
            raise NgpclError("전송 실패: %s" % e)
        return packet

    def receive(self, timeout=10.0):
        """패킷 끝 문자까지 읽어 메시지 문자열(체크필드 확인 후 제거)을 돌려준다."""
        if not self.sock:
            raise NgpclError("연결되어 있지 않습니다")
        start, end = CHARSETS[self.charset]
        self.sock.settimeout(timeout)
        width = 2 if self.unicode else 1
        raw = self._buf if isinstance(self._buf, bytes) else b""
        endb = end.encode(self._enc())
        while True:
            i = raw.find(endb)
            if i >= 0 and (i % width == 0):
                break
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                self._buf = raw
                raise NgpclError("응답 시간 초과(%.0f초)" % timeout)
            except OSError as e:
                self.close()
                raise NgpclError("수신 실패: %s" % e)
            if not chunk:
                self.close()
                raise NgpclError("장비가 연결을 닫았습니다")
            raw += chunk
        self._buf = raw[i + len(endb):]
        text = raw[:i + len(endb)].decode(self._enc(), "replace")
        s = text.find(start)
        text = text[s + 1:-1] if s >= 0 else text[:-1]
        if self.use_check:
            body, sep, chk = text.rpartition("|")
            if not sep or chk != checkfield(body):
                raise NgpclError("응답 체크필드 불일치: %r" % text)
            text = body
        return text

    def request(self, message: str, timeout=10.0):
        """보내고 응답 한 개를 받아 (보낸 패킷, 받은 메시지) 로 돌려준다."""
        sent = self.send(message)
        return sent, self.receive(timeout)

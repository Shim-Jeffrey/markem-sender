"""NGPCL 가짜 마켐 장비. 실제 장비 없이 화면·통신을 시험하는 용도.

실행: python sim_printer.py [포트]   (기본 21000)
받은 명령에 설계서(NGPCL Users Guide v25) 형식으로 답한다. 상태 필드는 켜져 있다고 가정한다.
"""
import socket
import sys
import threading

import ngpcl

STATE = {"job": "", "fields": {}, "state": "06", "total": 0, "batch": 0, "ready": True,
         "settings": {"darkness": "50"}}


def reply(msg):
    return msg


def handle(text):
    ident = text[:3]
    rest = text[3:]
    f = [x for x in rest.split("|")]
    if f and f[-1] == "":
        f.pop()
    if ident in ("~JS", "~JU"):
        job = f[1] if len(f) > 1 else ""
        if ident == "~JS":
            STATE["job"] = job
            STATE["fields"] = {}
        for i in range(3, len(f) - 1, 2):
            STATE["fields"][f[i]] = f[i + 1]
        return ident + "0|"
    if ident == "~JR":
        return "~JN0|%s|" % STATE["job"]
    if ident == "~FR":
        name = f[1] if len(f) > 1 else ""
        if name:
            return "~FC0|%s|%s|" % (name, STATE["fields"].get(name, ""))
        return "~FC0|" + "".join("%s|%s|" % kv for kv in STATE["fields"].items())
    if ident == "~DR":
        return "~DS0|%d|0|0|%d|2|0|000000000|%s|0|" % (
            STATE["state"] == "06", STATE["ready"], STATE["state"])
    if ident == "~DC":
        return "~DV0|0|"
    if ident == "~PS":
        return "~PR0|0|"
    if ident == "~PG":
        STATE["total"] += 1
        STATE["batch"] += 1
        return "~PG0|"
    if ident == "~ST":
        STATE["state"] = f[1] if len(f) > 1 else STATE["state"]
        return "~ST0|"
    if ident == "~CR":
        return "~CV0|TOTAL|%09d|BATCH|%09d|TFAIL|000000000|BFAIL|000000000|" % (
            STATE["total"], STATE["batch"])
    if ident == "~SC":
        STATE["settings"][f[0]] = f[1]
        return "~SC0|"
    if ident == "~SR":
        return "~SV0|%s|%s|" % (f[0], STATE["settings"].get(f[0], ""))
    if ident == "~VR":
        return "~VV0|001|001|001|"
    if ident == "~PV":
        return "~VP0|2.0|"
    if ident == "~RC":
        return "~CC0|10:25:40 04/12/2003|"
    return "~NK1|"


def serve_client(conn, addr, charset, use_check):
    start, end = ngpcl.CHARSETS[charset]
    buf = ""
    print("연결:", addr)
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buf += data.decode("ascii", "replace")
            while end in buf:
                packet, buf = buf.split(end, 1)
                text = packet[packet.find(start) + 1:]
                print("<-", repr(start + text + end))
                if use_check:
                    body, _, chk = text.rpartition("|")
                    if chk != ngpcl.checkfield(body):
                        out = "~NK1|"
                    else:
                        out = handle(body)
                else:
                    out = handle(text)
                resp = ngpcl.build_packet(out, charset, use_check)
                print("->", repr(resp))
                conn.sendall(resp.encode("ascii"))
    finally:
        conn.close()
        print("종료:", addr)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else ngpcl.PORT
    charset = "traditional" if "--stx" in sys.argv else "printable"
    use_check = "--check" in sys.argv
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    print("시뮬레이터 대기 중 127.0.0.1:%d (%s%s)" % (port, charset, ", 체크필드" if use_check else ""))
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=serve_client, args=(conn, addr, charset, use_check),
                         daemon=True).start()


if __name__ == "__main__":
    main()

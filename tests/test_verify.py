import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import dual
import ngpcl
import sim_printer
import verify as V


def fake(replies):
    """보낸 메시지를 기록하고 replies(딕셔너리: 필드→응답)로 답하는 xchg."""
    sent = []

    def xchg(msg, timeout):
        sent.append(msg)
        name = msg.split("|")[1] if msg.startswith("~FR") else ""
        r = replies[name]
        if isinstance(r, Exception):
            raise r
        return r
    xchg.sent = sent
    return xchg


class JudgeTest(unittest.TestCase):
    def test_same_is_green(self):
        self.assertEqual(V.judge("AAA", "AAA")[0], V.GREEN)

    def test_null_and_space_padding_is_green(self):
        self.assertEqual(V.judge("AAA", "AAA\0\0 ")[0], V.GREEN)

    def test_case_or_inner_space_is_yellow(self):
        self.assertEqual(V.judge("AAA", "aaa")[0], V.YELLOW)
        self.assertEqual(V.judge("A  B", "A B")[0], V.YELLOW)

    def test_different_is_red(self):
        self.assertEqual(V.judge("AAA", "AAB")[0], V.RED)


class ReadBackTest(unittest.TestCase):
    def test_message_format_and_green(self):
        x = fake({"LOT": "~FC0|LOT|AAA|", "EXP": "~FC0|EXP|20281031|"})
        cs = V.read_back(x, "", [("LOT", "AAA"), ("EXP", "20281031")], True)
        self.assertEqual(x.sent, ["~FR|LOT|", "~FR|EXP|"])      # 작업 이름 비움 = 현재 작업
        self.assertEqual(V.overall(cs), V.GREEN)
        self.assertEqual(V.summary(cs), "초록(전체 일치)")

    def test_job_name_included(self):
        x = fake({"LOT": "~FC0|LOT|AAA|"})
        V.read_back(x, "J1", [("LOT", "AAA")], True)
        self.assertEqual(x.sent, ["~FRJ1|LOT|"])

    def test_status_off(self):
        x = fake({"LOT": "~FCLOT|AAA|"})
        self.assertEqual(V.overall(V.read_back(x, "", [("LOT", "AAA")], False)), V.GREEN)

    def test_mismatch_is_red_and_named(self):
        x = fake({"LOT": "~FC0|LOT|AAB|", "EXP": "~FC0|EXP|1|"})
        cs = V.read_back(x, "", [("LOT", "AAA"), ("EXP", "1")], True)
        self.assertEqual(V.overall(cs), V.RED)
        self.assertEqual(V.summary(cs), "빨강(불일치): LOT(불일치)")

    def test_missing_field_is_yellow(self):
        x = fake({"LOT": "~FC1|"})
        cs = V.read_back(x, "", [("LOT", "AAA")], True)
        self.assertEqual(cs[0].light, V.YELLOW)

    def test_nak_is_yellow(self):
        x = fake({"LOT": "~NK1|"})
        self.assertEqual(V.read_back(x, "", [("LOT", "AAA")], True)[0].light, V.YELLOW)

    def test_error_stops_and_marks_rest_yellow(self):
        x = fake({"A": ngpcl.NgpclError("응답 시간 초과"), "B": "~FC0|B|1|"})
        cs = V.read_back(x, "", [("A", "1"), ("B", "1")], True)
        self.assertEqual([c.light for c in cs], [V.YELLOW, V.YELLOW])
        self.assertEqual(len(x.sent), 1)                         # 끊긴 뒤에는 더 보내지 않는다

    def test_empty_checks_summary(self):
        self.assertEqual(V.summary([]), "")


class SimulatorEndToEnd(unittest.TestCase):
    """가짜 장비에 실제 TCP 로 보내고(dual.run) 되읽는다 — 통신 모듈은 그대로 사용."""

    def test_send_then_read_back_green(self):
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        sim_printer.STATE.update({"job": "", "fields": {}})

        def serve():
            conn, addr = srv.accept()
            sim_printer.serve_client(conn, addr, "printable", False)
        threading.Thread(target=serve, daemon=True).start()

        c = ngpcl.Client()
        c.connect("127.0.0.1", port)
        try:
            req = dual.Request("update", "", "0", [("LOT", "AAA"), ("EXP", "20281031")], True)
            appr = dual.Approval(dual._MINT, req, None, None)
            xchg = lambda m, t: c.request(m, t)[1]
            ok, why = dual.run(c, appr, xchg)
            self.assertTrue(ok, why)
            cs = V.read_back(xchg, req.job, req.fields, c.status_on)
            self.assertEqual(V.overall(cs), V.GREEN, cs)
            self.assertEqual([x.got for x in cs], ["AAA", "20281031"])
        finally:
            c.close()
            srv.close()


if __name__ == "__main__":
    unittest.main()

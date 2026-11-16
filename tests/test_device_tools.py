import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import device_tools as T
import ngpcl
import sim_printer
import verify as V


def fake(table):
    sent = []

    def xchg(msg, timeout):
        sent.append(msg)
        r = table[msg]
        if isinstance(r, Exception):
            raise r
        return r
    xchg.sent = sent
    return xchg


class QueryFieldsTest(unittest.TestCase):
    def test_one_field_message_matches_manual(self):
        x = fake({"~FR|Field1|": "~FC0|Field1|Value1|"})
        self.assertEqual(T.query_fields(x, "", "Field1"), [("Field1", "Value1")])
        self.assertEqual(x.sent, ["~FR|Field1|"])          # 매뉴얼 5.6 예제

    def test_all_fields_message_matches_manual(self):
        x = fake({"~FR||": "~FC0|A|1|B|2|", "~FRJob1||": "~FC0|A|1|"})
        self.assertEqual(T.query_fields(x, "", ""), [("A", "1"), ("B", "2")])
        self.assertEqual(T.query_fields(x, "Job1", ""), [("A", "1")])
        self.assertEqual(x.sent, ["~FR||", "~FRJob1||"])   # 매뉴얼 5.6 예제

    def test_not_found_is_empty(self):
        self.assertEqual(T.query_fields(fake({"~FR|X|": "~FC1|"}), "", "X"), [])
        self.assertEqual(T.query_fields(fake({"~FR|X|": "~FC|"}), "", "X", False), [])

    def test_nak(self):
        with self.assertRaises(T.ToolError):
            T.query_fields(fake({"~FR|X|": "~NK1|"}), "", "X")


class SettingTest(unittest.TestCase):
    def test_get(self):
        self.assertEqual(T.get_setting(fake({"~SRdarkness|": "~SV0|darkness|90|"}), "darkness"), "90")

    def test_get_unknown(self):
        with self.assertRaises(T.ToolError):
            T.get_setting(fake({"~SRnope|": "~SV1|"}), "nope")

    def test_set_message_matches_manual_and_confirms(self):
        x = fake({"~SRdarkness|": "~SV0|darkness|50|", "~SCdarkness|90|": "~SC0|"})
        # 되읽기 응답을 바꿔 변경 후 값 90 을 돌려준다
        seq = iter(["~SV0|darkness|50|", "~SC0|", "~SV0|darkness|90|"])
        sent = []

        def xchg(msg, timeout):
            sent.append(msg)
            return next(seq)
        ok, note, before, after, light = T.set_setting(xchg, "darkness", "90")
        self.assertEqual(sent, ["~SRdarkness|", "~SCdarkness|90|", "~SRdarkness|"])  # 5.16 예제
        self.assertTrue(ok)
        self.assertEqual((before, after, light), ("50", "90", V.GREEN))

    def test_set_rejected_is_red(self):
        seq = iter(["~SV0|darkness|50|", "~SC1|"])
        ok, note, before, after, light = T.set_setting(lambda m, t: next(seq), "darkness", "90")
        self.assertFalse(ok)
        self.assertEqual(light, V.RED)

    def test_set_but_value_differs_is_red(self):
        seq = iter(["~SV0|darkness|50|", "~SC0|", "~SV0|darkness|50|"])
        ok, note, before, after, light = T.set_setting(lambda m, t: next(seq), "darkness", "90")
        self.assertTrue(ok)
        self.assertEqual(light, V.RED)

    def test_set_readback_error_is_yellow(self):
        seq = iter(["~SV0|darkness|50|", "~SC0|", "~SV1|"])
        ok, note, before, after, light = T.set_setting(lambda m, t: next(seq), "darkness", "90")
        self.assertEqual(light, V.YELLOW)

    def test_before_read_comm_error_sends_nothing(self):
        sent = []

        def xchg(msg, timeout):
            sent.append(msg)
            raise ngpcl.NgpclError("응답 시간 초과(10초)")
        ok, note, before, after, light = T.set_setting(xchg, "darkness", "90")
        self.assertIs(ok, False)                    # 변경하지 않았다고 기록된다
        self.assertEqual(sent, ["~SRdarkness|"])    # ~SC 는 보내지 않음

    def test_not_connected_at_send_is_failure_not_unknown(self):
        def xchg(msg, timeout):
            if msg.startswith("~SC"):
                raise ngpcl.NgpclError("연결되어 있지 않습니다")
            return "~SV0|darkness|50|"
        ok, note, before, after, light = T.set_setting(xchg, "darkness", "90")
        self.assertIs(ok, False)

    def test_response_name_mismatch(self):
        with self.assertRaises(T.ToolError):
            T.get_setting(fake({"~SRdarkness|": "~SV0|speed|90|"}), "darkness")

    def test_sc_timeout_is_unknown_not_exception(self):
        def xchg(msg, timeout):
            if msg.startswith("~SC"):
                raise ngpcl.NgpclError("응답 시간 초과(10초)")
            return "~SV0|darkness|50|"
        ok, note, before, after, light = T.set_setting(xchg, "darkness", "90")
        self.assertIsNone(ok)                       # 기록이 남도록 예외가 아니라 결과로 돌려준다
        self.assertEqual(light, V.YELLOW)

    def test_sr_timeout_after_sc_is_success_yellow(self):
        seq = iter(["~SV0|darkness|50|", "~SC0|"])

        def xchg(msg, timeout):
            try:
                return next(seq)
            except StopIteration:
                raise ngpcl.NgpclError("응답 시간 초과(10초)")
        ok, note, before, after, light = T.set_setting(xchg, "darkness", "90")
        self.assertTrue(ok)
        self.assertEqual(light, V.YELLOW)

    def test_bad_characters_blocked(self):
        with self.assertRaises(T.ToolError):
            T.set_setting(lambda m, t: "~SC0|", "dark|ness", "1")



def ds(producing, state):
    return "~DS0|%d|0|0|1|2|0|000000000|%s|0|" % (producing, state)


class StartProducingTest(unittest.TestCase):
    def run_seq(self, replies):
        seq = iter(replies)
        sent = []

        def xchg(msg, timeout):
            sent.append(msg)
            return next(seq)
        r = T.start_producing(xchg, True, tries=3, sleep=lambda s: None)
        return r, sent

    def test_starts_and_confirms(self):
        (ok, note, light), sent = self.run_seq([ds(0, "04"), "~ST0|", ds(1, "06")])
        self.assertEqual((ok, light), (True, V.GREEN))
        self.assertEqual(sent, ["~DR|", "~ST|06|", "~DR|"])   # 매뉴얼 5.22 형식(작업 이름 없는 단일 헤드)

    def test_already_producing_sends_nothing(self):
        (ok, note, light), sent = self.run_seq([ds(1, "06")])
        self.assertTrue(ok)
        self.assertEqual(sent, ["~DR|"])

    def test_flag_without_producing_code_is_not_trusted(self):
        # 생산 플래그만 1 이고 상태 코드가 06 이 아니면 이미 시작된 것으로 보지 않고 ~ST 를 보낸다
        (ok, note, light), sent = self.run_seq([ds(1, "02"), "~ST0|", ds(1, "06")])
        self.assertTrue(ok)
        self.assertEqual(sent, ["~DR|", "~ST|06|", "~DR|"])

    def test_rejected_is_red(self):
        (ok, note, light), sent = self.run_seq([ds(0, "04"), "~ST1|"])
        self.assertEqual((ok, light), (False, V.RED))

    def test_state_never_changes_is_yellow(self):
        (ok, note, light), sent = self.run_seq(
            [ds(0, "04"), "~ST0|", ds(0, "02"), ds(0, "02"), ds(0, "02")])
        self.assertEqual((ok, light), (False, V.YELLOW))
        self.assertIn("Producing", note)

    def test_comm_error_is_red_not_exception(self):
        def xchg(msg, timeout):
            raise ngpcl.NgpclError("응답 시간 초과(10초)")
        ok, note, light = T.start_producing(xchg, True, sleep=lambda s: None)
        self.assertEqual((ok, light), (False, V.RED))


class SimulatorEndToEnd(unittest.TestCase):
    def test_fields_and_settings_through_socket(self):
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        sim_printer.STATE.update({"job": "J", "fields": {"LOT": "AAA", "EXP": "20281031"},
                                  "settings": {"darkness": "50"}})
        threading.Thread(target=lambda: sim_printer.serve_client(
            *srv.accept(), "printable", False), daemon=True).start()
        c = ngpcl.Client()
        c.connect("127.0.0.1", port)
        x = lambda m, t: c.request(m, t)[1]
        try:
            self.assertEqual(T.query_fields(x, "", ""), [("LOT", "AAA"), ("EXP", "20281031")])
            self.assertEqual(T.query_fields(x, "", "LOT"), [("LOT", "AAA")])
            self.assertEqual(T.get_setting(x, "darkness"), "50")
            ok, note, before, after, light = T.set_setting(x, "darkness", "90")
            self.assertEqual((ok, before, after, light), (True, "50", "90", V.GREEN))
            self.assertEqual(T.get_setting(x, "darkness"), "90")
        finally:
            c.close()
            srv.close()


if __name__ == "__main__":
    unittest.main()

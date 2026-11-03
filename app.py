"""마켐이마지 프린터 데이터 전송 (초안 v0.1) — TASCO

장비 종류별 화면: TTO(열전사, NGPCL) / CIJ(잉크젯, 규격 확인 전).
실행: python app.py
"""
import datetime
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageTk

import ngpcl

try:  # 고해상도 화면에서 글자가 흐려지지 않게
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))

# TASCO 브랜드: 남색 + 흰색 + 회색 계열만 사용(브랜드가이드.md)
NAVY = "#00295B"
NAVY_HOVER = "#0B3A78"
WHITE = "#FFFFFF"
BG = "#F3F5F8"
LINE = "#D9DEE5"
TEXT = "#1F2937"
MUTED = "#6B7280"
OK = "#1B6E3C"
ERR = "#B42318"
FONT = "Malgun Gothic"


def visible(s):
    return s.replace("\x02", "<STX>").replace("\x03", "<ETX>")


class Card(tk.Frame):
    """흰 카드: 회색 테두리 + 남색 제목."""

    def __init__(self, parent, title):
        super().__init__(parent, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        tk.Label(self, text=title, bg=WHITE, fg=NAVY, font=(FONT, 11, "bold"),
                 anchor="w").pack(fill="x", padx=16, pady=(12, 6))
        self.body = tk.Frame(self, bg=WHITE)
        self.body.pack(fill="both", expand=True, padx=16, pady=(0, 14))


def field(parent, label, row, width=22, value="", show_label=True):
    tk.Label(parent, text=label, bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(
        row=row, column=0, sticky="w", pady=4, padx=(0, 10))
    e = ttk.Entry(parent, width=width)
    e.insert(0, value)
    e.grid(row=row, column=1, sticky="we", pady=4)
    return e


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TASCO 프린터 데이터 전송")
        self.minsize(980, 680)
        self.configure(bg=BG)
        self._style()
        self._header()
        self._tabs()
        self._log_panel()
        self.busy = False
        self.show("TTO")
        # 화면 배율(DPI)에 따라 글자가 커져도 잘리지 않도록 내용 크기에 맞춘다
        self.update_idletasks()
        w = min(max(self.winfo_reqwidth(), 1120), self.winfo_screenwidth() - 40)
        h = min(self.winfo_reqheight() + 10, self.winfo_screenheight() - 90)
        self.geometry("%dx%d+30+20" % (w, h))

    # ---- 스타일 ----
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", font=(FONT, 10), background=WHITE, foreground=TEXT)
        s.configure("TEntry", fieldbackground=WHITE, bordercolor=LINE, lightcolor=LINE,
                    darkcolor=LINE, padding=5)
        s.configure("TCombobox", fieldbackground=WHITE, bordercolor=LINE, padding=4)
        s.configure("TCheckbutton", background=WHITE, font=(FONT, 9))
        s.configure("Primary.TButton", background=NAVY, foreground=WHITE, bordercolor=NAVY,
                    padding=(16, 8), font=(FONT, 10, "bold"))
        s.map("Primary.TButton", background=[("active", NAVY_HOVER), ("disabled", LINE)],
              foreground=[("disabled", MUTED)])
        s.configure("Ghost.TButton", background=WHITE, foreground=NAVY, bordercolor=NAVY,
                    padding=(14, 7))
        s.map("Ghost.TButton", background=[("active", BG), ("disabled", WHITE)],
              foreground=[("disabled", MUTED)], bordercolor=[("disabled", LINE)])
        s.configure("Treeview", background=WHITE, fieldbackground=WHITE, rowheight=26,
                    bordercolor=LINE, font=(FONT, 10))
        s.configure("Treeview.Heading", background=BG, foreground=NAVY,
                    font=(FONT, 9, "bold"), relief="flat")
        s.map("Treeview", background=[("selected", "#DCE6F4")], foreground=[("selected", NAVY)])

    def _header(self):
        bar = tk.Frame(self, bg=NAVY, height=64)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        try:
            img = Image.open(os.path.join(HERE, "assets", "logo_white.png"))
            h = 26
            img = img.resize((int(img.width * h / img.height), h), Image.LANCZOS)
            self._logo = ImageTk.PhotoImage(img)
            tk.Label(bar, image=self._logo, bg=NAVY).pack(side="left", padx=(22, 14))
        except Exception:
            tk.Label(bar, text="TASCO", bg=NAVY, fg=WHITE, font=(FONT, 16, "bold")).pack(
                side="left", padx=22)
        tk.Frame(bar, bg="#3C5C8A", width=1).pack(side="left", fill="y", pady=16)
        tk.Label(bar, text="프린터 데이터 전송", bg=NAVY, fg=WHITE,
                 font=(FONT, 13, "bold")).pack(side="left", padx=14)
        tk.Label(bar, text="초안 v0.1", bg=NAVY, fg="#9DB2D0", font=(FONT, 9)).pack(
            side="right", padx=22)

    def _tabs(self):
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=22, pady=(16, 8))
        self.tab_btns = {}
        self.pages = {}
        host = tk.Frame(self, bg=BG)
        host.pack(fill="both", expand=True, padx=22)
        self.host = host
        for key, label, cls in (("TTO", "TTO  열전사", TtoPage), ("CIJ", "CIJ  잉크젯", CijPage)):
            b = tk.Label(row, text=label, font=(FONT, 10, "bold"), padx=22, pady=8,
                         cursor="hand2", bd=0, highlightthickness=1, highlightbackground=NAVY)
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, k=key: self.show(k))
            self.tab_btns[key] = b
            self.pages[key] = cls(host, self)

    def show(self, key):
        for k, b in self.tab_btns.items():
            on = k == key
            b.configure(bg=NAVY if on else WHITE, fg=WHITE if on else NAVY)
            self.pages[k].pack_forget()
        self.pages[key].pack(fill="both", expand=True)

    def _log_panel(self):
        card = Card(self, "통신 로그")
        card.pack(fill="x", padx=22, pady=(8, 18))
        wrap = tk.Frame(card.body, bg=WHITE)
        wrap.pack(fill="both", expand=True)
        self.logbox = tk.Text(wrap, height=8, bg="#FAFBFC", fg=TEXT, relief="flat",
                              font=("Consolas", 10), state="disabled", wrap="none",
                              highlightbackground=LINE, highlightthickness=1)
        sb = ttk.Scrollbar(wrap, command=self.logbox.yview)
        self.logbox.configure(yscrollcommand=sb.set)
        self.logbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for tag, color in (("TX", NAVY), ("RX", OK), ("ERR", ERR), ("INFO", MUTED)):
            self.logbox.tag_configure(tag, foreground=color)

    # ---- 공용 ----
    def log(self, kind, text):
        def put():
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            arrow = {"TX": "→ 송신", "RX": "← 수신", "ERR": "✕ 오류", "INFO": "· 정보"}[kind]
            self.logbox.configure(state="normal")
            self.logbox.insert("end", "%s  %s  %s\n" % (ts, arrow, visible(text)), kind)
            self.logbox.see("end")
            self.logbox.configure(state="disabled")
        self.after(0, put)

    def run(self, work, done=None):
        """링크는 한 번에 한 대화만 가능하므로 처리 중에는 새 명령을 받지 않는다."""
        if self.busy:
            self.log("INFO", "이전 명령을 처리하는 중입니다")
            return
        self.busy = True

        def target():
            try:
                res = work()
                err = None
            except ngpcl.NgpclError as e:
                res, err = None, str(e)
            except Exception as e:  # 예기치 못한 오류도 화면에 남긴다
                res, err = None, "%s: %s" % (type(e).__name__, e)

            def finish():
                self.busy = False
                if err:
                    self.log("ERR", err)
                elif done:
                    done(res)
            self.after(0, finish)
        threading.Thread(target=target, daemon=True).start()


class TtoPage(tk.Frame):
    """TTO(열전사, SmartDate X45/X65 등): NGPCL over TCP 21000."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self.client = ngpcl.Client()
        self.columnconfigure(0, weight=0, minsize=330)
        self.columnconfigure(1, weight=1)
        left = tk.Frame(self, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        right = tk.Frame(self, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        self._connection(left)
        self._status(left)
        self._job(right)
        self._actions(right)

    # ---- 연결 ----
    def _connection(self, parent):
        c = Card(parent, "장비 연결")
        c.pack(fill="x")
        g = c.body
        g.columnconfigure(1, weight=1)
        self.ip = field(g, "장비 IP", 0)
        self.port = field(g, "포트", 1, value=str(ngpcl.PORT))
        self.port.configure(state="disabled")  # 설계서: 항상 21000
        tk.Label(g, text="패킷 문자", bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(
            row=2, column=0, sticky="w", pady=4)
        self.charset = ttk.Combobox(g, state="readonly", width=20, values=[
            "Printable  { }  (기본)", "Traditional  STX / ETX"])
        self.charset.current(0)
        self.charset.grid(row=2, column=1, sticky="we", pady=4)
        self.v_check = tk.BooleanVar(value=False)
        self.v_status = tk.BooleanVar(value=True)
        self.v_unicode = tk.BooleanVar(value=False)
        for i, (t, v) in enumerate((("체크필드 사용", self.v_check),
                                    ("응답 상태 필드 사용(권장)", self.v_status),
                                    ("유니코드(16비트)", self.v_unicode))):
            ttk.Checkbutton(g, text=t, variable=v).grid(row=3 + i, column=0, columnspan=2,
                                                        sticky="w", pady=1)
        tk.Label(g, text="※ 장비 설정과 같아야 합니다", bg=WHITE, fg=MUTED, font=(FONT, 8)).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(2, 8))
        btns = tk.Frame(g, bg=WHITE)
        btns.grid(row=7, column=0, columnspan=2, sticky="we")
        self.b_conn = ttk.Button(btns, text="연결", style="Primary.TButton", command=self.connect)
        self.b_conn.pack(side="left")
        self.b_disc = ttk.Button(btns, text="해제", style="Ghost.TButton", command=self.disconnect,
                                 state="disabled")
        self.b_disc.pack(side="left", padx=8)
        st = tk.Frame(g, bg=WHITE)
        st.grid(row=8, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.dot = tk.Canvas(st, width=12, height=12, bg=WHITE, highlightthickness=0)
        self.dot_id = self.dot.create_oval(1, 1, 11, 11, fill=LINE, outline="")
        self.dot.pack(side="left")
        self.conn_label = tk.Label(st, text="연결 안 됨", bg=WHITE, fg=MUTED, font=(FONT, 9))
        self.conn_label.pack(side="left", padx=6)

    def _apply_options(self):
        c = self.client
        c.charset = "traditional" if self.charset.current() == 1 else "printable"
        c.use_check = self.v_check.get()
        c.status_on = self.v_status.get()
        c.unicode = self.v_unicode.get()

    def _set_conn(self, on, text):
        self.dot.itemconfigure(self.dot_id, fill=OK if on else LINE)
        self.conn_label.configure(text=text, fg=OK if on else MUTED)
        self.b_conn.configure(state="disabled" if on else "normal")
        self.b_disc.configure(state="normal" if on else "disabled")

    def connect(self):
        host = self.ip.get().strip()
        if not host:
            messagebox.showinfo("장비 IP", "장비 IP를 입력하세요")
            return
        self._apply_options()
        port = ngpcl.PORT
        if ":" in host:  # 시뮬레이터 시험용: 127.0.0.1:21000
            host, p = host.rsplit(":", 1)
            port = int(p)

        def work():
            self.client.connect(host, port)
            return host, port
        self.app.log("INFO", "연결 시도 %s:%d" % (host, port))
        self.app.run(work, lambda r: (self._set_conn(True, "연결됨  %s:%d" % r),
                                      self.app.log("INFO", "연결됨")))

    def disconnect(self):
        self.client.close()
        self._set_conn(False, "연결 안 됨")
        self.app.log("INFO", "연결 해제")

    # ---- 상태 ----
    def _status(self, parent):
        c = Card(parent, "장비 상태")
        c.pack(fill="x", pady=(14, 0))
        g = c.body
        g.columnconfigure(1, weight=1)
        self.st = {}
        for i, name in enumerate(("상태", "모드", "현재 작업", "작업 결과", "경고",
                                  "이미징 완료", "누적 인쇄", "배치 인쇄")):
            tk.Label(g, text=name, bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(
                row=i, column=0, sticky="w", pady=2)
            v = tk.Label(g, text="-", bg=WHITE, fg=TEXT, font=(FONT, 10, "bold"), anchor="w")
            v.grid(row=i, column=1, sticky="we", padx=(12, 0))
            self.st[name] = v
        ttk.Button(g, text="상태 새로고침", style="Ghost.TButton",
                   command=self.refresh).grid(row=9, column=0, columnspan=2, sticky="we",
                                              pady=(12, 0))

    def _ready(self):
        if not self.client.connected:
            messagebox.showinfo("연결", "먼저 장비에 연결하세요")
            return False
        return True

    def _x(self, msg, timeout=10.0):
        sent, got = self.client.request(msg, timeout)
        self.app.log("TX", sent)
        self.app.log("RX", got)
        return got

    @staticmethod
    def _ok(text):
        """명령 응답: '~JS0|' 처럼 식별자 뒤 성공코드 0."""
        return len(text) >= 4 and text[3] == "0"

    def refresh(self):
        if not self._ready():
            return

        def work():
            ds = ngpcl.parse_message(self._x(ngpcl.simple("~DR", "")), self.client.status_on)
            jn = ngpcl.parse_message(self._x(ngpcl.simple("~JR", "")), self.client.status_on)
            cv = ngpcl.parse_message(self._x(ngpcl.simple("~CR", "")), self.client.status_on)
            return ds, jn, cv

        def done(r):
            (_, _, f), (_, _, j), (_, _, c) = r
            off = 0
            f = list(f) + [""] * 9
            self.st["상태"].configure(
                text=ngpcl.PACKML_STATE.get(f[7 + off], f[7 + off]))
            self.st["모드"].configure(text=ngpcl.PACKML_MODE.get(f[8], f[8]))
            self.st["작업 결과"].configure(text=ngpcl.JOB_RESULT.get(f[4], f[4]))
            self.st["경고"].configure(text="있음" if f[2] == "1" else "없음",
                                    fg=ERR if f[2] == "1" else TEXT)
            self.st["이미징 완료"].configure(text="예" if f[3] == "1" else "아니오")
            self.st["현재 작업"].configure(text=(j[0] if j else "") or "(없음)")
            counts = dict(zip(c[0::2], c[1::2]))
            self.st["누적 인쇄"].configure(text=str(int(counts.get("TOTAL", "0") or 0)))
            self.st["배치 인쇄"].configure(text=str(int(counts.get("BATCH", "0") or 0)))
        self.app.run(work, done)

    # ---- 작업 ----
    def _job(self, parent):
        c = Card(parent, "작업과 필드 데이터")
        c.pack(fill="both", expand=True)
        g = c.body
        top = tk.Frame(g, bg=WHITE)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=0)
        tk.Label(top, text="작업 이름", bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        self.job = ttk.Entry(top)
        self.job.grid(row=0, column=1, sticky="we")
        tk.Label(top, text="할당 수", bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(
            row=0, column=2, sticky="w", padx=(18, 10))
        self.alloc = ttk.Entry(top, width=10)
        self.alloc.insert(0, "0")
        self.alloc.grid(row=0, column=3)
        tk.Label(top, text="작업 이름이 비면 현재 선택된 작업(단일 헤드)에 적용 · 할당 0 = 제한 없음",
                 bg=WHITE, fg=MUTED, font=(FONT, 8)).grid(row=1, column=0, columnspan=4,
                                                       sticky="w", pady=(4, 10))
        self.tree = ttk.Treeview(g, columns=("name", "value"), show="headings", height=6)
        self.tree.heading("name", text="필드 이름")
        self.tree.heading("value", text="값")
        self.tree.column("name", width=180)
        self.tree.column("value", width=300)
        self.tree.pack(fill="both", expand=True)
        add = tk.Frame(g, bg=WHITE)
        add.pack(fill="x", pady=(10, 0))
        add.columnconfigure(0, weight=1)
        add.columnconfigure(1, weight=2)
        self.f_name = ttk.Entry(add)
        self.f_val = ttk.Entry(add)
        self.f_name.grid(row=0, column=0, sticky="we", padx=(0, 8))
        self.f_val.grid(row=0, column=1, sticky="we", padx=(0, 8))
        self.f_val.bind("<Return>", lambda e: self.add_field())
        ttk.Button(add, text="추가", style="Ghost.TButton", command=self.add_field).grid(
            row=0, column=2, padx=(0, 6))
        ttk.Button(add, text="삭제", style="Ghost.TButton", command=self.del_field).grid(
            row=0, column=3)
        self.v_imaged = tk.BooleanVar(value=True)
        ttk.Checkbutton(g, text="이미징이 끝난 뒤 응답 받기 (권장)",
                        variable=self.v_imaged).pack(anchor="w", pady=(10, 0))

    def add_field(self):
        n, v = self.f_name.get().strip(), self.f_val.get()
        if not n:
            return
        try:
            ngpcl.clean(n)
            ngpcl.clean(v)
        except ngpcl.NgpclError as e:
            messagebox.showwarning("입력 확인", str(e))
            return
        self.tree.insert("", "end", values=(n, v))
        self.f_name.delete(0, "end")
        self.f_val.delete(0, "end")
        self.f_name.focus_set()

    def del_field(self):
        for i in self.tree.selection():
            self.tree.delete(i)

    def _fields(self):
        return [tuple(self.tree.item(i, "values")) for i in self.tree.get_children()]

    # ---- 전송 ----
    def _actions(self, parent):
        c = Card(parent, "전송")
        c.pack(fill="x", pady=(14, 0))
        g = c.body
        row = tk.Frame(g, bg=WHITE)
        row.pack(fill="x")
        ttk.Button(row, text="작업 선택", style="Primary.TButton",
                   command=lambda: self.send_job("~JS")).pack(side="left")
        ttk.Button(row, text="필드 갱신", style="Primary.TButton",
                   command=lambda: self.send_job("~JU")).pack(side="left", padx=8)
        ttk.Button(row, text="갱신 후 인쇄", style="Primary.TButton",
                   command=self.update_and_print).pack(side="left")
        ttk.Button(row, text="인쇄 신호", style="Ghost.TButton",
                   command=self.print_go).pack(side="left", padx=(18, 0))
        tk.Label(g, text="갱신 후 인쇄: 필드 갱신 → 인쇄 가능 대기 → 인쇄 (설계서 10장 순서)",
                 bg=WHITE, fg=MUTED, font=(FONT, 8)).pack(anchor="w", pady=(8, 0))

    def _job_message(self, ident):
        alloc = self.alloc.get().strip() or "0"
        build = ngpcl.job_select if ident == "~JS" else ngpcl.job_update
        return build(self.job.get().strip(), alloc, self._fields(), self.v_imaged.get())

    def send_job(self, ident):
        if not self._ready():
            return
        try:
            msg = self._job_message(ident)
        except ngpcl.NgpclError as e:
            messagebox.showwarning("입력 확인", str(e))
            return
        wait = 60.0 if self.v_imaged.get() else 10.0

        def work():
            return self._x(msg, wait)
        self.app.run(work, lambda r: self._result(ident, r))

    def _result(self, what, reply):
        if self._ok(reply):
            self.app.log("INFO", "%s 성공" % what)
            self.refresh_soft()
        else:
            self.app.log("ERR", "%s 실패 — 응답 %s" % (what, reply))

    def refresh_soft(self):
        self.after(50, self.refresh)

    def print_go(self):
        if not self._ready():
            return
        self.app.run(lambda: self._x(ngpcl.print_go("", 0, 1), 10.0),
                     lambda r: self._result("인쇄 신호", r))

    def update_and_print(self):
        if not self._ready():
            return
        try:
            msg = self._job_message("~JU")
        except ngpcl.NgpclError as e:
            messagebox.showwarning("입력 확인", str(e))
            return

        def work():
            r = self._x(msg, 60.0)
            if not self._ok(r):
                raise ngpcl.NgpclError("필드 갱신 실패 — 응답 %s" % r)
            r = self._x(ngpcl.print_status("", True), 60.0)
            _, st, f = ngpcl.parse_message(r, self.client.status_on)
            code = f[0] if f else st
            if code != "0":
                raise ngpcl.NgpclError("인쇄할 수 없는 상태입니다 (코드 %s: 1=상태 부적합, 2=인쇄 중, "
                                       "3=오류)" % code)
            return self._x(ngpcl.print_go("", 0, 1), 10.0)
        self.app.run(work, lambda r: self._result("갱신 후 인쇄", r))


class CijPage(tk.Frame):
    """CIJ(잉크젯): 통신 규격 자료 확인 전. 화면 자리만 잡아 둔다."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        c = Card(self, "CIJ 장비 연결")
        c.pack(fill="x")
        g = c.body
        g.columnconfigure(1, weight=1)
        field(g, "장비 IP", 0)
        field(g, "포트", 1, value="확인 필요")
        ttk.Button(g, text="연결", style="Primary.TButton", state="disabled").grid(
            row=2, column=0, sticky="w", pady=(10, 0))
        n = Card(self, "준비 중")
        n.pack(fill="x", pady=(14, 0))
        lines = (
            "CIJ 장비(9xxx 시리즈)는 TTO의 NGPCL과 통신 규격이 다릅니다.",
            "받은 자료는 NGPCL(TTO 전용)뿐이라 CIJ 규격 문서를 확인한 뒤 연결합니다.",
            "",
            "들어갈 기능(규격 확인 후):  메시지 선택  ·  변수 내용 전송  ·  상태 조회",
        )
        for t in lines:
            tk.Label(n.body, text=t, bg=WHITE, fg=TEXT if t else WHITE, font=(FONT, 10),
                     anchor="w").pack(fill="x")


if __name__ == "__main__":
    App().mainloop()

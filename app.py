"""마켐이마지 프린터 데이터 전송 (초안 v0.1) — TASCO

장비 종류별 화면: TTO(열전사, NGPCL) / CIJ(잉크젯, 규격 확인 전).
실행: python app.py
"""
import datetime
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageTk

import auth_ui
import confirm_ui
import device_tools
import dual
import ngpcl
import store
import verify
import verify_ui

try:  # 고해상도 화면에서 글자가 흐려지지 않게
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))

from app_theme import (NAVY, NAVY_HOVER, WHITE, BG, LINE, TEXT, MUTED, OK, ERR, FONT,
                       Card)


def visible(s):
    return s.replace("\x02", "<STX>").replace("\x03", "<ETX>")


def field(parent, label, row, width=22, value="", show_label=True):
    tk.Label(parent, text=label, bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(
        row=row, column=0, sticky="w", pady=4, padx=(0, 10))
    e = ttk.Entry(parent, width=width)
    e.insert(0, value)
    e.grid(row=row, column=1, sticky="we", pady=4)
    return e


class App(tk.Tk):
    IDLE_SECONDS = 600   # 미사용 10분이면 자동 로그아웃

    def __init__(self):
        super().__init__()
        self.title("TASCO 프린터 데이터 전송")
        self.minsize(720, 520)
        self.configure(bg=BG)
        self.busy = False
        self.user = None
        self.pages = {}
        self.store = store.Store()
        self.client = ngpcl.Client()   # 장비 연결은 로그인 사용자가 바뀌어도 유지
        self.dual = dual.DualEntry()
        self.conn_host = ""
        self.conn_text = ""
        self._style()
        self._header()
        self.content = tk.Frame(self, bg=BG)
        self.content.pack(fill="both", expand=True)
        self._last = time.time()
        for ev in ("<Key>", "<Button>", "<Motion>"):
            self.bind_all(ev, self._touch, add="+")
        self.after(30000, self._idle_check)
        self._q = queue.Queue()
        self.after(50, self._drain)
        self.start()

    # 작업 스레드의 결과는 큐로 받아 메인 스레드에서만 화면을 바꾼다(Tk는 스레드에 안전하지 않다)
    def post(self, fn):
        self._q.put(fn)

    def _drain(self):
        try:
            while True:
                self._q.get_nowait()()
        except queue.Empty:
            pass
        except Exception as e:   # 화면 갱신 오류가 앱을 멈추지 않게 한다
            print("화면 갱신 오류:", e)
        self.after(50, self._drain)

    def fit(self, minw, minh):
        """화면 배율(DPI)에 따라 글자가 커져도 잘리지 않도록 내용 크기에 맞춘다."""
        self.update_idletasks()
        w = min(max(self.winfo_reqwidth(), minw), self.winfo_screenwidth() - 40)
        h = min(max(self.winfo_reqheight() + 10, minh), self.winfo_screenheight() - 90)
        self.geometry("%dx%d+30+20" % (w, h))

    # ---- 로그인 흐름 ----
    def _clear(self):
        for w in self.content.winfo_children():
            w.destroy()

    def start(self, notice=""):
        self.user = None
        self._set_user(None)
        self._clear()
        if self.store.has_master():
            f = auth_ui.LoginFrame(self.content, self, notice)
        else:
            f = auth_ui.SetupFrame(self.content, self)
        f.pack(fill="both", expand=True)
        self.fit(760, 600)

    def after_login(self, user):
        self.user = user
        self._set_user(user)
        if user.must_change:
            self._clear()
            auth_ui.ChangePasswordFrame(self.content, self, user).pack(fill="both", expand=True)
        else:
            self.enter_main(user)

    def _expire_pending(self):
        """10분이 지난 1차 입력을 폐기하고 기록한다."""
        p = self.dual.discard()
        if p:
            try:
                self.store.log_work_safe("work_fail", p["worker"], None, job=p["request"].job,
                                    kind=dual.KIND_LABEL[p["request"].kind],
                                    reason="1차 입력 만료(%d분 안에 2차 확인 없음)" % (dual.EXPIRE_SECONDS // 60))
            except store.AuthError:
                pass

    def enter_main(self, user):
        self._clear()
        self._set_user(user)
        if self.dual.expired():
            self._expire_pending()
        if user.role == "manager" and self.dual.has_pending():
            self.open_confirm()    # 관리자는 대기 중인 요청이 있으면 바로 확인 화면
            return
        tabs = [("TTO", "TTO  열전사", TtoPage), ("CIJ", "CIJ  잉크젯", CijPage)]
        if user.role == "master":
            tabs += [("ACC", "계정 관리", auth_ui.AccountsPage), ("EVT", "기록", auth_ui.EventsPage)]
        self._tabs(tabs)
        self._log_panel()
        self.show(tabs[0][0])
        self.fit(1120, 700)

    def open_confirm(self):
        """2차 확인 입력 화면(관리자·마스터)."""
        if self.dual.expired():
            self._expire_pending()
        if not self.dual.has_pending():
            messagebox.showinfo("2차 확인", "대기 중인 1차 요청이 없습니다")
            self.enter_main(self.user)
            return
        self._clear()
        self._set_user(self.user)
        confirm_ui.ManagerEntryFrame(self.content, self).pack(fill="both", expand=True)
        self.fit(1000, 700)

    def logout(self, notice=""):
        if self.user:
            self.store.logout(self.user)
        self.busy = False
        self.pages = {}
        self.start(notice)

    def _touch(self, _e=None):
        self._last = time.time()

    def _idle_check(self):
        if self.dual.expired():
            self._expire_pending()
        if self.user and not self.busy and time.time() - self._last > self.IDLE_SECONDS:
            self.logout("미사용 10분이 지나 자동으로 로그아웃되었습니다")
        self.after(30000, self._idle_check)

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

    def _window_icon(self):
        """창 아이콘: TASCO 엠블럼(정사각형으로 여백을 두고 맞춘다)."""
        try:
            src = Image.open(os.path.join(HERE, "assets", "emblem_navy.png")).convert("RGBA")
            side = max(src.size)
            sq = Image.new("RGBA", (side, side), (255, 255, 255, 255))
            sq.alpha_composite(src, ((side - src.width) // 2, (side - src.height) // 2))
            self._icon = ImageTk.PhotoImage(sq.resize((64, 64), Image.LANCZOS))
            self.iconphoto(True, self._icon)
        except Exception:
            pass

    def _header(self):
        self._window_icon()
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
        tk.Label(bar, text="초안 v0.2", bg=NAVY, fg="#9DB2D0", font=(FONT, 9)).pack(
            side="right", padx=(10, 22))
        self.logout_btn = tk.Label(bar, text="로그아웃", bg=NAVY, fg=WHITE, font=(FONT, 9, "bold"),
                                   padx=12, pady=5, cursor="hand2", highlightthickness=1,
                                   highlightbackground="#9DB2D0")
        self.logout_btn.bind("<Button-1>", lambda e: self.logout())
        self.user_lbl = tk.Label(bar, text="", bg=NAVY, fg=WHITE, font=(FONT, 10))

    def _set_user(self, user):
        if user:
            self.user_lbl.configure(text="%s  ·  %s" % (user.display_name, user.label))
            self.logout_btn.pack(side="right", padx=(0, 6))
            self.user_lbl.pack(side="right", padx=(0, 14))
        else:
            self.logout_btn.pack_forget()
            self.user_lbl.pack_forget()

    def _tabs(self, tabs):
        row = tk.Frame(self.content, bg=BG)
        row.pack(fill="x", padx=22, pady=(16, 8))
        self.tab_btns = {}
        self.pages = {}
        host = tk.Frame(self.content, bg=BG)
        host.pack(fill="both", expand=True, padx=22)
        self.host = host
        for key, label, cls in tabs:
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
        card = Card(self.content, "통신 로그")
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
            try:
                self.logbox.configure(state="normal")
                self.logbox.insert("end", "%s  %s  %s\n" % (ts, arrow, visible(text)), kind)
                self.logbox.see("end")
                self.logbox.configure(state="disabled")
            except (AttributeError, tk.TclError):
                pass  # 로그아웃으로 화면이 바뀐 뒤 도착한 기록
        self.post(put)

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
            self.post(finish)
        threading.Thread(target=target, daemon=True).start()


class TtoPage(tk.Frame):
    """TTO(열전사, SmartDate X45/X65 등): NGPCL over TCP 21000."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self.client = app.client
        self.role = app.user.role if app.user else "worker"
        self.columnconfigure(0, weight=0, minsize=330)
        self.columnconfigure(1, weight=1)
        left = tk.Frame(self, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        right = tk.Frame(self, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        self._connection(left)
        self._status(left)
        if self.role == "worker":
            self._job(right)
            self._actions(right)
        else:
            self._note(right)
        self._tools(right)
        if app.conn_host:
            self.ip.insert(0, app.conn_host)
        if self.client.connected:
            self._set_conn(True, app.conn_text)

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
        def done(r):
            self.app.conn_host = self.ip.get().strip()
            self.app.conn_text = "연결됨  %s:%d" % r
            self._set_conn(True, self.app.conn_text)
            self.app.log("INFO", "연결됨")
        self.app.run(work, done)

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

    # ---- 필드·설정 도구 ----
    def _tools(self, parent):
        c = Card(parent, "필드·설정 조회")
        c.pack(fill="x", pady=(14, 0))
        g = c.body
        g.columnconfigure(1, weight=1)
        tk.Label(g, text="필드 이름", bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        self.q_field = ttk.Entry(g)
        self.q_field.grid(row=0, column=1, sticky="we")
        self.q_field.bind("<Return>", lambda e: self.query_fields())
        ttk.Button(g, text="필드 조회", style="Ghost.TButton", command=self.query_fields).grid(
            row=0, column=2, padx=(8, 0))
        tk.Label(g, text="비우면 현재 작업의 전체 필드", bg=WHITE, fg=MUTED, font=(FONT, 8)).grid(
            row=1, column=1, sticky="w")
        self.q_tree = ttk.Treeview(g, columns=("name", "value"), show="headings", height=3)
        self.q_tree.heading("name", text="필드 이름")
        self.q_tree.heading("value", text="장비의 현재 값")
        self.q_tree.column("name", width=170)
        self.q_tree.column("value", width=300)
        self.q_tree.grid(row=2, column=0, columnspan=3, sticky="we", pady=(6, 10))

        tk.Label(g, text="설정 이름", bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(
            row=3, column=0, sticky="w", padx=(0, 10))
        self.s_name = ttk.Entry(g)
        self.s_name.grid(row=3, column=1, sticky="we")
        ttk.Button(g, text="설정 조회", style="Ghost.TButton", command=self.query_setting).grid(
            row=3, column=2, padx=(8, 0), pady=2)
        tk.Label(g, text="설정 값", bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(
            row=4, column=0, sticky="w", padx=(0, 10))
        self.s_value = ttk.Entry(g)
        self.s_value.grid(row=4, column=1, sticky="we")
        worker = self.role == "worker"
        self.b_set = ttk.Button(g, text="설정 변경 요청", style="Primary.TButton",
                                command=self.request_setting)
        self.b_set.grid(row=4, column=2, padx=(8, 0), pady=2)
        if not worker:
            self.b_set.configure(state="disabled")
        tk.Label(g, text="설정 변경은 이중 입력: 작업자가 요청 → 관리자가 같은 이름·값을 입력해 일치할 때만 전송"
                         " · 인쇄 중에는 반영되지 않음" if worker else
                 "설정 변경은 1차 작업자가 요청하고 관리자가 2차 확인합니다 (조회는 모두 가능)",
                 bg=WHITE, fg=MUTED, font=(FONT, 8)).grid(row=5, column=0, columnspan=3,
                                                          sticky="w", pady=(2, 0))
        res = tk.Frame(g, bg=WHITE)
        res.grid(row=6, column=0, columnspan=3, sticky="we", pady=(6, 0))
        self.t_dot = tk.Canvas(res, width=14, height=14, bg=WHITE, highlightthickness=0)
        self.t_dot_id = self.t_dot.create_oval(1, 1, 13, 13, fill=LINE, outline="")
        self.t_dot.pack(side="left")
        self.t_msg = tk.Label(res, text="", bg=WHITE, fg=MUTED, font=(FONT, 9), anchor="w",
                              justify="left", wraplength=470)
        self.t_msg.pack(side="left", padx=6, fill="x")

    def _tool_result(self, text, light=None):
        color = {verify.GREEN: OK, verify.YELLOW: verify_ui.WARN, verify.RED: ERR}.get(light, LINE)
        self.t_dot.itemconfigure(self.t_dot_id, fill=color)
        self.t_msg.configure(text=text, fg=TEXT if light else MUTED)

    def _xchg(self):
        """도구용 전송 함수. 통신 오류가 나면 링크를 끊어, 늦게 온 응답이 다음 명령과 짝지어지지 않게 한다."""
        def x(m, t):
            try:
                return self._x(m, t)
            except ngpcl.NgpclError:
                self.client.close()
                self.app.post(lambda: self._set_conn(False, "연결 끊김(통신 오류) — 다시 연결하세요"))
                raise
        return x

    def query_fields(self):
        if not self._ready():
            return
        name, on = self.q_field.get().strip(), self.client.status_on
        x = self._xchg()

        def work():
            return device_tools.query_fields(x, "", name, on)

        def done(rows):
            self.q_tree.delete(*self.q_tree.get_children())
            for n, v in rows:
                self.q_tree.insert("", "end", values=(n, v))
            if rows:
                self._tool_result("필드 %d개를 조회했습니다" % len(rows), verify.GREEN)
            else:
                self._tool_result("장비에서 필드를 찾지 못했습니다(이름·현재 작업 확인)", verify.YELLOW)
        self.app.run(self._guard(work), done)

    def query_setting(self):
        if not self._ready():
            return
        name, on = self.s_name.get().strip(), self.client.status_on
        x = self._xchg()

        def work():
            return device_tools.get_setting(x, name, on)

        def done(v):
            self.s_value.delete(0, "end")
            self.s_value.insert(0, v)
            self._tool_result("설정 %s = %s" % (name, v), verify.GREEN)
        self.app.run(self._guard(work), done)

    def _guard(self, work):
        """도구 오류(ToolError)를 화면 문구로 바꿔 보여 준다."""
        def run():
            try:
                return work()
            except device_tools.ToolError as e:
                raise ngpcl.NgpclError(str(e))
        return run

    def request_setting(self):
        """설정 변경은 이중 입력: 작업자가 이름·값을 입력해 관리자 확인을 요청한다(직접 전송 없음)."""
        if not self._ready():
            return
        try:
            req = dual.Request("setting", "", "0",
                               [(self.s_name.get().strip(), self.s_value.get())], True)
        except dual.DualError as e:
            messagebox.showwarning("입력 확인", str(e))
            return
        self.request_confirm("setting", req)

    # ---- 전송 ----
    def _note(self, parent):
        """관리자·마스터 화면: 대기 중인 1차 요청이 있으면 2차 확인 입력으로 들어간다."""
        c = Card(parent, "2차 확인")
        c.pack(fill="x")
        d = self.app.dual
        if d.has_pending() and not d.expired():
            w = d.pending_worker()
            left = d.seconds_left()
            tk.Label(c.body, text="대기 중인 1차 요청: %s  ·  %s  ·  남은 시간 %d분 %02d초"
                                  % (w.display_name, dual.KIND_LABEL[d.pending_kind()],
                                     left // 60, left % 60),
                     bg=WHITE, fg=NAVY, font=(FONT, 10, "bold"), anchor="w").pack(fill="x")
            ttk.Button(c.body, text="2차 확인 입력", style="Primary.TButton",
                       command=self.app.open_confirm).pack(anchor="w", pady=(10, 0))
        else:
            tk.Label(c.body, text="대기 중인 1차 작업자의 확인 요청이 없습니다.\n"
                                  "작업자가 입력을 마치면 여기서 2차 확인 입력을 할 수 있습니다.",
                     bg=WHITE, fg=MUTED, font=(FONT, 10), justify="left", anchor="w").pack(fill="x")

    def _actions(self, parent):
        c = Card(parent, "관리자 확인 요청")
        c.pack(fill="x", pady=(14, 0))
        g = c.body
        row = tk.Frame(g, bg=WHITE)
        row.pack(fill="x")
        for text, kind in (("작업 선택", "select"), ("필드 갱신", "update"),
                           ("갱신 후 인쇄", "update_print")):
            ttk.Button(row, text=text, style="Primary.TButton",
                       command=lambda k=kind: self.request_confirm(k)).pack(side="left", padx=(0, 8))
        ttk.Button(row, text="인쇄 신호", style="Ghost.TButton",
                   command=self.print_go).pack(side="left", padx=(10, 0))
        tk.Label(g, text="입력을 마치고 요청하면 로그아웃되고, 2차 관리자가 같은 내용을 입력해 일치할 때만 "
                         "장비로 전송됩니다 (10분 이내)",
                 bg=WHITE, fg=MUTED, font=(FONT, 8)).pack(anchor="w", pady=(8, 0))

    def request_confirm(self, kind, req=None):
        app = self.app
        if not self._ready():
            return
        if app.busy:    # 장비와 통신 중이면 로그아웃·요청을 미룬다(응답이 엇갈리지 않게)
            messagebox.showinfo("처리 중", "장비와 통신 중입니다. 끝난 뒤 다시 요청하세요.")
            return
        if req is None:
            try:
                req = dual.Request(kind, self.job.get(), self.alloc.get(), self._fields(),
                                   self.v_imaged.get())
            except dual.DualError as e:
                messagebox.showwarning("입력 확인", str(e))
                return
        old = app.dual.submit(app.user, req)
        if old:   # 이전 요청이 확인 없이 새 요청으로 바뀐 경우
            try:
                app.store.log_work_safe("work_fail", old["worker"], None, job=old["request"].job,
                                   kind=dual.KIND_LABEL[old["request"].kind],
                                   reason="2차 확인 전에 1차가 새 요청을 입력해 폐기")
            except store.AuthError:
                pass
        app.store.log_work_safe("work_request", app.user, job=req.job, kind=dual.KIND_LABEL[kind])
        messagebox.showinfo("1차 입력 완료", "입력을 접수했습니다.\n2차 관리자가 로그인해 같은 내용을 "
                                        "입력하면 전송됩니다 (10분 이내).")
        app.logout("1차 입력이 접수되었습니다. 2차 관리자가 로그인해 같은 내용을 입력하세요 (10분 이내)")

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


def _single_instance():
    """같은 PC에서 프로그램을 두 개 띄우지 못하게 한다(계정·기록 충돌 방지)."""
    try:
        import ctypes
        h = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\TASCO_MarkemSender")
        if ctypes.windll.kernel32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
            return None
        return h
    except Exception:
        return True   # Windows가 아니면 검사하지 않는다


if __name__ == "__main__":
    _lock = _single_instance()
    if _lock is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("이미 실행 중", "프로그램이 이미 실행 중입니다")
    else:
        App().mainloop()

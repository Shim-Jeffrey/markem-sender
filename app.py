"""마켐이마지 프린터 데이터 전송 (초안 v0.1) — TASCO

장비 화면: TTO(열전사, SmartDate X40/X45 등, NGPCL over TCP 21000)만 지원한다.
실행: python app.py
"""
import datetime
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageTk

import auth_ui
import confirm_ui
import device_tools
import dual
import job_picker
import messages
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

import app_theme
from app_theme import (px, NAVY, NAVY_HOVER, WHITE, BG, LINE, TEXT, MUTED, OK, ERR, FONT,
                       Card)


# 메뉴(탭) 쉬운 설명: 탭 옆에 표시
TAB_HELP = {
    "TTO": "프린터에 작업·글자를 보내고, 상태를 보고, 설정을 확인하는 화면입니다.",
    "ACC": "사용할 사람(작업자·관리자)을 만들고 관리하는 화면입니다. 마스터만 쓸 수 있습니다.",
    "EVT": "누가 언제 무엇을 했는지 기록을 보는 화면입니다. 마스터만 쓸 수 있습니다.",
}


def visible(s):
    return s.replace("\x02", "<STX>").replace("\x03", "<ETX>")


def field(parent, label, row, width=22, value="", show_label=True):
    tk.Label(parent, text=label, bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(
        row=row, column=0, sticky="w", pady=4, padx=(0, 10))
    e = ttk.Entry(parent, width=width)
    e.insert(0, value)
    e.grid(row=row, column=1, sticky="we", pady=4)
    return e


KIOSK = "--window" not in sys.argv    # 기본: 전체 화면 잠금. 개발·시험용으로 `--window` 를 주면 일반 창


class ExitDialog(tk.Toplevel):
    """프로그램 종료 확인: 마스터 계정의 아이디·비밀번호가 맞아야 종료된다."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.ok = False
        self.title("프로그램 종료")
        self.configure(bg=WHITE, padx=24, pady=20)
        self.transient(app)
        self.attributes("-topmost", True)
        self.resizable(False, False)
        tk.Label(self, text="프로그램을 종료하려면 마스터 계정으로 확인하세요", bg=WHITE, fg=TEXT,
                 font=(FONT, 11, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.user = auth_ui._entry(self, "마스터 아이디", 1)
        self.pw = auth_ui._entry(self, "비밀번호", 2, secret=True)
        self.msg = tk.Label(self, text="", bg=WHITE, fg=ERR, font=(FONT, 9), anchor="w",
                            wraplength=px(320), justify="left")
        self.msg.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        row = tk.Frame(self, bg=WHITE)
        row.grid(row=4, column=0, columnspan=2, sticky="we", pady=(10, 0))
        ttk.Button(row, text="종료", style="Primary.TButton", command=self.submit).pack(side="left")
        ttk.Button(row, text="취소", style="Ghost.TButton", command=self.destroy).pack(
            side="left", padx=(8, 0))
        self.pw.bind("<Return>", lambda e: self.submit())
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry("+%d+%d" % (max(x, 0), max(y, 0)))
        self.user.focus_force()
        self.grab_set()

    def submit(self):
        try:
            u = self.app.store.login(self.user.get(), self.pw.get())
        except store.AuthError as e:
            self.msg.configure(text=str(e))
            self.pw.delete(0, "end")
            return
        if u.role != "master":
            self.msg.configure(text="마스터 계정만 프로그램을 종료할 수 있습니다")
            self.pw.delete(0, "end")
            try:
                self.app.store.logout(u)
            except Exception:
                pass
            return
        try:
            self.app.store.log_event("app_exit", u.username, {})
        except Exception as e:     # 기록이 남지 않으면 종료하지 않는다
            self.msg.configure(text="종료 기록을 저장하지 못해 종료하지 않았습니다. 담당자에게 알려 주세요")
            self.app.log("ERR", "종료 기록 실패: %s" % e)
            return
        try:
            self.app.store.logout(u)
        except Exception:
            pass
        self.ok = True
        self.destroy()


class App(tk.Tk):
    IDLE_SECONDS = 600   # 미사용 10분이면 자동 로그아웃

    def __init__(self):
        super().__init__()
        self._set_scale()
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
        self._kiosk_on = False
        self._exiting = False
        self.protocol("WM_DELETE_WINDOW", self.request_exit)    # 일반 창 모드에서도 종료 확인
        if KIOSK:
            self._kiosk()
        self.start()

    def _set_scale(self):
        """전체 화면에서는 화면 해상도에 맞춰 화면 전체 크기를 맞춘다.

        기준: 100% 배율에서 주 화면이 920x1200 픽셀쯤 필요하다. 화면이 그보다 크면 가로·세로 중
        먼저 닿는 쪽에 맞춰 1.0~1.8배로 키운다(내용이 화면 밖으로 잘리지 않게 한다).
        """
        f = 1.0
        if KIOSK:
            f = min(self.winfo_screenwidth() / 920.0, self.winfo_screenheight() / 1200.0)
            f = max(1.0, min(1.8, f))
            self.tk.call("tk", "scaling", 96.0 / 72.0 * f)    # 화면은 물리 픽셀 기준(DPI 인식)
        app_theme.SCALE = f

    # ---- 전체 화면 잠금: 마스터 비밀번호 없이는 종료·창 전환이 되지 않는다 ----
    def _kiosk(self):
        self._kiosk_on = True
        self.attributes("-fullscreen", True)
        self.protocol("WM_DELETE_WINDOW", self.request_exit)      # X·Alt+F4 도 종료 확인으로
        self.bind("<Unmap>", lambda e: self.after(50, self._restore))
        self.bind("<Escape>", lambda e: "break")
        self.after(1000, self._keep_front)

    def _restore(self):
        if self._kiosk_on and not self._exiting and self.state() == "iconic":
            self.deiconify()
            self.attributes("-fullscreen", True)

    def _keep_front(self):
        if not self._exiting:
            try:
                if not self.attributes("-fullscreen"):
                    self.attributes("-fullscreen", True)
                if self.state() == "iconic":
                    self.deiconify()
            except tk.TclError:
                return
            self.after(1000, self._keep_front)

    def request_exit(self):
        """종료 요청: 장비와 통신 중이면 막고, 마스터 확인 뒤에만 닫는다."""
        if self._exiting:
            return
        g = self.grab_current()      # 결과 창 같은 모달이 열려 있으면 그 창을 먼저 닫게 한다(모달이 풀리지 않게)
        if g is not None:
            g.lift()
            g.focus_force()
            return
        if self.busy:
            messagebox.showinfo("처리 중", "장비와 통신 중입니다. 끝난 뒤 종료하세요.", parent=self)
            return
        dlg = ExitDialog(self)
        self.wait_window(dlg)
        if dlg.ok:
            self._exiting = True
            self._expire_pending()      # 대기 중인 1차 요청이 있으면 폐기하고 기록
            if self.user:
                try:
                    self.store.logout(self.user)
                except Exception:
                    pass
            self.destroy()

    # 작업 스레드의 결과는 큐로 받아 메인 스레드에서만 화면을 바꾼다(Tk는 스레드에 안전하지 않다)
    def post(self, fn):
        self._q.put(fn)

    def _drain(self):
        # 다음 차례를 먼저 예약한다: 처리 중 결과 창(모달)이 열려도 그 창 안의 작업 결과가 계속 반영된다
        self.after(50, self._drain)
        try:
            while True:
                self._q.get_nowait()()
        except queue.Empty:
            pass
        except Exception as e:   # 화면 갱신 오류가 앱을 멈추지 않게 한다
            print("화면 갱신 오류:", e)

    def fit(self, minw, minh):
        """화면 배율(DPI)에 따라 글자가 커져도 잘리지 않도록 내용 크기에 맞춘다(전체 화면에서는 하지 않는다)."""
        if self._kiosk_on:
            return
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
        tabs = [("TTO", "TTO  열전사", TtoPage)]
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

    def _manual_logout(self):
        """[로그아웃] 버튼: 장비와 통신 중에는 막는다(다음 사용자와 응답이 엇갈리지 않게)."""
        if self.busy:
            messagebox.showinfo("처리 중", "장비와 통신 중입니다. 끝난 뒤 로그아웃하세요.")
            return
        self.logout()

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
        s.configure("Treeview", background=WHITE, fieldbackground=WHITE, rowheight=px(26),
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
        bar = tk.Frame(self, bg=NAVY, height=px(64))
        bar.pack(fill="x")
        bar.pack_propagate(False)
        try:
            img = Image.open(os.path.join(HERE, "assets", "logo_white.png"))
            h = px(26)
            img = img.resize((int(img.width * h / img.height), h), Image.LANCZOS)
            self._logo = ImageTk.PhotoImage(img)
            tk.Label(bar, image=self._logo, bg=NAVY).pack(side="left", padx=(22, 14))
        except Exception:
            tk.Label(bar, text="TASCO", bg=NAVY, fg=WHITE, font=(FONT, 16, "bold")).pack(
                side="left", padx=22)
        tk.Frame(bar, bg="#3C5C8A", width=1).pack(side="left", fill="y", pady=16)
        tk.Label(bar, text="프린터 데이터 전송", bg=NAVY, fg=WHITE,
                 font=(FONT, 13, "bold")).pack(side="left", padx=14)
        self.exit_btn = tk.Label(bar, text="프로그램 종료", bg=NAVY, fg=WHITE, font=(FONT, 9, "bold"),
                                 padx=12, pady=5, cursor="hand2", highlightthickness=1,
                                 highlightbackground="#9DB2D0")
        self.exit_btn.bind("<Button-1>", lambda e: self.request_exit())
        self.exit_btn.pack(side="right", padx=(6, 22))
        tk.Label(bar, text="v0.5", bg=NAVY, fg="#9DB2D0", font=(FONT, 9)).pack(
            side="right", padx=(10, 0))
        self.logout_btn = tk.Label(bar, text="로그아웃", bg=NAVY, fg=WHITE, font=(FONT, 9, "bold"),
                                   padx=12, pady=5, cursor="hand2", highlightthickness=1,
                                   highlightbackground="#9DB2D0")
        self.logout_btn.bind("<Button-1>", lambda e: self._manual_logout())
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
        self.tab_help = tk.Label(row, text="", bg=BG, fg=MUTED, font=(FONT, 9), anchor="w")
        self.tab_help.pack(side="left", padx=14)

    def show(self, key):
        for k, b in self.tab_btns.items():
            on = k == key
            b.configure(bg=NAVY if on else WHITE, fg=WHITE if on else NAVY)
            self.pages[k].pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        self.tab_help.configure(text=TAB_HELP.get(key, ""))

    def _log_panel(self):
        card = Card(self.content, "통신 로그",
                    "프린터와 주고받은 내용입니다. 송신=보낸 것, 수신=받은 것. 문제가 생기면 이 내용을 담당자에게 알려 주세요.")
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
    def _file_log(self, kind, text):
        """통신 내용을 날짜별 파일로도 남긴다(화면 로그는 로그아웃하면 사라지므로, 문제 분석용)."""
        try:
            if not getattr(self, "_log_dir", None):
                self._log_dir = store.default_data_dir()
            now = datetime.datetime.now()
            path = os.path.join(self._log_dir, "comm_%s.log" % now.strftime("%Y-%m-%d"))
            with open(path, "a", encoding="utf-8") as f:
                f.write("%s  %-4s %s\n" % (now.strftime("%H:%M:%S"), kind, visible(text)))
        except Exception:
            pass     # 로그 파일 문제로 전송을 멈추지 않는다

    def log(self, kind, text):
        self._file_log(kind, text)

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
        else:
            self._note(right)
        self._tools(right)
        if app.conn_host:
            self.ip.insert(0, app.conn_host)
        if self.client.connected:
            self._set_conn(True, app.conn_text)

    # ---- 연결 ----
    def _connection(self, parent):
        c = Card(parent, "장비 연결",
                 "프린터의 IP 주소를 넣고 [연결]을 누르세요. 초록 불이 켜지면 연결된 것입니다. 패킷 문자 등은 프린터 설정과 같아야 합니다.")
        c.pack(fill="x")
        g = c.body
        g.columnconfigure(1, weight=1)
        self.ip = field(g, "장비 IP", 0)
        self.port = field(g, "포트", 1, value=str(ngpcl.PORT))
        self.port.configure(state="disabled")  # 설계서: 항상 21000
        def note(row, text):
            tk.Label(g, text=text, bg=WHITE, fg=MUTED, font=(FONT, 8), anchor="w", justify="left",
                     wraplength=px(290)).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 4))

        tk.Label(g, text="패킷 문자", bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(
            row=2, column=0, sticky="w", pady=4)
        self.charset = ttk.Combobox(g, state="readonly", width=20, values=[
            "Printable  { }  (기본)", "Traditional  STX / ETX"])
        self.charset.current(0)
        self.charset.grid(row=2, column=1, sticky="we", pady=4)
        note(3, "메시지의 시작·끝을 표시하는 방식입니다. 보통 기본값이면 됩니다. "
                "프린터가 STX/ETX 방식으로 설정돼 있으면 두 번째를 고르세요.")
        self.v_check = tk.BooleanVar(value=False)
        self.v_status = tk.BooleanVar(value=True)
        self.v_unicode = tk.BooleanVar(value=False)
        for row, (t, v, d) in zip((4, 6, 8), (
                ("오류 검사 숫자 사용 (체크필드)", self.v_check,
                 "보낸 글자가 중간에 깨지지 않았는지 확인하는 검사 숫자를 붙입니다. "
                 "프린터에서 켜 둔 경우에만 체크하세요. 모르면 끄세요."),
                ("답장에 성공 여부 붙임 (응답 상태 필드)", self.v_status,
                 "프린터가 답장 맨 앞에 성공 여부 숫자(0=성공)를 붙이는 방식입니다. "
                 "프린터 설정과 같게 두세요. 모르면 켜 두세요."),
                ("모든 글자 지원 (유니코드)", self.v_unicode,
                 "한글 같은 모든 글자를 보내는 방식입니다. 프린터가 영문·숫자만 쓰는 설정이면 끄세요. "
                 "프린터 설정과 같아야 합니다."))):
            ttk.Checkbutton(g, text=t, variable=v).grid(row=row, column=0, columnspan=2,
                                                        sticky="w", pady=(4, 0))
            note(row + 1, d)
        note(10, "※ 모르면 기본값 그대로 [연결]을 누르세요. 연결이 안 되거나 답이 없으면 "
                 "프린터의 통신 설정과 같은지 확인하세요.")
        btns = tk.Frame(g, bg=WHITE)
        btns.grid(row=11, column=0, columnspan=2, sticky="we", pady=(4, 0))
        self.b_conn = ttk.Button(btns, text="연결", style="Primary.TButton", command=self.connect)
        self.b_conn.pack(side="left")
        self.b_disc = ttk.Button(btns, text="해제", style="Ghost.TButton", command=self.disconnect,
                                 state="disabled")
        self.b_disc.pack(side="left", padx=8)
        st = tk.Frame(g, bg=WHITE)
        st.grid(row=12, column=0, columnspan=2, sticky="w", pady=(10, 0))
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
        c = Card(parent, "장비 상태",
                 "프린터가 지금 인쇄 중인지, 경고는 없는지 보여줍니다. [상태 새로고침]을 누르면 최신으로 바뀝니다.")
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

    # ---- 작업·메세지 ----
    def _job(self, parent):
        c = Card(parent, "작업과 보내는 메세지",
                 "프린터에서 작업을 불러와 선택하고, 인쇄할 메세지를 순서대로 추가하세요. "
                 "메세지는 1번부터 자동으로 번호가 붙어 프린터의 같은 번호 칸으로 들어갑니다 "
                 "(필드이름은 직접 입력하지 않습니다).")
        c.pack(fill="both", expand=True)
        g = c.body
        tk.Label(g, text="① 작업 (프린터에서 불러와 선택)", bg=WHITE, fg=NAVY,
                 font=(FONT, 9, "bold")).pack(anchor="w")
        self.picker = job_picker.JobPicker(g, self.app)
        self.picker.pack(fill="x", pady=(2, 10))
        tk.Label(g, text="② 보내는 메세지 (추가한 순서대로 메세지 1, 2, 3 …)", bg=WHITE, fg=NAVY,
                 font=(FONT, 9, "bold")).pack(anchor="w")
        self._msgs = []          # 메세지 원문은 목록에 보관(표는 표시용: 숫자처럼 보이는 글자가 바뀌지 않게)
        self.tree = ttk.Treeview(g, columns=("no", "msg"), show="headings", height=6)
        self.tree.heading("no", text="번호")
        self.tree.heading("msg", text="보내는 메세지")
        self.tree.column("no", width=90, anchor="center")
        self.tree.column("msg", width=420)
        self.tree.pack(fill="both", expand=True, pady=(2, 0))
        add = tk.Frame(g, bg=WHITE)
        add.pack(fill="x", pady=(10, 0))
        add.columnconfigure(0, weight=1)
        self.f_val = ttk.Entry(add)
        self.f_val.grid(row=0, column=0, sticky="we", padx=(0, 8))
        self.f_val.bind("<Return>", lambda e: self.add_message())
        ttk.Button(add, text="추가", style="Ghost.TButton", command=self.add_message).grid(
            row=0, column=1, padx=(0, 6))
        ttk.Button(add, text="삭제", style="Ghost.TButton", command=self.del_message).grid(
            row=0, column=2)
        ttk.Button(g, text="데이터 전송", style="Primary.TButton",
                   command=self.request_confirm).pack(anchor="w", pady=(14, 0))
        tk.Label(g, text="누르면 로그아웃되고, 관리자가 같은 메세지를 입력해 일치할 때만 프린터로 "
                         "전송됩니다 (10분 이내)", bg=WHITE, fg=MUTED, font=(FONT, 8)).pack(
            anchor="w", pady=(6, 0))

    def _show_messages(self):
        self.tree.delete(*self.tree.get_children())
        for i, m in enumerate(self._msgs, 1):
            self.tree.insert("", "end", values=(i, m))

    def add_message(self):
        v = self.f_val.get()
        try:
            ngpcl.clean(v)
        except ngpcl.NgpclError as e:
            messagebox.showwarning("입력 확인", str(e))
            return
        if v == "" and not messagebox.askyesno(
                "메세지가 비어 있습니다",
                "보내는 메세지가 비어 있습니다.\n\n프린터의 이 칸을 빈 글자로 바꾸게 됩니다. "
                "이대로 추가할까요?"):
            return
        self._msgs.append(v)
        self._show_messages()
        self.f_val.delete(0, "end")
        self.f_val.focus_set()

    def del_message(self):
        for item in sorted((self.tree.index(i) for i in self.tree.selection()), reverse=True):
            del self._msgs[item]       # 지우면 번호는 다시 1부터 이어진다
        self._show_messages()

    def _fields(self):
        return messages.build_fields(self._msgs)

    # ---- 필드·설정 도구 ----
    def _tools(self, parent):
        c = Card(parent, "필드·설정 조회",
                 "프린터에 지금 들어 있는 글자(필드)와 설정 값을 눈으로 확인합니다. 설정을 바꾸는 것은 관리자 확인(이중 입력)이 필요합니다.")
        c.pack(fill="x", pady=(14, 0))
        g = c.body
        g.columnconfigure(1, weight=1)
        ttk.Button(g, text="프린터 메세지 조회", style="Ghost.TButton", command=self.query_fields).grid(
            row=0, column=0, columnspan=3, sticky="w")
        tk.Label(g, text="프린터의 현재 작업에 들어 있는 메세지를 번호 순서대로 보여 줍니다", bg=WHITE, fg=MUTED,
                 font=(FONT, 8)).grid(row=1, column=0, columnspan=3, sticky="w")
        self.q_tree = ttk.Treeview(g, columns=("name", "value"), show="headings", height=3)
        self.q_tree.heading("name", text="번호")
        self.q_tree.heading("value", text="프린터에 들어 있는 메세지")
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
                              justify="left", wraplength=px(470))
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
        on = self.client.status_on
        x = self._xchg()

        def work():
            return device_tools.query_fields(x, "", "", on)

        def done(rows):
            self.q_tree.delete(*self.q_tree.get_children())
            for n, v in rows:
                self.q_tree.insert("", "end", values=(messages.friendly(n), v))
            if rows:
                self._tool_result("메세지 %d개를 조회했습니다" % len(rows), verify.GREEN)
            else:
                self._tool_result("프린터에서 메세지를 찾지 못했습니다(현재 작업 확인)", verify.YELLOW)
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
        self.request_confirm(req)

    # ---- 전송 ----
    def _note(self, parent):
        """관리자·마스터 화면: 대기 중인 1차 요청이 있으면 2차 확인 입력으로 들어간다."""
        c = Card(parent, "2차 확인",
                 "작업자가 입력을 요청하면 여기서 같은 값을 다시 입력해 확인합니다. 두 값이 같아야 프린터로 전송됩니다.")
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

    def request_confirm(self, req=None):
        app = self.app
        if not self._ready():
            return
        if app.busy:    # 장비와 통신 중이면 로그아웃·요청을 미룬다(응답이 엇갈리지 않게)
            messagebox.showinfo("처리 중", "장비와 통신 중입니다. 끝난 뒤 다시 요청하세요.")
            return
        if req is None:
            job = self.picker.get()
            if not job:
                messagebox.showinfo("작업 선택", "먼저 [프린터에서 불러오기]로 작업을 선택하세요.")
                return
            if not self._msgs:
                messagebox.showinfo("보내는 메세지", "보내는 메세지를 한 개 이상 추가하세요.")
                return
            # 프린터의 현재 작업이면 메세지만 갱신, 다른 작업이면 작업을 선택하며 보낸다
            kind = "update" if job == self.picker.current else "select"
            try:
                req = dual.Request(kind, job, "0", self._fields(), True)
            except dual.DualError as e:
                messagebox.showwarning("입력 확인", str(e))
                return
        kind = req.kind
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

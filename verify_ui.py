"""수신 확인 결과 창: 신호등 + 장비가 받은 데이터 표."""
import tkinter as tk
from tkinter import ttk

import messages
import verify
from app_theme import BG, WHITE, NAVY, MUTED, LINE, OK, ERR, FONT

WARN = "#B7791F"
COLOR = {verify.GREEN: OK, verify.YELLOW: WARN, verify.RED: ERR}
HEADLINE = {
    verify.GREEN: "장비가 데이터를 정상적으로 받았습니다",
    verify.YELLOW: "받은 데이터를 확인해 주세요",
    verify.RED: "장비가 받은 데이터가 다릅니다",
}


def show(parent, title, checks, who_line="", start=None, name_label="번호"):
    """모달 창. checks: verify.Check 목록, start: (성공 여부, 설명, 신호) 작업 시작 결과. 닫으면 돌아온다."""
    win = tk.Toplevel(parent)
    win.title(title)
    win.configure(bg=BG)
    win.transient(parent.winfo_toplevel())
    win.resizable(False, False)
    light = verify.overall(checks)

    box = tk.Frame(win, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
    box.pack(fill="both", expand=True, padx=16, pady=16)
    head = tk.Frame(box, bg=WHITE)
    head.pack(fill="x", padx=16, pady=(14, 6))
    dot = tk.Canvas(head, width=44, height=44, bg=WHITE, highlightthickness=0)
    dot.create_oval(3, 3, 41, 41, fill=COLOR[light], outline="")
    dot.pack(side="left")
    txt = tk.Frame(head, bg=WHITE)
    txt.pack(side="left", padx=12)
    tk.Label(txt, text=HEADLINE[light], bg=WHITE, fg=COLOR[light],
             font=(FONT, 13, "bold"), anchor="w").pack(fill="x")
    if who_line:
        tk.Label(txt, text=who_line, bg=WHITE, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")

    tree = ttk.Treeview(box, columns=("light", "name", "sent", "got"), show="headings",
                        height=min(max(len(checks), 2), 10))
    for key, text, w in (("light", "판정", 130), ("name", name_label, 220),
                         ("sent", "보낸 메세지", 220), ("got", "프린터가 받은 메세지", 220)):
        tree.heading(key, text=text)
        tree.column(key, width=w, anchor="w")
    tree.tag_configure(verify.GREEN, foreground=OK)
    tree.tag_configure(verify.YELLOW, foreground=WARN)
    tree.tag_configure(verify.RED, foreground=ERR)
    for c in checks:
        tree.insert("", "end", tags=(c.light,),
                    values=("● " + verify.LABEL[c.light], messages.friendly(c.name), c.sent, c.got))
    if checks:
        tree.pack(fill="x", padx=16, pady=(8, 4))
    for c in checks:                  # 긴 안내는 표 밖에서 줄바꿈해 모두 보여 준다
        if c.note:
            tk.Label(box, text="· %s: %s" % (messages.friendly(c.name), c.note), bg=WHITE,
                     fg=COLOR.get(c.light, MUTED), font=(FONT, 10), anchor="w", justify="left",
                     wraplength=780).pack(fill="x", padx=16, pady=(2, 0))
    if start:
        row = tk.Frame(box, bg=WHITE)
        row.pack(fill="x", padx=16, pady=(6, 4))
        d2 = tk.Canvas(row, width=16, height=16, bg=WHITE, highlightthickness=0)
        d2.create_oval(2, 2, 14, 14, fill=COLOR[start[2]] if start[0] else
                       (ERR if start[2] == verify.RED else WARN), outline="")
        d2.pack(side="left")
        tk.Label(row, text="작업 시작: " + start[1], bg=WHITE, font=(FONT, 10, "bold"),
                 fg=OK if start[0] else (ERR if start[2] == verify.RED else WARN),
                 anchor="w", justify="left", wraplength=560).pack(side="left", padx=8)
    tk.Label(box, text="※ 되읽기로 확인한 것은 장비 작업의 필드 값입니다(실제 인쇄 결과 확인은 아님)",
             bg=WHITE, fg=MUTED, font=(FONT, 8), anchor="w").pack(fill="x", padx=16)
    ttk.Button(box, text="확인", style="Primary.TButton", command=win.destroy).pack(
        pady=(10, 14))

    win.update_idletasks()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    win.geometry("%dx%d+%d+%d" % (win.winfo_reqwidth(), win.winfo_reqheight(), px + 60, py + 60))
    win.grab_set()
    win.focus_set()
    parent.wait_window(win)


def show_failure(parent, title, reason, who_line="", hint="1차 입력부터 다시 해야 합니다."):
    """전송되지 않았을 때: 빨간 신호등 + 사유. 닫으면 돌아온다."""
    win = tk.Toplevel(parent)
    win.title(title)
    win.configure(bg=BG)
    win.transient(parent.winfo_toplevel())
    win.resizable(False, False)
    box = tk.Frame(win, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
    box.pack(fill="both", expand=True, padx=16, pady=16)
    head = tk.Frame(box, bg=WHITE)
    head.pack(fill="x", padx=16, pady=(14, 6))
    dot = tk.Canvas(head, width=44, height=44, bg=WHITE, highlightthickness=0)
    dot.create_oval(3, 3, 41, 41, fill=ERR, outline="")
    dot.pack(side="left")
    txt = tk.Frame(head, bg=WHITE)
    txt.pack(side="left", padx=12)
    tk.Label(txt, text="전송되지 않았습니다", bg=WHITE, fg=ERR, font=(FONT, 13, "bold"),
             anchor="w").pack(fill="x")
    if who_line:
        tk.Label(txt, text=who_line, bg=WHITE, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")
    tk.Label(box, text="사유: " + reason, bg=WHITE, fg=ERR, font=(FONT, 10), anchor="w",
             justify="left", wraplength=440).pack(fill="x", padx=16, pady=(8, 2))
    if hint:
        tk.Label(box, text=hint, bg=WHITE, fg=MUTED, font=(FONT, 9), anchor="w").pack(
            fill="x", padx=16)
    ttk.Button(box, text="확인", style="Primary.TButton", command=win.destroy).pack(
        pady=(12, 14))
    win.update_idletasks()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    win.geometry("%dx%d+%d+%d" % (win.winfo_reqwidth(), win.winfo_reqheight(), px + 60, py + 60))
    win.grab_set()
    win.focus_set()
    parent.wait_window(win)


def show_print_control(parent, title, checks, who_line, on_action):
    """전송 결과 + 인쇄 시작·정지 창(2차 작업자용). 닫으면 돌아온다.

    on_action(kind, set_status, lock): kind 는 "start" 또는 "stop".
      set_status(text, light) 로 결과 줄을 바꾸고, lock(False) 로 버튼을 다시 풀어 준다.
    인쇄 시작은 되읽기가 모두 초록일 때만 누를 수 있다.
    """
    win = tk.Toplevel(parent)
    win.title(title)
    win.configure(bg=BG)
    win.transient(parent.winfo_toplevel())
    win.resizable(False, False)
    light = verify.overall(checks) if checks else verify.GREEN

    box = tk.Frame(win, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
    box.pack(fill="both", expand=True, padx=16, pady=16)
    head = tk.Frame(box, bg=WHITE)
    head.pack(fill="x", padx=16, pady=(14, 6))
    dot = tk.Canvas(head, width=44, height=44, bg=WHITE, highlightthickness=0)
    dot.create_oval(3, 3, 41, 41, fill=COLOR[light], outline="")
    dot.pack(side="left")
    txt = tk.Frame(head, bg=WHITE)
    txt.pack(side="left", padx=12)
    tk.Label(txt, text=HEADLINE[light], bg=WHITE, fg=COLOR[light], font=(FONT, 13, "bold"),
             anchor="w").pack(fill="x")
    if who_line:
        tk.Label(txt, text=who_line, bg=WHITE, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")

    tk.Label(box, text="보낸 데이터", bg=WHITE, fg=NAVY, font=(FONT, 11, "bold"),
             anchor="w").pack(fill="x", padx=16, pady=(8, 2))
    tree = ttk.Treeview(box, columns=("light", "no", "sent", "got"), show="headings",
                        height=min(max(len(checks), 2), 10))
    for key, text, w in (("light", "판정", 120), ("no", "번호", 110),
                         ("sent", "보낸 메세지", 260), ("got", "프린터가 받은 메세지", 260)):
        tree.heading(key, text=text)
        tree.column(key, width=w, anchor="w")
    tree.tag_configure(verify.GREEN, foreground=OK)
    tree.tag_configure(verify.YELLOW, foreground=WARN)
    tree.tag_configure(verify.RED, foreground=ERR)
    for c in checks:
        tree.insert("", "end", tags=(c.light,),
                    values=("● " + verify.LABEL[c.light], messages.friendly(c.name), c.sent, c.got))
    tree.pack(fill="x", padx=16, pady=(0, 4))
    for c in checks:
        if c.note:
            tk.Label(box, text="· %s: %s" % (messages.friendly(c.name), c.note), bg=WHITE,
                     fg=COLOR.get(c.light, MUTED), font=(FONT, 10), anchor="w", justify="left",
                     wraplength=780).pack(fill="x", padx=16, pady=(2, 0))

    status = tk.Label(box, text="인쇄를 시작하려면 [인쇄 시작], 멈추려면 [정지]를 누르세요. "
                      "(3분 안에 선택하지 않으면 창이 닫히고 로그아웃됩니다)",
                      bg=WHITE, fg=MUTED, font=(FONT, 10, "bold"), anchor="w", justify="left",
                      wraplength=780)
    status.pack(fill="x", padx=16, pady=(10, 4))
    btns = tk.Frame(box, bg=WHITE)
    btns.pack(pady=(4, 14))
    b_start = ttk.Button(btns, text="인쇄 시작", style="Primary.TButton")
    b_stop = ttk.Button(btns, text="정지", style="Ghost.TButton")
    b_close = ttk.Button(btns, text="닫기", style="Ghost.TButton", command=win.destroy)
    for b in (b_start, b_stop, b_close):
        b.pack(side="left", padx=6)
    can_start = light == verify.GREEN and bool(checks)
    state = {"locked": False}
    if not can_start:
        b_start.configure(state="disabled")
        status.configure(text="수신 확인이 초록이 아니어서 [인쇄 시작]을 누를 수 없습니다. "
                              "내용을 확인하세요.", fg=WARN)

    def set_status(text, lt):
        if win.winfo_exists():
            status.configure(text=text, fg={verify.GREEN: OK, verify.YELLOW: WARN,
                                            verify.RED: ERR}.get(lt, MUTED))

    def lock(on):
        if not win.winfo_exists():
            return
        state["locked"] = bool(on)
        b_start.configure(state="disabled" if (on or not can_start) else "normal")
        b_stop.configure(state="disabled" if on else "normal")
        b_close.configure(state="disabled" if on else "normal")

    def act(kind):
        lock(True)
        set_status("처리 중…", None)
        on_action(kind, set_status, lock)

    b_start.configure(command=lambda: act("start"))
    b_stop.configure(command=lambda: act("stop"))
    # 프린터와 통신 중에는 창 닫기(X)를 막는다(다음 사용자와 응답이 엇갈리지 않게)
    win.protocol("WM_DELETE_WINDOW", lambda: None if state["locked"] else win.destroy())

    def auto_close():     # 자리를 비운 채 두지 않도록 3분 뒤 닫는다(닫으면 로그아웃)
        if not win.winfo_exists():
            return
        if state["locked"]:
            win.after(5000, auto_close)
        else:
            win.destroy()
    win.after(180000, auto_close)

    win.update_idletasks()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    win.geometry("%dx%d+%d+%d" % (win.winfo_reqwidth(), win.winfo_reqheight(), px + 60, py + 60))
    win.grab_set()
    win.focus_set()
    parent.wait_window(win)

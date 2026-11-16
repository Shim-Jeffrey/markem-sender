"""수신 확인 결과 창: 신호등 + 장비가 받은 데이터 표."""
import tkinter as tk
from tkinter import ttk

import verify
from app_theme import BG, WHITE, NAVY, MUTED, LINE, OK, ERR, FONT

WARN = "#B7791F"
COLOR = {verify.GREEN: OK, verify.YELLOW: WARN, verify.RED: ERR}
HEADLINE = {
    verify.GREEN: "장비가 데이터를 정상적으로 받았습니다",
    verify.YELLOW: "받은 데이터를 확인해 주세요",
    verify.RED: "장비가 받은 데이터가 다릅니다",
}


def show(parent, title, checks, who_line=""):
    """모달 창. checks: verify.Check 목록. 닫으면 돌아온다."""
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

    tree = ttk.Treeview(box, columns=("light", "name", "sent", "got", "note"), show="headings",
                        height=min(max(len(checks), 2), 10))
    for key, text, w in (("light", "판정", 100), ("name", "필드 이름", 150),
                         ("sent", "보낸 값", 170), ("got", "장비가 받은 값", 170),
                         ("note", "비고", 200)):
        tree.heading(key, text=text)
        tree.column(key, width=w, anchor="w")
    tree.tag_configure(verify.GREEN, foreground=OK)
    tree.tag_configure(verify.YELLOW, foreground=WARN)
    tree.tag_configure(verify.RED, foreground=ERR)
    for c in checks:
        tree.insert("", "end", tags=(c.light,),
                    values=("● " + verify.LABEL[c.light], c.name, c.sent, c.got, c.note))
    tree.pack(fill="x", padx=16, pady=(8, 4))
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

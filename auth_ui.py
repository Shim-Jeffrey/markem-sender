"""로그인·최초 마스터 설정·비밀번호 변경·계정 관리·기록 화면 (설계: docs/설계-이중입력.md)."""
import datetime
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

import store as S
from app_theme import (BG, WHITE, NAVY, MUTED, LINE, ERR, TEXT, FONT, Card)

def _entry(parent, label, row, secret=False, width=28):
    tk.Label(parent, text=label, bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(
        row=row, column=0, sticky="w", pady=5, padx=(0, 12))
    e = ttk.Entry(parent, width=width, show="●" if secret else "")
    e.grid(row=row, column=1, sticky="we", pady=5)
    return e


class AuthBox(tk.Frame):
    """화면 가운데 놓는 카드(로그인류 공통)."""

    def __init__(self, parent, title, note=""):
        super().__init__(parent, bg=BG)
        outer = tk.Frame(self, bg=BG)
        outer.place(relx=0.5, rely=0.42, anchor="center")
        self.card = Card(outer, title)
        self.card.pack()
        self.form = self.card.body
        self.form.columnconfigure(1, weight=1)
        if note:
            tk.Label(self.form, text=note, bg=WHITE, fg=MUTED, font=(FONT, 9), justify="left",
                     wraplength=360).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.msg = tk.Label(self.form, text="", bg=WHITE, fg=ERR, font=(FONT, 9), anchor="w",
                            wraplength=360, justify="left")

    def finish(self, row, button_text, command, first_entry, last_entry):
        self.msg.grid(row=row, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Button(self.form, text=button_text, style="Primary.TButton", command=command).grid(
            row=row + 1, column=0, columnspan=2, sticky="we", pady=(10, 0))
        last_entry.bind("<Return>", lambda e: command())
        first_entry.focus_set()

    def error(self, text):
        self.msg.configure(text=text)


class LoginFrame(AuthBox):
    def __init__(self, parent, app, notice=""):
        super().__init__(parent, "로그인", notice)
        self.app = app
        r = 1 if notice else 0
        self.user = _entry(self.form, "아이디", r)
        self.pw = _entry(self.form, "비밀번호", r + 1, secret=True)
        self.finish(r + 2, "로그인", self.submit, self.user, self.pw)

    def submit(self):
        try:
            u = self.app.store.login(self.user.get(), self.pw.get())
        except S.AuthError as e:
            self.pw.delete(0, "end")
            self.error(str(e))
            return
        self.app.after_login(u)


class SetupFrame(AuthBox):
    def __init__(self, parent, app):
        super().__init__(parent, "처음 설정 — 마스터 계정 만들기",
                         "마스터는 계정을 만들고 지우는 관리자입니다. 아이디와 비밀번호를 "
                         "잊지 않도록 따로 보관하세요.")
        self.app = app
        self.user = _entry(self.form, "아이디", 1)
        self.name = _entry(self.form, "이름", 2)
        self.pw = _entry(self.form, "비밀번호", 3, secret=True)
        self.pw2 = _entry(self.form, "비밀번호 확인", 4, secret=True)
        tk.Label(self.form, text="8자 이상, 영문과 숫자 포함", bg=WHITE, fg=MUTED,
                 font=(FONT, 8)).grid(row=5, column=1, sticky="w")
        self.finish(6, "마스터 만들기", self.submit, self.user, self.pw2)

    def submit(self):
        if self.pw.get() != self.pw2.get():
            self.error("비밀번호 확인이 다릅니다")
            return
        try:
            self.app.store.create_first_master(self.user.get(), self.name.get(), self.pw.get())
            u = self.app.store.login(self.user.get(), self.pw.get())
        except S.AuthError as e:
            self.error(str(e))
            return
        self.app.after_login(u)


class ChangePasswordFrame(AuthBox):
    def __init__(self, parent, app, user):
        super().__init__(parent, "비밀번호 변경",
                         "처음 로그인했거나 초기화된 계정입니다. 새 비밀번호를 정해 주세요.")
        self.app, self.u = app, user
        self.old = _entry(self.form, "현재(임시) 비밀번호", 1, secret=True)
        self.new = _entry(self.form, "새 비밀번호", 2, secret=True)
        self.new2 = _entry(self.form, "새 비밀번호 확인", 3, secret=True)
        self.finish(4, "변경", self.submit, self.old, self.new2)

    def submit(self):
        if self.new.get() != self.new2.get():
            self.error("새 비밀번호 확인이 다릅니다")
            return
        try:
            self.app.store.change_password(self.u, self.old.get(), self.new.get())
        except S.AuthError as e:
            self.error(str(e))
            return
        self.app.enter_main(self.u)


class AccountsPage(tk.Frame):
    """마스터 전용: 계정 만들기·비활성화·삭제·비밀번호 초기화·잠금 해제."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0, minsize=330)
        self.rowconfigure(0, weight=1)
        left = Card(self, "계정 목록",
                    "프로그램을 쓰는 사람 목록입니다. 사람을 고른 뒤 아래 버튼으로 비밀번호 초기화·잠금 해제·비활성·삭제를 합니다.")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self.tree = ttk.Treeview(left.body, columns=("id", "name", "role", "state"),
                                 show="headings", height=10, selectmode="browse")
        for c, t, w in (("id", "아이디", 130), ("name", "이름", 130), ("role", "역할", 130),
                        ("state", "상태", 190)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w)
        self.tree.pack(fill="both", expand=True)
        row = tk.Frame(left.body, bg=WHITE)
        row.pack(fill="x", pady=(10, 0))
        for text, cmd in (("비활성화/활성화", self.toggle), ("비밀번호 초기화", self.reset),
                          ("잠금 해제", self.unlock), ("삭제", self.delete)):
            ttk.Button(row, text=text, style="Ghost.TButton", command=cmd).pack(side="left",
                                                                                padx=(0, 8))
        right = Card(self, "계정 추가",
                     "새로 쓸 사람을 만듭니다. 처음 비밀번호는 임시이며 첫 로그인 때 본인이 바꿉니다. 작업자=입력, 관리자=확인.")
        right.grid(row=0, column=1, sticky="nsew")
        g = right.body
        g.columnconfigure(1, weight=1)
        self.f_user = _entry(g, "아이디", 0, width=20)
        self.f_name = _entry(g, "이름", 1, width=20)
        tk.Label(g, text="역할", bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(row=2, column=0,
                                                                         sticky="w", pady=5)
        self._roles = [("worker", S.ROLE_LABEL["worker"]), ("manager", S.ROLE_LABEL["manager"])]
        if app.user.org == "tasco":
            self._roles.append(("master", "마스터(거래처)"))
        self.f_role = ttk.Combobox(g, state="readonly", width=18,
                                   values=[lab for _, lab in self._roles])
        self.f_role.current(0)
        self.f_role.grid(row=2, column=1, sticky="we", pady=5)
        self.f_pw = _entry(g, "임시 비밀번호", 3, width=20)
        ttk.Button(g, text="자동 생성", style="Ghost.TButton", command=self.gen).grid(
            row=4, column=1, sticky="e", pady=(0, 6))
        tk.Label(g, text="처음 로그인할 때 본인이 비밀번호를 바꿉니다", bg=WHITE, fg=MUTED,
                 font=(FONT, 8)).grid(row=5, column=0, columnspan=2, sticky="w")
        ttk.Button(g, text="계정 만들기", style="Primary.TButton", command=self.create).grid(
            row=6, column=0, columnspan=2, sticky="we", pady=(12, 0))
        self.msg = tk.Label(g, text="", bg=WHITE, fg=ERR, font=(FONT, 9), wraplength=290,
                            justify="left", anchor="w")
        self.msg.grid(row=7, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.refresh()

    def _sel(self):
        s = self.tree.selection()
        if not s:
            messagebox.showinfo("계정 선택", "목록에서 계정을 먼저 선택하세요")
            return None
        return int(s[0])

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for u in self.app.store.list_users(self.app.user):
            state = "잠김" if u.locked else ("사용" if u.active else "비활성")
            if u.must_change:
                state += " · 변경 대기"
            self.tree.insert("", "end", iid=str(u.id),
                             values=(u.username, u.display_name, u.label, state))

    def _do(self, fn):
        try:
            fn()
        except S.AuthError as e:
            self.msg.configure(text=str(e))
            return False
        self.msg.configure(text="")
        self.refresh()
        return True

    def gen(self):
        self.f_pw.delete(0, "end")
        self.f_pw.insert(0, S.gen_temp_password())

    def create(self):
        role = self._roles[self.f_role.current()][0]
        pw = self.f_pw.get()
        user = self.f_user.get()
        if self._do(lambda: self.app.store.create_user(self.app.user, user, self.f_name.get(),
                                                       role, pw)):
            messagebox.showinfo("계정을 만들었습니다",
                                "아이디: %s\n임시 비밀번호: %s\n\n본인에게 전달하세요. "
                                "처음 로그인할 때 비밀번호를 바꾸게 됩니다." % (user.strip().lower(), pw))
            for e in (self.f_user, self.f_name, self.f_pw):
                e.delete(0, "end")

    def toggle(self):
        i = self._sel()
        if i is None:
            return
        u = next(x for x in self.app.store.list_users(self.app.user) if x.id == i)
        self._do(lambda: self.app.store.set_active(self.app.user, i, not u.active))

    def reset(self):
        i = self._sel()
        if i is None:
            return
        pw = S.gen_temp_password()
        if self._do(lambda: self.app.store.reset_password(self.app.user, i, pw)):
            messagebox.showinfo("비밀번호 초기화", "임시 비밀번호: %s\n\n본인에게 전달하세요. "
                                "다음 로그인 때 새 비밀번호로 바꾸게 됩니다." % pw)

    def unlock(self):
        i = self._sel()
        if i is not None:
            self._do(lambda: self.app.store.unlock(self.app.user, i))

    def delete(self):
        i = self._sel()
        if i is None:
            return
        name = self.tree.item(str(i), "values")[0]
        if messagebox.askyesno("계정 삭제", "'%s' 계정을 삭제할까요?\n로그인이 막히고 목록에서 "
                               "사라집니다. 과거 기록은 남고, 같은 아이디는 다시 쓸 수 "
                               "없습니다." % name):
            self._do(lambda: self.app.store.delete_user(self.app.user, i))


class EventsPage(tk.Frame):
    """마스터 전용: 로그인·계정 변경·작업 기록 조회, 무결성 확인, CSV 내보내기."""

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        card = Card(self, "기록",
                    "누가 언제 무엇을 했는지 남은 기록입니다. 종류별로 보고, 엑셀(CSV)로 저장하거나 변조 여부를 확인할 수 있습니다.")
        card.pack(fill="both", expand=True)
        top = tk.Frame(card.body, bg=WHITE)
        top.pack(fill="x", pady=(0, 8))
        tk.Label(top, text="보기", bg=WHITE, fg=MUTED, font=(FONT, 9)).pack(side="left")
        self.group = ttk.Combobox(top, state="readonly", width=10, values=list(S.EVENT_GROUPS))
        self.group.current(0)
        self.group.pack(side="left", padx=(6, 10))
        self.group.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Button(top, text="새로고침", style="Ghost.TButton", command=self.refresh).pack(
            side="left")
        ttk.Button(top, text="기록 무결성 확인", style="Ghost.TButton",
                   command=self.verify).pack(side="left", padx=8)
        exp = tk.Frame(card.body, bg=WHITE)
        exp.pack(fill="x", pady=(0, 8))
        tk.Label(exp, text="CSV 기간", bg=WHITE, fg=MUTED, font=(FONT, 9)).pack(side="left")
        self.since = ttk.Entry(exp, width=11)
        self.until = ttk.Entry(exp, width=11)
        self.since.pack(side="left", padx=(6, 4))
        tk.Label(exp, text="~", bg=WHITE, fg=MUTED).pack(side="left")
        self.until.pack(side="left", padx=4)
        tk.Label(exp, text="(YYYY-MM-DD, 비우면 전체)", bg=WHITE, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=(2, 12))
        ttk.Button(exp, text="작업 로그 CSV", style="Ghost.TButton",
                   command=self.export_work).pack(side="left")
        ttk.Button(exp, text="계정 변경 CSV", style="Ghost.TButton",
                   command=lambda: self.export_events("계정 변경")).pack(side="left", padx=8)
        ttk.Button(exp, text="로그인 CSV", style="Ghost.TButton",
                   command=lambda: self.export_events("로그인")).pack(side="left")
        self.tree = ttk.Treeview(card.body, columns=("ts", "type", "actor", "detail"),
                                 show="headings", height=14)
        for c, t, w in (("ts", "시각", 140), ("type", "종류", 130), ("actor", "계정", 110),
                        ("detail", "내용", 520)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w)
        self.tree.pack(fill="both", expand=True)
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for e in self.app.store.events(self.app.user, group=self.group.get()):
            self.tree.insert("", "end", values=(e["ts"].replace("T", " "),
                                                S.EVENT_LABEL.get(e["type"], e["type"]),
                                                e["actor"], S.format_detail(e["detail"], e["type"])))

    def _dates(self):
        out = []
        for e in (self.since, self.until):
            v = e.get().strip()
            if v:
                try:
                    datetime.date.fromisoformat(v)
                except ValueError:
                    messagebox.showwarning("기간", "날짜는 YYYY-MM-DD 형식으로 입력하세요")
                    return None
            out.append(v or None)
        return out

    def _export(self, default_name, fn):
        d = self._dates()
        if d is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=default_name,
                                            filetypes=[("CSV 파일", "*.csv")])
        if not path:
            return
        try:
            n = fn(path, *d)
        except (S.AuthError, OSError) as e:
            messagebox.showerror("내보내기", str(e))
            return
        messagebox.showinfo("내보내기", "%d건을 저장했습니다\n%s" % (n, path))
        self.refresh()

    def export_work(self):
        self._export("작업로그.csv", lambda p, a, b: self.app.store.export_work_csv(
            self.app.user, p, a, b))

    def export_events(self, group):
        self._export("%s기록.csv" % group.replace(" ", ""),
                     lambda p, a, b: self.app.store.export_event_csv(self.app.user, p, group, a, b))

    def verify(self):
        try:
            ok, why = self.app.store.verify_chain(self.app.user)
        except S.AuthError as e:
            messagebox.showerror("기록 무결성", str(e))
            return
        if ok:
            messagebox.showinfo("기록 무결성", "이상 없음: 기록이 중간에 고쳐지거나 지워지지 않았습니다")
        else:
            messagebox.showerror("기록 무결성", "경고: %s" % why)

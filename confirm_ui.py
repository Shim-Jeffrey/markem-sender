"""2차(관리자) 확인 입력 화면. 설계: docs/설계-이중입력.md"""
import tkinter as tk
from tkinter import ttk, messagebox

import device_tools
import dual
import ngpcl
import store as S
import verify
import verify_ui
from app_theme import BG, WHITE, NAVY, MUTED, LINE, ERR, OK, FONT, Card


def no_paste(widget):
    """2차 입력칸은 붙여넣기를 막는다(1차와 같은 오타가 복사되어 들어가는 것을 방지)."""
    for seq in ("<<Paste>>", "<Control-v>", "<Control-V>", "<Shift-Insert>", "<Button-2>"):
        widget.bind(seq, lambda e: "break")


class ManagerEntryFrame(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self.pad = tk.Frame(self, bg=BG)
        self.pad.pack(fill="both", expand=True, padx=22, pady=16)
        card = Card(self.pad, "2차 확인 입력")
        card.pack(fill="both", expand=True)
        g = card.body
        w = app.dual.pending_worker()
        self.info = tk.Label(g, text="", bg=WHITE, fg=NAVY, font=(FONT, 10, "bold"), anchor="w")
        self.info.pack(fill="x")
        self.w_name = w.display_name
        tk.Label(g, text="1차 작업자가 입력한 값을 보지 않고, 원본 자료를 보며 직접 입력하세요. "
                         "(붙여넣기는 막혀 있습니다)", bg=WHITE, fg=MUTED, font=(FONT, 9),
                 anchor="w").pack(fill="x", pady=(4, 12))

        self.is_setting = app.dual.pending_kind() == "setting"
        top = tk.Frame(g, bg=WHITE)
        if not self.is_setting:          # 설정 변경은 작업 이름·할당 없이 이름/값 한 쌍만
            top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        tk.Label(top, text="작업 이름", bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        self.job = ttk.Entry(top)
        self.job.grid(row=0, column=1, sticky="we")
        tk.Label(top, text="할당 수", bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(
            row=0, column=2, sticky="w", padx=(18, 10))
        self.alloc = ttk.Entry(top, width=10)
        self.alloc.insert(0, "0")
        self.alloc.grid(row=0, column=3)
        self.v_imaged = tk.BooleanVar(value=True)
        if not self.is_setting:
            ttk.Checkbutton(g, text="이미징이 끝난 뒤 응답 받기", variable=self.v_imaged).pack(
                anchor="w", pady=(8, 8))

        self.tree = ttk.Treeview(g, columns=("name", "value"), show="headings",
                                 height=2 if self.is_setting else 6)
        self.tree.heading("name", text="설정 이름" if self.is_setting else "필드 이름")
        self.tree.heading("value", text="설정 값" if self.is_setting else "값")
        self.tree.column("name", width=180)
        self.tree.column("value", width=320)
        self.tree.pack(fill="both", expand=True)
        add = tk.Frame(g, bg=WHITE)
        add.pack(fill="x", pady=(10, 0))
        add.columnconfigure(0, weight=1)
        add.columnconfigure(1, weight=2)
        self.f_name, self.f_val = ttk.Entry(add), ttk.Entry(add)
        for e in (self.job, self.alloc, self.f_name, self.f_val):
            no_paste(e)
        self.f_name.grid(row=0, column=0, sticky="we", padx=(0, 8))
        self.f_val.grid(row=0, column=1, sticky="we", padx=(0, 8))
        self.f_val.bind("<Return>", lambda e: self.add_field())
        ttk.Button(add, text="추가", style="Ghost.TButton", command=self.add_field).grid(
            row=0, column=2, padx=(0, 6))
        ttk.Button(add, text="삭제", style="Ghost.TButton", command=self.del_field).grid(
            row=0, column=3)

        self.msg = tk.Label(g, text="", bg=WHITE, fg=ERR, font=(FONT, 10, "bold"), anchor="w",
                            wraplength=700, justify="left")
        self.msg.pack(fill="x", pady=(12, 0))
        row = tk.Frame(g, bg=WHITE)
        row.pack(fill="x", pady=(8, 0))
        self.b_ok = ttk.Button(row, text="확인 및 전송", style="Primary.TButton", command=self.submit)
        self.b_ok.pack(side="left")
        self.b_cancel = ttk.Button(row, text="취소(로그아웃)" if app.user.role != "master" else "취소",
                                   style="Ghost.TButton", command=self.cancel)
        self.b_cancel.pack(
            side="left", padx=8)
        self._tick()
        self.job.focus_set()

    # ---- 표시 ----
    def _tick(self):
        if not self.winfo_exists():
            return
        left = self.app.dual.seconds_left()
        kind = dual.KIND_LABEL.get(self.app.dual.pending_kind() or "", "")
        self.info.configure(text="1차 작업자 %s  ·  %s  ·  남은 시간 %02d:%02d  ·  불일치 %d/%d"
                                 % (self.w_name, kind, left // 60, left % 60,
                                    self.app.dual.tries(), dual.MAX_TRIES))
        self.after(1000, self._tick)

    def add_field(self):
        n, v = self.f_name.get().strip(), self.f_val.get()
        if not n:
            return
        if self.is_setting and self.tree.get_children():
            self.msg.configure(text="설정 변경은 이름과 값 한 쌍만 입력합니다. 먼저 삭제하세요.")
            return
        self.tree.insert("", "end", values=(n, v))
        self.f_name.delete(0, "end")
        self.f_val.delete(0, "end")
        self.f_name.focus_set()

    def del_field(self):
        for i in self.tree.selection():
            self.tree.delete(i)

    def _clear_inputs(self):
        self.job.delete(0, "end")
        self.alloc.delete(0, "end")
        self.alloc.insert(0, "0")
        self.f_name.delete(0, "end")
        self.f_val.delete(0, "end")
        self.tree.delete(*self.tree.get_children())
        self.v_imaged.set(True)

    # ---- 동작 ----
    def cancel(self):
        if self.app.user and self.app.user.role == "master":
            self.app.enter_main(self.app.user)     # 마스터는 원래 화면으로
        else:
            self.app.logout("확인을 취소했습니다. 1차 입력은 남은 시간 동안 유지됩니다")

    def submit(self):
        app = self.app
        d = app.dual
        if not app.client.connected:
            self.msg.configure(text="장비에 연결되어 있지 않습니다. 취소하고 작업자가 연결을 확인하세요.")
            return
        pending = d._p                       # 기록용(1차 정보)
        if not app.store.user_usable(pending["worker"].id, "worker"):
            # 승인 대기 중 1차 작업자 계정이 삭제·비활성·잠금 되었다
            pw = pending["worker"]
            d.discard()
            app.store.log_work_safe("work_fail", pw, app.user, job=pending["request"].job,
                                    kind=dual.KIND_LABEL[pending["request"].kind],
                                    reason="1차 작업자 계정을 더 이상 쓸 수 없어 폐기")
            messagebox.showwarning("확인 불가", "1차 작업자 계정을 더 이상 쓸 수 없어 요청이 폐기되었습니다.")
            app.logout("1차 요청이 폐기되었습니다. 1차 작업자부터 다시 입력하세요")
            return
        try:
            req = dual.Request(d.pending_kind(), self.job.get(), self.alloc.get(),
                               [tuple(self.tree.item(i, "values")) for i in self.tree.get_children()],
                               self.v_imaged.get())
        except dual.DualError as e:
            self.msg.configure(text=str(e))
            return
        try:
            res = d.verify(app.user, req)
        except dual.DualError as e:
            self.msg.configure(text=str(e))
            return
        w, m = pending["worker"], app.user
        job, data = pending["request"].job, pending["request"].data_text()
        kind = dual.KIND_LABEL[pending["request"].kind]

        if res.status == "retry":
            app.store.log_work_safe("work_retry", w, m, job=job, kind=kind, tries=res.tries,
                               fields=res.fields)
            self.msg.configure(text="입력이 일치하지 않습니다 (%d/%d회). 다시 입력하세요 — 남은 기회 %d회"
                                    % (res.tries, dual.MAX_TRIES, res.left))
            self._clear_inputs()
            self.job.focus_set()
        elif res.status == "reset":
            app.store.log_work_safe("work_fail", w, m, job=job, kind=kind, data=data, tries=res.tries,
                               fields=res.fields,
                               reason="2차 입력 %d회 불일치 — 1차 입력 폐기, 1차부터 다시" % res.tries)
            messagebox.showwarning("입력 불일치",
                                   "2차 입력이 %d회 일치하지 않았습니다.\n1차 입력이 폐기되었습니다. "
                                   "1차 작업자가 처음부터 다시 입력해야 합니다." % res.tries)
            app.logout("2차 입력 %d회 불일치로 1차 입력이 폐기되었습니다. 1차 작업자부터 다시 입력하세요"
                       % res.tries)
        elif res.status == "expired":
            app.store.log_work_safe("work_fail", w, m, job=job, kind=kind, reason="1차 입력 만료(10분)")
            messagebox.showwarning("시간 초과", "1차 입력이 만료되었습니다. 1차 작업자부터 다시 입력하세요.")
            app.logout("1차 입력이 만료되었습니다. 1차 작업자부터 다시 입력하세요")
        else:
            self._send(res.approval, w, m, job, kind, data, res.tries, pending["request"])

    def _send(self, approval, w, m, job, kind, data, mismatches, request):
        app = self.app
        self.b_ok.configure(state="disabled")
        self.b_cancel.configure(state="disabled")      # 전송 중에는 화면을 떠나지 못하게
        self.msg.configure(text="일치합니다. 장비로 전송하는 중…", fg=NAVY)

        def xchg(message, timeout):
            sent, got = app.client.request(message, timeout)
            app.log("TX", sent)
            app.log("RX", got)
            return got

        if request.kind == "setting":
            self._send_setting(approval, w, m, xchg, mismatches, request)
            return

        def work():
            r = dual.run(app.client, approval, xchg)
            checks = None
            if r[0] and request.fields:     # 전송 성공 → 장비가 받은 값을 되읽어 신호등으로 확인
                def xchg_rb(message, timeout):
                    try:
                        return xchg(message, timeout)
                    except ngpcl.NgpclError:
                        app.client.close()   # 늦게 온 응답이 다음 명령과 짝지어지지 않도록 링크를 끊는다
                        raise
                checks = verify.read_back(xchg_rb, request.job, request.fields, app.client.status_on)
            return r, checks

        def done(res):
            (ok, why), checks = res
            if ok:
                who = "1차 %s · 2차 %s" % (w.display_name, m.display_name)
                app.store.log_work_safe("work_ok", w, m, job=job, kind=kind, data=data,
                                   tries=mismatches or "", verify=verify.summary(checks))
                if checks:
                    verify_ui.show(self, "전송 완료 · 수신 확인", checks, "작업 성공 · " + who)
                else:
                    messagebox.showinfo("전송 완료", "작업 성공\n" + who)
                app.logout("작업 성공: %s (1차 %s / 2차 %s)%s" % (
                    kind, w.display_name, m.display_name,
                    " · 수신확인 " + verify.summary(checks) if checks else ""))
            else:
                app.store.log_work_safe("work_fail", w, m, job=job, kind=kind, data=data,
                                   tries=mismatches or "", reason=why)
                messagebox.showerror("전송 실패", "작업 실패\n%s\n\n1차 작업자부터 다시 입력해야 합니다." % why)
                app.logout("작업 실패: %s" % why)
        app.run(work, done)

    def _send_setting(self, approval, w, m, xchg, mismatches, request):
        """설정 변경(`~SC`): 일치 판정 후 전송하고 `~SR` 로 되읽어 신호등으로 확인한다."""
        app = self.app
        req = approval.consume()
        name, value = req.fields[0]
        kind, data = dual.KIND_LABEL["setting"], req.data_text()
        who = "1차 %s · 2차 %s" % (w.display_name, m.display_name)

        def x(message, timeout):
            try:
                return xchg(message, timeout)
            except ngpcl.NgpclError:
                app.client.close()      # 늦게 온 응답이 다음 명령과 짝지어지지 않도록 링크를 끊는다
                raise

        def work():
            try:
                return device_tools.set_setting(x, name, value, app.client.status_on)
            except (device_tools.ToolError, ngpcl.NgpclError) as e:
                return False, str(e), "", "", verify.RED

        def done(r):
            ok, note, before, after, light = r
            change = "이전 %s → 이후 %s" % (before or "?", after or "?")
            if ok and light != verify.RED:
                app.store.log_work_safe("work_ok", w, m, kind=kind, data=data,
                                        tries=mismatches or "",
                                        verify="%s: %s" % (verify.SUMMARY[light], change))
                chk = [verify.Check(name, value, after, light, note if light != verify.GREEN
                                    else change)]
                verify_ui.show(self, "설정 변경 완료 · 수신 확인", chk, "작업 성공 · " + who)
                app.logout("작업 성공: %s %s (%s / %s)" % (kind, data, who, change))
            else:
                if ok:     # 장비가 받아들였지만 되읽은 값이 다름 → 성공으로 기록하지 않는다
                    why = "장비가 변경을 받아들였으나 되읽은 값이 다름(%s)" % change
                else:
                    why = note if ok is False else "응답 확인 불가: " + note
                app.store.log_work_safe("work_fail", w, m, kind=kind, data=data,
                                        tries=mismatches or "", reason=why,
                                        verify="%s: %s" % (verify.SUMMARY[light], change)
                                        if ok else "")
                messagebox.showerror("전송 실패", "작업 실패\n%s\n\n1차 작업자부터 다시 입력해야 합니다." % why)
                app.logout("작업 실패: %s" % why)
        app.run(work, done)

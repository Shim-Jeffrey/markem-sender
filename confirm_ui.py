"""2차(관리자) 확인 입력 화면. 설계: docs/설계-이중입력.md"""
import tkinter as tk
from tkinter import ttk, messagebox

import device_tools
import dual
import job_picker
import messages
import ngpcl
import store as S
import verify
import verify_ui
from app_theme import px, BG, WHITE, NAVY, MUTED, LINE, ERR, OK, FONT, Card


def no_paste(widget):
    """2차 입력칸은 붙여넣기를 막는다(1차와 같은 오타가 복사되어 들어가는 것을 방지)."""
    for seq in ("<<Paste>>", "<Control-v>", "<Control-V>", "<Shift-Insert>", "<Button-2>"):
        widget.bind(seq, lambda e: "break")


class ManagerEntryFrame(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self.is_setting = app.dual.pending_kind() == "setting"
        self._msgs = []          # 보내는 메세지 원문(표는 표시용: 숫자처럼 보이는 글자가 바뀌지 않게)
        self._pair = None        # 설정 변경: (설정 이름, 설정 값)
        self.pad = tk.Frame(self, bg=BG)
        self.pad.pack(fill="both", expand=True, padx=22, pady=16)
        card = Card(self.pad, "2차 확인 입력")
        card.pack(fill="both", expand=True)
        g = card.body
        w = app.dual.pending_worker()
        self.info = tk.Label(g, text="", bg=WHITE, fg=NAVY, font=(FONT, 10, "bold"), anchor="w")
        self.info.pack(fill="x")
        self.w_name = w.display_name
        tk.Label(g, text="1차 작업자가 입력한 내용을 보지 않고, 원본 자료를 보며 직접 입력하세요. "
                         "(붙여넣기는 막혀 있습니다)", bg=WHITE, fg=MUTED, font=(FONT, 9),
                 anchor="w").pack(fill="x", pady=(4, 0))
        if not self.is_setting:
            tk.Label(g, text="※ 같은 메세지면 프린터로 전송합니다. 전송 뒤 결과 창에서 [인쇄 시작]·[정지]를 "
                             "선택할 수 있습니다.", bg=WHITE, fg=MUTED, font=(FONT, 9),
                     anchor="w").pack(fill="x", pady=(2, 12))
            tk.Label(g, text="① 작업 (프린터에서 불러와 1차와 같은 작업을 선택)", bg=WHITE, fg=NAVY,
                     font=(FONT, 9, "bold")).pack(anchor="w")
            self.picker = job_picker.JobPicker(g, app)
            self.picker.pack(fill="x", pady=(2, 10))
            tk.Label(g, text="② 보내는 메세지 (1차와 같은 순서로 메세지 1, 2, 3 …)", bg=WHITE, fg=NAVY,
                     font=(FONT, 9, "bold")).pack(anchor="w")
        else:
            tk.Label(g, text="", bg=WHITE).pack(pady=(0, 6))

        if self.is_setting:
            self.tree = ttk.Treeview(g, columns=("name", "value"), show="headings", height=2)
            self.tree.heading("name", text="설정 이름")
            self.tree.heading("value", text="설정 값")
            self.tree.column("name", width=180)
            self.tree.column("value", width=320)
        else:
            self.tree = ttk.Treeview(g, columns=("no", "msg"), show="headings", height=6)
            self.tree.heading("no", text="번호")
            self.tree.heading("msg", text="보내는 메세지")
            self.tree.column("no", width=90, anchor="center")
            self.tree.column("msg", width=420)
        self.tree.pack(fill="both", expand=True)
        add = tk.Frame(g, bg=WHITE)
        add.pack(fill="x", pady=(10, 0))
        self.f_name = ttk.Entry(add) if self.is_setting else None
        self.f_val = ttk.Entry(add)
        if self.is_setting:
            add.columnconfigure(0, weight=1)
            add.columnconfigure(1, weight=2)
            tk.Label(add, text="① 설정 이름", bg=WHITE, fg=NAVY,
                     font=(FONT, 9, "bold")).grid(row=0, column=0, sticky="w")
            tk.Label(add, text="② 설정 값", bg=WHITE, fg=NAVY,
                     font=(FONT, 9, "bold")).grid(row=0, column=1, sticky="w")
            self.f_name.grid(row=1, column=0, sticky="we", padx=(0, 8))
            self.f_val.grid(row=1, column=1, sticky="we", padx=(0, 8))
            c_btn = 2
        else:
            add.columnconfigure(0, weight=1)
            self.f_val.grid(row=0, column=0, sticky="we", padx=(0, 8))
            c_btn = 1
        for e in (self.f_name, self.f_val):
            if e is not None:
                no_paste(e)
        self.f_val.bind("<Return>", lambda e: self.add_field())
        brow = 1 if self.is_setting else 0
        ttk.Button(add, text="추가", style="Ghost.TButton", command=self.add_field).grid(
            row=brow, column=c_btn, padx=(0, 6))
        ttk.Button(add, text="삭제", style="Ghost.TButton", command=self.del_field).grid(
            row=brow, column=c_btn + 1)

        self.msg = tk.Label(g, text="", bg=WHITE, fg=ERR, font=(FONT, 10, "bold"), anchor="w",
                            wraplength=px(700), justify="left")
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
        (self.f_name or self.f_val).focus_set()

    # ---- 표시 ----
    def _tick(self):
        if not self.winfo_exists():
            return
        left = self.app.dual.seconds_left()
        kind = "데이터 전송" if self.app.dual.pending_kind() != "setting" else \
            dual.KIND_LABEL["setting"]
        self.info.configure(text="1차 작업자 %s  ·  %s  ·  남은 시간 %02d:%02d  ·  불일치 %d/%d"
                                 % (self.w_name, kind, left // 60, left % 60,
                                    self.app.dual.tries(), dual.MAX_TRIES))
        self.after(1000, self._tick)

    def _show_rows(self):
        self.tree.delete(*self.tree.get_children())
        if self.is_setting:
            if self._pair:
                self.tree.insert("", "end", values=self._pair)
        else:
            for i, m in enumerate(self._msgs, 1):
                self.tree.insert("", "end", values=(i, m))

    def add_field(self):
        v = self.f_val.get()
        if self.is_setting:
            n = self.f_name.get().strip()
            if not n:
                return
            if self._pair:
                self.msg.configure(text="설정 변경은 이름과 값 한 쌍만 입력합니다. 먼저 삭제하세요.")
                return
            if v == "" and not messagebox.askyesno(
                    "설정 값이 비어 있습니다", "'%s'의 설정 값이 비어 있습니다. 이대로 추가할까요?" % n):
                return
            self._pair = (n, v)
            self.f_name.delete(0, "end")
        else:
            if v == "" and not messagebox.askyesno(
                    "보내는 메세지가 비어 있습니다",
                    "보내는 메세지가 비어 있습니다. 이대로 추가할까요?"):
                return
            self._msgs.append(v)
        self._show_rows()
        self.f_val.delete(0, "end")
        (self.f_name or self.f_val).focus_set()

    def del_field(self):
        if self.is_setting:
            self._pair = None
        else:
            for idx in sorted((self.tree.index(i) for i in self.tree.selection()), reverse=True):
                del self._msgs[idx]      # 지우면 번호는 다시 1부터 이어진다
        self._show_rows()

    def _clear_inputs(self):
        self._msgs, self._pair = [], None
        if not self.is_setting:
            self.picker.clear()
        for e in (self.f_name, self.f_val):
            if e is not None:
                e.delete(0, "end")
        self._show_rows()

    # ---- 동작 ----
    def cancel(self):
        if self.app.user and self.app.user.role == "master":
            self.app.enter_main(self.app.user)     # 마스터는 원래 화면으로
        else:
            self.app.logout("확인을 취소했습니다. 1차 입력은 남은 시간 동안 유지됩니다")

    def _build_request(self, kind):
        """2차 입력으로 요청을 만든다(작업은 목록에서 선택, 필드이름은 text1, text2 … 약속)."""
        if kind == "setting":
            if not self._pair:
                raise dual.DualError("설정 이름과 값을 한 쌍 입력하세요")
            return dual.Request("setting", "", "0", [self._pair], True)
        job = self.picker.get()
        if not job:
            raise dual.DualError("작업을 선택하세요. [프린터에서 불러오기]를 누른 뒤 1차와 같은 작업을 고릅니다")
        if not self._msgs:
            raise dual.DualError("보내는 메세지를 한 개 이상 추가하세요")
        return dual.Request(kind, job, "0", messages.build_fields(self._msgs), True)

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
            verify_ui.show_failure(self, "전송되지 않음", "1차 작업자 계정을 더 이상 쓸 수 없어 요청이 폐기되었습니다")
            app.logout("1차 요청이 폐기되었습니다. 1차 작업자부터 다시 입력하세요")
            return
        try:
            req = self._build_request(d.pending_kind())
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
            (self.f_name or self.f_val).focus_set()
        elif res.status == "reset":
            app.store.log_work_safe("work_fail", w, m, job=job, kind=kind, data=data, tries=res.tries,
                               fields=res.fields,
                               reason="2차 입력 %d회 불일치 — 1차 입력 폐기, 1차부터 다시" % res.tries)
            verify_ui.show_failure(self, "전송되지 않음",
                                   "입력이 %d회 일치하지 않아 1차 입력이 폐기되었습니다" % res.tries)
            app.logout("2차 입력 %d회 불일치로 1차 입력이 폐기되었습니다. 1차 작업자부터 다시 입력하세요"
                       % res.tries)
        elif res.status == "expired":
            app.store.log_work_safe("work_fail", w, m, job=job, kind=kind, reason="1차 입력 만료(10분)")
            verify_ui.show_failure(self, "전송되지 않음", "1차 입력이 만료되었습니다(10분 안에 확인 없음)")
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

        def xchg_rb(message, timeout):
            try:
                return xchg(message, timeout)
            except ngpcl.NgpclError:
                app.client.close()   # 늦게 온 응답이 다음 명령과 짝지어지지 않도록 링크를 끊는다
                raise

        def work():
            r = dual.run(app.client, approval, xchg)
            checks = None
            if r[0] and request.fields:     # 전송 성공 → 장비가 받은 값을 되읽어 신호등으로 확인
                checks = verify.read_back(xchg_rb, request.job, request.fields, app.client.status_on)
                try:    # 보내지 않은 text칸에 이전 값이 남아 있으면 노랑으로 알린다
                    rows = device_tools.query_fields(xchg_rb, request.job, "", app.client.status_on)
                    checks = checks + verify.extra_field_checks(rows, len(request.fields))
                except Exception:
                    pass
            return r, checks

        def on_action(act, set_status, lock):
            """결과 창의 [인쇄 시작]·[정지]: 2차 작업자가 직접 선택한다."""
            label = "인쇄 시작" if act == "start" else "정지"
            fn = device_tools.start_producing if act == "start" else device_tools.stop_printing
            if app.busy:
                set_status("장비와 통신 중입니다. 잠시 뒤 다시 누르세요.", None)
                lock(False)
                return

            def act_work():
                if act == "start":    # 창을 띄워 둔 사이 프린터 값이 바뀌었을 수 있어 시작 직전에 다시 확인
                    again = verify.read_back(xchg_rb, request.job, request.fields,
                                             app.client.status_on)
                    if verify.overall(again) != verify.GREEN:
                        return False, "프린터의 값이 보낸 값과 달라 시작하지 않았습니다. 다시 확인하세요", verify.RED
                return fn(xchg_rb, app.client.status_on)

            def act_done(res):
                ok, note, lt = res
                text = "%s: %s" % (label, note)
                try:
                    app.store.log_event("print_control", m.username,
                                        {"동작": label, "결과": "성공" if ok else "실패",
                                         "비고": note, "작업": job,
                                         "1차": w.username})
                except Exception as e:
                    app.log("ERR", "인쇄 제어 기록 실패: %s" % e)
                    text += "  (※ 기록 저장 실패: 담당자에게 알려 주세요)"
                set_status(text, lt)
                lock(False)
            app.run(act_work, act_done)

        def done(res):
            (ok, why), checks = res
            who = "1차 %s · 2차 %s" % (w.display_name, m.display_name)
            if ok:
                job_picker.remember_job(job)
                app.store.log_work_safe("work_ok", w, m, job=job, kind=kind, data=data,
                                   tries=mismatches or "", verify=verify.summary(checks))
                verify_ui.show_print_control(self, "전송 완료 · 인쇄 제어", checks or [],
                                             "작업 성공 · " + who, on_action)
                app.logout("작업 성공: %s (%s) · %s" % (kind, who, verify.summary(checks)))
            else:
                app.store.log_work_safe("work_fail", w, m, job=job, kind=kind, data=data,
                                   tries=mismatches or "", reason=why)
                verify_ui.show_failure(self, "전송되지 않음", why, "작업 실패 · " + who)
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
                verify_ui.show(self, "설정 변경 완료 · 수신 확인", chk, "작업 성공 · " + who,
                               name_label="설정 이름")
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
                verify_ui.show_failure(self, "전송되지 않음", why, "작업 실패 · " + who)
                app.logout("작업 실패: %s" % why)
        app.run(work, done)

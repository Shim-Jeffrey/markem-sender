"""프린터 작업 선택기: 이름을 직접 입력하지 않고 프린터에서 불러와 목록에서 고른다.

NGPCL 에는 '작업 목록 조회' 명령이 없어(매뉴얼 v25) 프린터의 현재 작업(`~JR`)을 불러온다.
이전에 불러오거나 전송한 작업 이름은 이 PC에 기억해 목록에 함께 보여 준다.
"""
import json
import os
import tkinter as tk
from tkinter import ttk, messagebox

import ngpcl
import store
from app_theme import WHITE, MUTED, FONT

MAX_RECENT = 20


def _path():
    return os.path.join(store.default_data_dir(), "recent_jobs.json")


def recent_jobs():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return [str(x) for x in data if isinstance(x, str)][:MAX_RECENT]
    except (OSError, ValueError):
        return []


def remember_job(name):
    """최근 작업 이름을 맨 앞에 기억한다(중복 제거, 최대 20개)."""
    name = (name or "").strip()
    if not name:
        return
    names = [name] + [n for n in recent_jobs() if n != name]
    try:
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(names[:MAX_RECENT], f, ensure_ascii=False)
    except OSError:
        pass        # 기억하지 못해도 전송에는 영향 없음


def read_current_job(xchg, status_on=True, timeout=10.0):
    """`~JR` 로 프린터의 현재 작업 이름을 읽는다(없으면 빈 문자열)."""
    reply = xchg(ngpcl.simple("~JR", ""), timeout)
    ident, status, f = ngpcl.parse_message(reply, status_on)
    if ident == "~NK":
        raise ngpcl.NgpclError("프린터가 작업 조회를 거부했습니다(NAK)")
    if ident != "~JN" or status == "1":
        return ""
    return f[0] if f else ""


class JobPicker(tk.Frame):
    """읽기 전용 목록 + [프린터에서 불러오기]. get() 으로 선택한 작업 이름을 얻는다."""

    def __init__(self, parent, app, bg=WHITE):
        super().__init__(parent, bg=bg)
        self.app = app
        self.current = None          # 마지막으로 프린터에서 읽은 현재 작업
        self.var = tk.StringVar()
        self.columnconfigure(0, weight=1)
        self.cb = ttk.Combobox(self, textvariable=self.var, state="readonly",
                               values=recent_jobs())
        self.cb.grid(row=0, column=0, sticky="we")
        ttk.Button(self, text="프린터에서 불러오기", style="Ghost.TButton",
                   command=self.load).grid(row=0, column=1, padx=(8, 0))
        tk.Label(self, text="프린터의 현재 작업을 불러와 선택합니다 (이름을 직접 입력하지 않음)",
                 bg=bg, fg=MUTED, font=(FONT, 8)).grid(row=1, column=0, columnspan=2,
                                                      sticky="w", pady=(2, 0))

    def get(self):
        return self.var.get().strip()

    def clear(self):
        self.var.set("")

    def load(self):
        app = self.app
        if not app.client.connected:
            messagebox.showinfo("연결", "먼저 장비에 연결하세요")
            return

        def work():
            def x(m, t):
                sent, got = app.client.request(m, t)
                app.log("TX", sent)
                app.log("RX", got)
                return got
            return read_current_job(x, app.client.status_on)

        def done(name):
            self.current = name
            if not name:
                messagebox.showinfo("작업 없음", "프린터에 선택된 작업이 없습니다. "
                                    "프린터에서 작업을 먼저 선택하세요.")
                return
            remember_job(name)
            self.cb.configure(values=recent_jobs())
            self.var.set(name)
            app.log("INFO", "프린터 작업 불러옴: %s" % name)
        app.run(work, done)

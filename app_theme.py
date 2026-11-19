"""TASCO 화면 공통: 색·글꼴·카드."""
import tkinter as tk

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


class Card(tk.Frame):
    """흰 카드: 회색 테두리 + 남색 제목."""

    def __init__(self, parent, title, desc=""):
        """desc: 이 메뉴가 무엇을 하는 곳인지 한두 줄로 쉽게 설명(운영자 안내)."""
        super().__init__(parent, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        tk.Label(self, text=title, bg=WHITE, fg=NAVY, font=(FONT, 11, "bold"),
                 anchor="w").pack(fill="x", padx=16, pady=(12, 0 if desc else 6))
        if desc:
            tk.Label(self, text=desc, bg=WHITE, fg=MUTED, font=(FONT, 9), anchor="w",
                     justify="left", wraplength=px(620)).pack(fill="x", padx=16, pady=(2, 8))
        self.body = tk.Frame(self, bg=WHITE)
        self.body.pack(fill="both", expand=True, padx=16, pady=(0, 14))


SCALE = 1.0      # 화면 크기에 맞춘 확대 배율(App 이 시작할 때 정한다)


def px(n):
    """픽셀로 정한 크기(줄바꿈 폭·행 높이 등)를 화면 배율에 맞춘다."""
    return int(round(n * SCALE))

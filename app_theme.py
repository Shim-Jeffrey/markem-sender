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

    def __init__(self, parent, title):
        super().__init__(parent, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        tk.Label(self, text=title, bg=WHITE, fg=NAVY, font=(FONT, 11, "bold"),
                 anchor="w").pack(fill="x", padx=16, pady=(12, 6))
        self.body = tk.Frame(self, bg=WHITE)
        self.body.pack(fill="both", expand=True, padx=16, pady=(0, 14))

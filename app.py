# -*- coding: utf-8 -*-
"""
app.py (v17 - 수익형 키워드 발굴 + 오늘의 작성 큐 통합판)

구조:
  [고정 영역] 헤더 + API 설정(접기/펼치기) + 카드형 요약 지표 + 실행버튼/진행바
  [스크롤 영역]
    1) 키워드 분석 테이블(좌) + 상세 분석 패널(우)
    2) 오늘의 TOP5 작성 큐 (카드형)
    3) 선택 키워드 글 초안 패널 (제목/개요/FAQ/태그 + 복사/저장)
  [하단] 상태바
"""

import os
import sys
import json
import csv
import random
import threading
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import collector
import scorer
from naver_search_api import NaverOpenAPI, NaverAdAPI, verify_keyword, write_debug_log

APP_TITLE = "수익형 키워드 발굴기 v17"
FONT_NAME = "맑은 고딕"

# ----------------------------------------------------------------------
# 색상 / 등급 표시
# ----------------------------------------------------------------------
COLOR_BG = "#eef1f6"
COLOR_CARD_BG = "#ffffff"
COLOR_HEADER_BG = "#1f2b46"
COLOR_HEADER_FG = "#ffffff"
COLOR_ACCENT = "#2f6fed"

BUCKET_STYLE = {
    "TOP5":    {"bg": "#e3f6e8", "fg": "#1e7d34", "label": "TOP5 추천"},
    "TOP10":   {"bg": "#fff8e1", "fg": "#8a6d00", "label": "TOP10 후보"},
    "상시추천": {"bg": "#e8f0ff", "fg": "#2450b5", "label": "상시추천"},
    "보류":    {"bg": "#f0f0f0", "fg": "#666666", "label": "보류"},
    "위험":    {"bg": "#fde8e8", "fg": "#c62828", "label": "위험(비추천)"},
}
BUCKET_ORDER = {"TOP5": 0, "TOP10": 1, "상시추천": 2, "보류": 3, "위험": 4}

STATUS_OPTIONS = ["미작성", "작성중", "작성완료", "발행완료"]
STATUS_COLOR = {
    "미작성": "#999999", "작성중": "#c77700",
    "작성완료": "#2450b5", "발행완료": "#1e7d34",
}


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "blog_config.json")
STATUS_PATH = os.path.join(app_dir(), "keyword_status.json")


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------------
# 표시용 포맷 함수 (None -> 사용자 친화적 문구)
# ----------------------------------------------------------------------
def fmt_num_or_fail(v):
    if v is None:
        return "검증 실패"
    return f"{v:,}"


def fmt_doc_cell(r):
    doc = r.get("doc_count")
    if doc is None:
        return "검증 실패"
    return f"{doc:,}건"


def fmt_vol_cell(r):
    vol = r.get("search_volume")
    if vol is None:
        return "검증 실패"
    return f"{vol:,}회"


def fmt_trend_cell(r):
    if r.get("trend_status") == "미검증":
        return "검증 실패"
    spike = r.get("spike_ratio", 1.0)
    status = r.get("issue_status_final", r.get("trend_status", "-"))
    return f"{status} x{spike:.2f}"


def fmt_efficiency_cell(r):
    label = r.get("efficiency_label", "효율 미검증")
    if "미검증" in label:
        return "검증 실패"
    ratio = r.get("efficiency_ratio")
    if ratio is not None:
        return f"{label} ({ratio:.2f})"
    return label


def fmt_revenue(r):
    rev = r.get("estimated_revenue_won")
    if rev is None:
        return "산출 불가(검증 실패)"
    return f"약 {rev:,}원 (추정, 실제와 다를 수 있음)"


def compute_difficulty(doc_count, comp_label):
    if comp_label == "높음":
        return "어려움"
    if comp_label == "중간":
        return "보통"
    if comp_label == "낮음":
        return "쉬움"
    if doc_count is not None:
        if doc_count > 100_000:
            return "어려움"
        if doc_count > 20_000:
            return "보통"
        return "쉬움"
    return "미확인"


def assign_timing_text(r):
    if r.get("risk"):
        return "작성 비권장"
    if r.get("issue_status_final") == "상시성":
        return "여유있게 작성 가능 (상시성 키워드)"
    rank = r.get("rank")
    if rank is not None and rank <= 1:
        return "오늘 바로 작성 권장"
    if rank is not None and rank <= 5:
        return "오늘 중 작성 권장"
    if rank is not None and rank <= 15:
        return "이번 주 내 작성 권장"
    return "여유있게 작성 가능"


# ----------------------------------------------------------------------
# 글쓰기 초안 생성 (v16 기능 복원)
# ----------------------------------------------------------------------
TITLE_TEMPLATES = {
    "SEO형": [
        "{kw} 조건 및 신청방법 총정리",
        "{kw} 대상자 확인 및 준비서류 안내",
        "{kw} 얼마나 받을 수 있는지 정리",
    ],
    "질문형": [
        "{kw}, 나도 대상일까?",
        "{kw} 신청 안 하면 손해일까?",
        "{kw} 어디서 확인해야 할까?",
    ],
    "비교형": [
        "{kw} 이전과 달라진 점 비교 정리",
        "{kw} 기존 제도와 무엇이 다를까",
        "{kw} 조건 변경 전후 비교",
    ],
    "실수방지형": [
        "{kw} 신청 전 꼭 확인해야 할 3가지",
        "{kw} 놓치기 쉬운 서류 실수 정리",
        "{kw} 신청 기한 놓치지 않는 방법",
    ],
    "후킹형": [
        "{kw}, 모르면 손해 보는 이유",
        "다들 몰랐던 {kw} 혜택 총정리",
        "{kw} 지금 안 하면 늦습니다",
    ],
    "감성형": [
        "{kw} 덕분에 숨통 트인 이야기",
        "저도 이번에 처음 알게 된 {kw}",
        "{kw}, 미리 알았더라면 좋았을 것",
    ],
}
SEARCH_TYPES = ["SEO형", "질문형", "비교형"]
FEED_TYPES = ["실수방지형", "후킹형", "감성형"]


def generate_titles_by_type(keyword: str):
    picked = {}
    for t, pool in TITLE_TEMPLATES.items():
        picked[t] = random.choice(pool).format(kw=keyword)
    return picked


def make_outline(keyword: str):
    return (
        f"1) 도입 - '{keyword}' 관련 공감/문제 제기 (왜 지금 주목해야 하는지)\n"
        f"2) 핵심정보 - 대상, 조건, 예상 금액/혜택 정리\n"
        f"3) 신청방법 - 절차, 필요서류, 신청 기한\n"
        f"4) 주의사항 - 마감일, 자주 하는 실수, 유의점\n"
        f"5) 마무리 - 핵심 요약 + 행동 유도(신청 링크/문의처 안내)"
    )


def generate_faq(keyword: str):
    return [
        f"{keyword} 대상은 누구인가요?",
        f"{keyword} 신청 시 필요한 서류는 무엇인가요?",
        f"{keyword} 처리(지급)까지 얼마나 걸리나요?",
        f"{keyword} 신청 기한을 놓치면 어떻게 되나요?",
        f"{keyword} 관련 문의는 어디로 하면 되나요?",
    ]


def generate_tags(keyword: str, category: str):
    base = keyword.split(" ")
    tags = list(dict.fromkeys(base + [category, "총정리", "확인방법", "신청방법"]))
    return tags[:8]


def build_draft_text(keyword: str, r: dict, titles: dict):
    lines = []
    lines.append(f"■ {keyword}  ({r.get('bucket', '-')} / {r.get('stars', '-')})")
    lines.append("")
    lines.append("[검색용 제목 - SEO형 / 질문형 / 비교형]")
    for t in SEARCH_TYPES:
        lines.append(f"  ({t}) {titles[t]}")
    lines.append("")
    lines.append("[홈판용 제목 - 실수방지형 / 후킹형 / 감성형]")
    for t in FEED_TYPES:
        lines.append(f"  ({t}) {titles[t]}")
    lines.append("")
    lines.append("[글 개요]")
    lines.append(make_outline(keyword))
    lines.append("")
    lines.append("[FAQ]")
    for q in generate_faq(keyword):
        lines.append(f"  - {q}")
    lines.append("")
    lines.append("[태그 추천]")
    lines.append("  " + ", ".join(generate_tags(keyword, r.get("category", "기타"))))
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 스크롤 가능한 프레임 (Canvas + Frame)
# ----------------------------------------------------------------------
class ScrollableArea(ttk.Frame):
    def __init__(self, parent, bg=COLOR_BG):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self.vscroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self._win_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vscroll.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.vscroll.pack(side="right", fill="y")

        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_inner_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self._win_id, width=event.width)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


# ----------------------------------------------------------------------
# 요약 카드
# ----------------------------------------------------------------------
class SummaryCard(tk.Frame):
    def __init__(self, parent, title, value="-"):
        super().__init__(parent, bg=COLOR_CARD_BG, highlightbackground="#dcdfe6",
                         highlightthickness=1, bd=0)
        self.configure(padx=14, pady=10)
        self.value_var = tk.StringVar(value=str(value))
        tk.Label(self, text=title, bg=COLOR_CARD_BG, fg="#777777",
                font=(FONT_NAME, 9)).pack(anchor="w")
        tk.Label(self, textvariable=self.value_var, bg=COLOR_CARD_BG, fg="#1f2b46",
                font=(FONT_NAME, 20, "bold")).pack(anchor="w", pady=(4, 0))

    def set_value(self, value):
        self.value_var.set(str(value))


# ----------------------------------------------------------------------
# TOP5 작성 큐 카드
# ----------------------------------------------------------------------
class QueueCard(tk.Frame):
    def __init__(self, parent, rank, r, status, on_status_change, on_select):
        style = BUCKET_STYLE.get(r["bucket"], BUCKET_STYLE["보류"])
        super().__init__(parent, bg=style["bg"], highlightbackground="#c8c8c8",
                         highlightthickness=1, bd=0)
        self.configure(padx=14, pady=10)
        self.keyword = r["keyword"]
        self.r = r
        self.on_status_change = on_status_change
        self.on_select = on_select

        doc = r.get("doc_count")
        comp = r.get("comp_label")
        difficulty = compute_difficulty(doc, comp)
        timing = assign_timing_text(r)
        revenue_txt = fmt_revenue(r)

        top_row = tk.Frame(self, bg=style["bg"])
        top_row.pack(fill="x", anchor="w")
        tk.Label(top_row, text=f"{rank}위", bg=style["bg"], fg=style["fg"],
                font=(FONT_NAME, 12, "bold")).pack(side="left")
        title_lbl = tk.Label(top_row, text=f"  {r['keyword']}", bg=style["bg"], fg="#222222",
                            font=(FONT_NAME, 13, "bold"), cursor="hand2")
        title_lbl.pack(side="left")
        title_lbl.bind("<Button-1>", lambda e: self.on_select(self.keyword))
        tk.Label(top_row, text=f"  난이도: {difficulty}", bg=style["bg"], fg="#555555",
                font=(FONT_NAME, 10)).pack(side="left")

        mid = tk.Label(self, text=f"예상수익: {revenue_txt}", bg=style["bg"], fg="#333333",
                      font=(FONT_NAME, 10))
        mid.pack(anchor="w", pady=(4, 0))

        timing_lbl = tk.Label(self, text=f"▶ {timing}", bg=style["bg"], fg=style["fg"],
                              font=(FONT_NAME, 10, "bold"))
        timing_lbl.pack(anchor="w", pady=(2, 4))

        status_row = tk.Frame(self, bg=style["bg"])
        status_row.pack(fill="x", anchor="w")

        self.status_var = tk.StringVar(value=status)
        status_lbl = tk.Label(status_row, textvariable=self.status_var,
                              bg=style["bg"], fg=STATUS_COLOR.get(status, "#999999"),
                              font=(FONT_NAME, 10, "bold"))
        status_lbl.pack(side="left", padx=(0, 10))
        self._status_lbl = status_lbl

        btn_frame = tk.Frame(self, bg=style["bg"])
        btn_frame.pack(anchor="w", pady=(4, 0))
        ttk.Button(btn_frame, text="작성 시작", width=10,
                  command=lambda: self._change_status("작성중")).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="작성완료 표시", width=12,
                  command=lambda: self._change_status("작성완료")).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="발행완료 표시", width=12,
                  command=lambda: self._change_status("발행완료")).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="글초안 보기", width=10,
                  command=lambda: self.on_select(self.keyword)).pack(side="left", padx=(10, 2))

    def _change_status(self, new_status):
        self.status_var.set(new_status)
        self._status_lbl.configure(fg=STATUS_COLOR.get(new_status, "#999999"))
        self.on_status_change(self.keyword, new_status)


# ----------------------------------------------------------------------
# 메인 애플리케이션
# ----------------------------------------------------------------------
class KeywordApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1560x980")
        self.configure(bg=COLOR_BG)

        self.results = []
        self.cfg = load_json(CONFIG_PATH, {})
        self.status_map = load_json(STATUS_PATH, {})
        self.selected_keyword = None
        self.selected_write_keyword = None
        self.api_visible = True
        self._row_keyword_map = {}
        self._current_titles = None

        self._setup_style()
        self._build_header()
        self._build_api_frame()
        self._build_summary_cards()
        self._build_run_controls()

        self.scroll_area = ScrollableArea(self, bg=COLOR_BG)
        self.scroll_area.pack(fill="both", expand=True, padx=0, pady=0)

        self._build_table_and_detail(self.scroll_area.inner)
        self._build_queue_section(self.scroll_area.inner)
        self._build_draft_section(self.scroll_area.inner)

        self._build_status_bar()

    # ---------------- 스타일 ----------------
    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", rowheight=28, font=(FONT_NAME, 10),
                        background="#ffffff", fieldbackground="#ffffff")
        style.configure("Treeview.Heading", font=(FONT_NAME, 10, "bold"),
                        background="#f4f6f9", foreground="#333333")
        style.configure("TButton", font=(FONT_NAME, 9))
        style.configure("Accent.TButton", font=(FONT_NAME, 11, "bold"))
        style.configure("TLabelframe", background=COLOR_BG, font=(FONT_NAME, 10, "bold"))
        style.configure("TLabelframe.Label", background=COLOR_BG, font=(FONT_NAME, 10, "bold"))
        style.configure("TFrame", background=COLOR_BG)

    # ---------------- 헤더 ----------------
    def _build_header(self):
        header = tk.Frame(self, bg=COLOR_HEADER_BG, height=56)
        header.pack(fill="x", side="top")
        tk.Label(header, text="  수익형 키워드 발굴기 v17", bg=COLOR_HEADER_BG,
                fg=COLOR_HEADER_FG, font=(FONT_NAME, 16, "bold")).pack(side="left", pady=10)
        self.api_toggle_btn = tk.Button(header, text="API 설정 접기 ▲", command=self.toggle_api_frame,
                                        bg="#33436b", fg="#ffffff", relief="flat",
                                        font=(FONT_NAME, 9), activebackground="#455a85")
        self.api_toggle_btn.pack(side="right", padx=14, pady=10)

    # ---------------- API 설정 (접기/펼치기) ----------------
    def _build_api_frame(self):
        self.api_frame = ttk.LabelFrame(self, text="API 설정")
        self.api_frame.pack(fill="x", padx=12, pady=(10, 6))

        self.vars = {
            "naver_client_id": tk.StringVar(value=self.cfg.get("naver_client_id", "")),
            "naver_client_secret": tk.StringVar(value=self.cfg.get("naver_client_secret", "")),
            "ad_api_key": tk.StringVar(value=self.cfg.get("ad_api_key", "")),
            "ad_secret_key": tk.StringVar(value=self.cfg.get("ad_secret_key", "")),
            "ad_customer_id": tk.StringVar(value=self.cfg.get("ad_customer_id", "")),
        }
        rows = [
            ("네이버 검색 API Client ID", "naver_client_id", False),
            ("네이버 검색 API Client Secret", "naver_client_secret", True),
            ("검색광고 API License Key", "ad_api_key", False),
            ("검색광고 API Secret Key", "ad_secret_key", True),
            ("검색광고 API Customer ID", "ad_customer_id", False),
        ]
        for i, (label, key, is_secret) in enumerate(rows):
            r, c = divmod(i, 3)
            ttk.Label(self.api_frame, text=label).grid(row=r * 2, column=c, sticky="w", padx=8, pady=(6, 0))
            entry = ttk.Entry(self.api_frame, textvariable=self.vars[key], width=32,
                              show="*" if is_secret else "")
            entry.grid(row=r * 2 + 1, column=c, sticky="w", padx=8, pady=(0, 6))

        btn_frame = ttk.Frame(self.api_frame)
        btn_frame.grid(row=4, column=0, columnspan=3, pady=6)
        ttk.Button(btn_frame, text="네이버 검색 API 연결 테스트", command=self.test_open_api).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="검색광고 API 연결 테스트", command=self.test_ad_api).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="설정 저장", command=self.on_save_config).pack(side="left", padx=4)

    def toggle_api_frame(self):
        if self.api_visible:
            self.api_frame.pack_forget()
            self.api_toggle_btn.configure(text="API 설정 펼치기 ▼")
        else:
            self.api_frame.pack(fill="x", padx=12, pady=(10, 6), before=self._cards_row)
            self.api_toggle_btn.configure(text="API 설정 접기 ▲")
        self.api_visible = not self.api_visible

    def on_save_config(self):
        cfg = {k: v.get().strip() for k, v in self.vars.items()}
        save_json(CONFIG_PATH, cfg)
        self.cfg = cfg
        messagebox.showinfo("저장 완료", "API 설정이 저장되었습니다.")

    def _make_open_api(self):
        return NaverOpenAPI(self.vars["naver_client_id"].get().strip(),
                            self.vars["naver_client_secret"].get().strip())

    def _make_ad_api(self):
        return NaverAdAPI(self.vars["ad_api_key"].get().strip(),
                          self.vars["ad_secret_key"].get().strip(),
                          self.vars["ad_customer_id"].get().strip())

    def test_open_api(self):
        ok, msg = self._make_open_api().test_connection()
        (messagebox.showinfo if ok else messagebox.showerror)("검색 API 테스트", msg)

    def test_ad_api(self):
        ok, msg = self._make_ad_api().test_connection()
        (messagebox.showinfo if ok else messagebox.showerror)("검색광고 API 테스트", msg)

    # ---------------- 카드형 요약 ----------------
    def _build_summary_cards(self):
        self._cards_row = tk.Frame(self, bg=COLOR_BG)
        self._cards_row.pack(fill="x", padx=12, pady=(0, 6))

        self.card_total = SummaryCard(self._cards_row, "검증 완료 키워드")
        self.card_top5 = SummaryCard(self._cards_row, "TOP5 추천")
        self.card_top10 = SummaryCard(self._cards_row, "TOP10 후보")
        self.card_risk = SummaryCard(self._cards_row, "위험 키워드")
        self.card_updated = SummaryCard(self._cards_row, "마지막 실행")

        for card in (self.card_total, self.card_top5, self.card_top10, self.card_risk, self.card_updated):
            card.pack(side="left", fill="x", expand=True, padx=5)

    # ---------------- 실행 컨트롤 ----------------
    def _build_run_controls(self):
        run_frame = tk.Frame(self, bg=COLOR_BG)
        run_frame.pack(fill="x", padx=12, pady=(0, 8))

        self.run_btn = ttk.Button(run_frame, text="▶ 오늘의 수익형 키워드 분석 시작",
                                  style="Accent.TButton", command=self.on_run_pipeline)
        self.run_btn.pack(side="left")

        self.progress = ttk.Progressbar(run_frame, mode="indeterminate", length=220)
        self.progress.pack(side="left", padx=12)

    # ---------------- 테이블 + 상세 패널 ----------------
    def _build_table_and_detail(self, parent):
        row = tk.Frame(parent, bg=COLOR_BG)
        row.pack(fill="both", expand=False, padx=12, pady=(4, 8))

        left = ttk.LabelFrame(row, text="키워드 분석 결과")
        left.pack(side="left", fill="both", expand=True)

        columns = ("rank", "keyword", "bucket", "score", "vol", "doc", "eff", "trend", "risk")
        headers = {
            "rank": "순위", "keyword": "키워드", "bucket": "등급", "score": "점수",
            "vol": "검색량", "doc": "문서수", "eff": "효율", "trend": "DataLab", "risk": "위험여부",
        }
        self.tree = ttk.Treeview(left, columns=columns, show="headings", height=14)
        widths = {"rank": 50, "keyword": 220, "bucket": 90, "score": 70,
                 "vol": 100, "doc": 100, "eff": 150, "trend": 130, "risk": 80}
        for col in columns:
            self.tree.heading(col, text=headers[col])
            self.tree.column(col, width=widths[col], anchor="center")
        self.tree.pack(fill="both", expand=True, padx=6, pady=6, side="left")

        tree_scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        tree_scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tree_scroll.set)

        for bucket, style in BUCKET_STYLE.items():
            self.tree.tag_configure(bucket, background=style["bg"])

        self.tree.bind("<<TreeviewSelect>>", self.on_select_row)

        right = ttk.LabelFrame(row, text="상세 분석 패널", width=420)
        right.pack(side="left", fill="both", padx=(8, 0))
        right.pack_propagate(False)

        self.detail_text = tk.Text(right, width=46, height=16, wrap="word",
                                   font=(FONT_NAME, 10), relief="flat", bg="#fbfbfc")
        self.detail_text.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self.detail_text.config(state="disabled")

        ttk.Button(right, text="이 키워드로 글초안 생성",
                  command=self._use_selected_for_writing).pack(pady=(0, 8))

    # ---------------- TOP5 작성 큐 ----------------
    def _build_queue_section(self, parent):
        section = ttk.LabelFrame(parent, text="오늘의 TOP5 작성 큐")
        section.pack(fill="x", padx=12, pady=(4, 8))
        self.queue_frame = tk.Frame(section, bg=COLOR_BG)
        self.queue_frame.pack(fill="x", padx=6, pady=6)
        self._queue_placeholder = tk.Label(self.queue_frame, text="분석을 실행하면 TOP5 작성 큐가 표시됩니다.",
                                           bg=COLOR_BG, fg="#888888", font=(FONT_NAME, 10))
        self._queue_placeholder.pack(pady=20)

    # ---------------- 글 초안 패널 ----------------
    def _build_draft_section(self, parent):
        section = ttk.LabelFrame(parent, text="선택 키워드 글 초안 (제목 / 개요 / FAQ / 태그)")
        section.pack(fill="both", padx=12, pady=(4, 16))

        top_bar = tk.Frame(section, bg=COLOR_BG)
        top_bar.pack(fill="x", padx=6, pady=(6, 0))
        self.draft_title_var = tk.StringVar(value="키워드를 선택하면 초안이 표시됩니다.")
        tk.Label(top_bar, textvariable=self.draft_title_var, bg=COLOR_BG,
                fg="#1f2b46", font=(FONT_NAME, 12, "bold")).pack(side="left")

        btn_bar = tk.Frame(section, bg=COLOR_BG)
        btn_bar.pack(fill="x", padx=6, pady=(4, 6))
        ttk.Button(btn_bar, text="제목 다시 생성", command=self._regenerate_titles).pack(side="left", padx=2)
        ttk.Button(btn_bar, text="전체 복사(클립보드)", command=self._copy_draft).pack(side="left", padx=2)
        ttk.Button(btn_bar, text="TXT로 저장", command=self._save_draft_txt).pack(side="left", padx=2)
        ttk.Button(btn_bar, text="분석 결과 CSV 저장", command=self._save_results_csv).pack(side="left", padx=2)

        self.draft_text = tk.Text(section, height=16, wrap="word",
                                  font=(FONT_NAME, 10), relief="flat", bg="#fbfbfc")
        self.draft_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    # ---------------- 하단 상태바 ----------------
    def _build_status_bar(self):
        bar = tk.Frame(self, bg="#dde2ea", height=26)
        bar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="대기 중")
        tk.Label(bar, textvariable=self.status_var, bg="#dde2ea", fg="#333333",
                anchor="w", font=(FONT_NAME, 9), padx=10).pack(side="left", fill="x", expand=True)

    def _set_status(self, text):
        self.status_var.set(text)

    # ---------------- 파이프라인 실행 ----------------
    def on_run_pipeline(self):
        cid = self.vars["naver_client_id"].get().strip()
        csec = self.vars["naver_client_secret"].get().strip()
        if not cid or not csec:
            messagebox.showwarning("API 키 필요", "먼저 네이버 검색 API Client ID/Secret을 입력하고 저장하세요.")
            return
        ad_key = self.vars["ad_api_key"].get().strip()
        if not ad_key:
            self._set_status("[주의] 검색광고 API 키가 비어있어 검색량/경쟁도는 검증되지 않습니다.")

        self.run_btn.config(state="disabled")
        self.progress.start(12)
        self.tree.delete(*self.tree.get_children())
        self._set_status("Discovery 단계: 뉴스 수집 중...")

        thread = threading.Thread(target=self._pipeline_worker, daemon=True)
        thread.start()

    def _pipeline_worker(self):
        try:
            open_api = self._make_open_api()
            ad_api = self._make_ad_api()

            def progress_cb(msg):
                self.after(0, self._set_status, msg)

            filtered_40 = collector.collect_candidates(open_api, progress_cb=progress_cb)

            if not filtered_40:
                self.after(0, self._set_status, "수집된 후보가 없습니다.")
                self.after(0, self._finish_pipeline)
                return

            verified = []
            for i, cand in enumerate(filtered_40, start=1):
                self.after(0, self._set_status,
                          f"Verification 단계: '{cand['keyword']}' 검증 중 ({i}/{len(filtered_40)})")
                try:
                    v = verify_keyword(cand["keyword"], open_api, ad_api)
                except Exception as e:
                    write_debug_log(f"[verify_keyword] '{cand['keyword']}' 예외: {e}")
                    v = {"doc_count": None, "spike_ratio": 1.0, "trend_status": "미검증",
                        "search_volume": None, "comp_label": None, "comp_idx": 0}
                verified.append({**cand, **v})

            self.after(0, self._set_status, "Scoring 단계: 점수 계산 및 위험 판정 중...")
            scored = scorer.compute_scores(verified)

            self.results = scored
            self.after(0, self._render_all)
            self.after(0, self._set_status, f"완료 - 검증 {len(filtered_40)}건 분석됨")
        except Exception as e:
            write_debug_log(f"[pipeline] 전체 실패: {e}")
            self.after(0, lambda: messagebox.showerror("오류", f"분석 중 오류가 발생했습니다: {e}"))
        finally:
            self.after(0, self._finish_pipeline)

    def _finish_pipeline(self):
        self.progress.stop()
        self.run_btn.config(state="normal")

    def _render_all(self):
        self._render_table()
        self._render_queue()
        self._update_summary_cards()

    # ---------------- 요약 카드 갱신 ----------------
    def _update_summary_cards(self):
        total = len(self.results)
        top5 = sum(1 for r in self.results if r["bucket"] == "TOP5")
        top10 = sum(1 for r in self.results if r["bucket"] == "TOP10")
        risk = sum(1 for r in self.results if r["bucket"] == "위험")

        self.card_total.set_value(total)
        self.card_top5.set_value(top5)
        self.card_top10.set_value(top10)
        self.card_risk.set_value(risk)
        self.card_updated.set_value(datetime.now().strftime("%H:%M:%S"))

    # ---------------- 테이블 렌더링 ----------------
    def _render_table(self):
        self.tree.delete(*self.tree.get_children())
        self._row_keyword_map = {}
        ordered = sorted(self.results, key=lambda r: (
            BUCKET_ORDER.get(r["bucket"], 9), -r["final_score"]
        ))
        for idx, r in enumerate(ordered):
            rank_txt = r["rank"] if r["rank"] is not None else "-"
            risk_txt = "위험" if r.get("risk") else "-"
            iid = f"row{idx}"
            self.tree.insert("", "end", iid=iid, values=(
                rank_txt, r["keyword"], r["bucket"], r["final_score"],
                fmt_vol_cell(r), fmt_doc_cell(r), fmt_efficiency_cell(r),
                fmt_trend_cell(r), risk_txt,
            ), tags=(r["bucket"],))
            self._row_keyword_map[iid] = r["keyword"]

    def on_select_row(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        keyword = self._row_keyword_map.get(sel[0])
        r = next((x for x in self.results if x["keyword"] == keyword), None)
        if r is None:
            return
        self.selected_keyword = keyword
        self._show_detail(r)

    def _show_detail(self, r):
        lines = []
        rank_txt = f"{r['rank']}위" if r["rank"] is not None else "순위 제외(위험)"
        style = BUCKET_STYLE.get(r["bucket"], BUCKET_STYLE["보류"])
        lines.append(f"[{rank_txt}] {r['keyword']}   ({style['label']})")
        lines.append(f"{r.get('stars', '-')}  최종점수 {r['final_score']}")
        lines.append(f"카테고리: {r.get('category', '-')} / 예상 수익성: {r.get('profit_label', '-')}")
        lines.append(f"예상 월수익: {fmt_revenue(r)}")
        lines.append("")
        lines.append("[추천/비추천 이유]")
        for tag in r.get("reason_tags", []):
            prefix = "  ⚠ " if tag.startswith("[위험]") else "  ✓ "
            lines.append(f"{prefix}{tag}")
        lines.append("")
        lines.append("[검증 데이터]")
        lines.append(f"  - 검색량(월): {fmt_num_or_fail(r.get('search_volume'))}")
        lines.append(f"  - 문서수: {fmt_num_or_fail(r.get('doc_count'))}")
        lines.append(f"  - 효율(검색량÷문서수): {fmt_efficiency_cell(r)}")
        lines.append(f"  - 뉴스 언급: {r.get('mentions', 0)}건")
        lines.append(f"  - DataLab 상태: {fmt_trend_cell(r)}")
        lines.append(f"  - 경쟁도: {r.get('comp_label') or '검증 실패'}")
        if r.get("risk"):
            lines.append("")
            lines.append("[위험 사유]")
            for reason in r.get("risk_reasons", []):
                lines.append(f"  ⚠ {reason}")
        lines.append("")
        lines.append("[참고 기사]")
        for a in r.get("articles", [])[:3]:
            lines.append(f"  - {a['title']}")
            lines.append(f"    {a['link']}")

        self.detail_text.config(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", "\n".join(lines))
        self.detail_text.config(state="disabled")

    def _use_selected_for_writing(self):
        if not self.selected_keyword:
            messagebox.showinfo("안내", "먼저 테이블에서 키워드를 선택하세요.")
            return
        self._load_draft(self.selected_keyword)

    # ---------------- TOP5 작성 큐 렌더링 ----------------
    def _render_queue(self):
        for widget in self.queue_frame.winfo_children():
            widget.destroy()

        non_risk = [r for r in self.results if not r.get("risk") and r.get("rank") is not None]
        non_risk.sort(key=lambda r: r["rank"])
        top5 = non_risk[:5]

        if not top5:
            tk.Label(self.queue_frame, text="추천 가능한 키워드가 없습니다. (전부 위험/보류 처리됨)",
                    bg=COLOR_BG, fg="#888888", font=(FONT_NAME, 10)).pack(pady=20)
            return

        for i, r in enumerate(top5, start=1):
            status = self.status_map.get(r["keyword"], {}).get("status", "미작성")
            card = QueueCard(self.queue_frame, i, r, status,
                            on_status_change=self._on_queue_status_change,
                            on_select=self._load_draft)
            card.pack(fill="x", pady=6, padx=2)

    def _on_queue_status_change(self, keyword, new_status):
        self.status_map[keyword] = {
            "status": new_status,
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_json(STATUS_PATH, self.status_map)

    # ---------------- 글 초안 패널 ----------------
    def _load_draft(self, keyword):
        r = next((x for x in self.results if x["keyword"] == keyword), None)
        if r is None:
            return
        self.selected_write_keyword = keyword
        self._current_titles = generate_titles_by_type(keyword)
        self.draft_title_var.set(f"■ {keyword}  -  글쓰기 초안")
        self._render_draft_text(keyword, r, self._current_titles)

    def _render_draft_text(self, keyword, r, titles):
        text = build_draft_text(keyword, r, titles)
        self.draft_text.delete("1.0", "end")
        self.draft_text.insert("1.0", text)

    def _regenerate_titles(self):
        if not self.selected_write_keyword:
            messagebox.showinfo("안내", "먼저 키워드를 선택하세요.")
            return
        r = next((x for x in self.results if x["keyword"] == self.selected_write_keyword), None)
        if r is None:
            return
        self._current_titles = generate_titles_by_type(self.selected_write_keyword)
        self._render_draft_text(self.selected_write_keyword, r, self._current_titles)

    def _copy_draft(self):
        content = self.draft_text.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo("안내", "복사할 초안이 없습니다.")
            return
        self.clipboard_clear()
        self.clipboard_append(content)
        messagebox.showinfo("복사 완료", "글 초안이 클립보드에 복사되었습니다.")

    def _save_draft_txt(self):
        content = self.draft_text.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo("안내", "저장할 초안이 없습니다.")
            return
        keyword = self.selected_write_keyword or "draft"
        default_name = f"{keyword}_초안.txt"
        path = filedialog.asksaveasfilename(
            defaultextension=".txt", initialfile=default_name,
            filetypes=[("Text files", "*.txt")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        messagebox.showinfo("저장 완료", f"초안이 저장되었습니다:\n{path}")

    def _save_results_csv(self):
        if not self.results:
            messagebox.showinfo("안내", "저장할 분석 결과가 없습니다.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="keyword_analysis.csv",
            filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["순위", "키워드", "등급", "점수", "검색량", "문서수",
                            "효율", "DataLab", "위험여부", "예상수익", "카테고리"])
            for r in sorted(self.results, key=lambda r: (BUCKET_ORDER.get(r["bucket"], 9), -r["final_score"])):
                writer.writerow([
                    r["rank"] if r["rank"] is not None else "-",
                    r["keyword"], r["bucket"], r["final_score"],
                    r.get("search_volume") if r.get("search_volume") is not None else "검증실패",
                    r.get("doc_count") if r.get("doc_count") is not None else "검증실패",
                    r.get("efficiency_label", "-"),
                    fmt_trend_cell(r),
                    "위험" if r.get("risk") else "-",
                    r.get("estimated_revenue_won") if r.get("estimated_revenue_won") is not None else "산출불가",
                    r.get("category", "-"),
                ])
        messagebox.showinfo("저장 완료", f"분석 결과 CSV가 저장되었습니다:\n{path}")


if __name__ == "__main__":
    app = KeywordApp()
    app.mainloop()

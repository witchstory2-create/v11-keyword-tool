# -*- coding: utf-8 -*-
"""
app.py (v18.2)
네이버 블로그 수익형 키워드 발굴 시스템 - 메인 UI

파이프라인:
  collector.collect_candidates()  -> 후보 수집
  profit_filter.filter_candidates() -> 수익형 필터링(카테고리 가중치/검색의도)
  scorer.score_candidates()       -> 4단계 교차검증 + 점수화 + 등급 분류

카드형 UI:
  TOP5(금색) / TOP10(은색) / 보류(주황) / 위험(빨강) 4등급을
  색상/배지/테두리로 구분되는 카드로 렌더링.

표준 라이브러리(tkinter)만 사용 -> PyInstaller / GitHub Actions 빌드 호환.
외부 유료 AI API, 추가 pip 패키지 사용하지 않음.
"""

import os
import sys
import json
import csv
import threading
import queue
import traceback
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext

# ---- 파이프라인 모듈 ----------------------------------------------------
import collector
import profit_filter
import scorer
from naver_search_api import NaverSearchAPI, NaverDataLabAPI, NaverAdsAPI

APP_TITLE = "네이버 블로그 수익형 키워드 발굴기"
APP_VERSION = "v18.2"

BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LOG_FILE = os.path.join(BASE_DIR, "trend_debug_log.txt")

DEFAULT_CATEGORIES = ["보험", "대출", "환급", "지원금", "연금", "세금",
                       "청약", "부동산", "카드", "자동차보험", "건강보험"]

# =========================================================================
# 등급별 카드 스타일 정의
# =========================================================================
GRADE_STYLE = {
    "TOP5":  {"bg": "#FFF6DC", "border": "#E6B800", "badge_bg": "#E6B800",
              "badge_fg": "#3A2E00", "label": "\U0001F947 TOP5"},
    "TOP10": {"bg": "#F1F3F6", "border": "#8C9BAB", "badge_bg": "#8C9BAB",
              "badge_fg": "#FFFFFF", "label": "\U0001F948 TOP10"},
    "보류":   {"bg": "#FFF1E0", "border": "#F39C12", "badge_bg": "#F39C12",
              "badge_fg": "#FFFFFF", "label": "\u23F8 보류"},
    "위험":   {"bg": "#FDEAEA", "border": "#E74C3C", "badge_bg": "#E74C3C",
              "badge_fg": "#FFFFFF", "label": "\u26A0 위험"},
}
GRADE_ORDER = ["TOP5", "TOP10", "보류", "위험"]


# =========================================================================
# 설정 파일 로드 / 저장
# =========================================================================
DEFAULT_CONFIG = {
    "naver_client_id": "",
    "naver_client_secret": "",
    "ads_customer_id": "",
    "ads_license_key": "",
    "ads_secret_key": "",
    "categories": DEFAULT_CATEGORIES,
    "max_workers": 4,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            cfg.update(saved)
        except Exception:
            pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# =========================================================================
# 스크롤 가능한 프레임 (카드 리스트용)
# =========================================================================
class ScrollableFrame(ttk.Frame):
    def __init__(self, parent, bg="#FFFFFF", *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0, bg=bg)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar.pack(side="right", fill="y")

        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_mousewheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_inner_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_resize(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def clear(self):
        for w in self.inner.winfo_children():
            w.destroy()


# =========================================================================
# 접기/펼치기 설정 섹션
# =========================================================================
class CollapsibleSection(ttk.Frame):
    def __init__(self, parent, title, start_expanded=False, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.title = title
        self.expanded = start_expanded

        header = ttk.Frame(self)
        header.pack(fill="x")
        self.toggle_btn = ttk.Button(header, text=self._label(), command=self.toggle)
        self.toggle_btn.pack(fill="x")

        self.body = ttk.Frame(self, padding=8)
        if self.expanded:
            self.body.pack(fill="x")

    def _label(self):
        arrow = "\u25BC" if self.expanded else "\u25B6"
        return f"{arrow} {self.title}"

    def toggle(self):
        self.expanded = not self.expanded
        if self.expanded:
            self.body.pack(fill="x")
        else:
            self.body.pack_forget()
        self.toggle_btn.config(text=self._label())


# =========================================================================
# 키워드 카드
# =========================================================================
class KeywordCard(tk.Frame):
    def __init__(self, parent, data, on_click, *args, **kwargs):
        style = GRADE_STYLE.get(data.get("grade", "보류"), GRADE_STYLE["보류"])
        super().__init__(parent, bg=style["border"], *args, **kwargs)
        self.data = data
        self.on_click = on_click

        self.pack_configure(fill="x", padx=8, pady=5)

        card = tk.Frame(self, bg=style["bg"])
        card.pack(fill="x", padx=1, pady=1)

        # ---- 상단: 배지 / 순위 / 최종점수 ----
        top_row = tk.Frame(card, bg=style["bg"])
        top_row.pack(fill="x", padx=12, pady=(9, 3))

        badge = tk.Label(top_row, text=style["label"], bg=style["badge_bg"], fg=style["badge_fg"],
                          font=("맑은 고딕", 9, "bold"), padx=8, pady=2)
        badge.pack(side="left")

        rank = data.get("rank")
        if rank:
            tk.Label(top_row, text=f"#{rank}", bg=style["bg"], fg="#555555",
                     font=("맑은 고딕", 9)).pack(side="left", padx=(8, 0))

        tk.Label(top_row, text=f"FinalScore {data.get('final_score', 0):.1f}",
                 bg=style["bg"], fg="#333333", font=("맑은 고딕", 9, "bold")).pack(side="right")

        # ---- 키워드명 / 카테고리 ----
        kw_row = tk.Frame(card, bg=style["bg"])
        kw_row.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(kw_row, text=data.get("keyword", ""), bg=style["bg"], fg="#111111",
                 font=("맑은 고딕", 13, "bold")).pack(side="left")
        tk.Label(kw_row, text=f"[{data.get('category', '-')}]", bg=style["bg"], fg="#777777",
                 font=("맑은 고딕", 9)).pack(side="left", padx=(6, 0))

        # ---- 지표 라인 ----
        stat_row = tk.Frame(card, bg=style["bg"])
        stat_row.pack(fill="x", padx=12, pady=(0, 4))
        stat_text = (f"검색량 {data.get('search_volume', 0):,} · "
                      f"문서수 {data.get('doc_count', 0):,} · "
                      f"효율 {data.get('efficiency', 0):.2f} · "
                      f"{data.get('timing', '상시')}")
        tk.Label(stat_row, text=stat_text, bg=style["bg"], fg="#444444",
                 font=("맑은 고딕", 9)).pack(side="left")

        # ---- 위험/보류 사유 라인 (있을 때만) ----
        reason = None
        if data.get("grade") == "위험" and data.get("risk_reasons"):
            reason = "위험 사유: " + ", ".join(data["risk_reasons"])
        elif data.get("grade") == "보류" and data.get("hold_reasons"):
            reason = "보류 사유: " + ", ".join(data["hold_reasons"])

        if reason:
            tk.Label(card, text=reason, bg=style["bg"], fg=style["border"],
                     font=("맑은 고딕", 9, "italic"), wraplength=620,
                     justify="left", anchor="w").pack(fill="x", padx=12, pady=(0, 9), anchor="w")
        else:
            tk.Frame(card, bg=style["bg"], height=6).pack()

        # 카드 전체 클릭 가능하게 바인딩
        clickable = [self, card, top_row, kw_row, stat_row]
        for w in clickable:
            w.bind("<Button-1>", self._click)
            w.configure(cursor="hand2")
        for w in card.winfo_children():
            self._bind_children(w)

    def _bind_children(self, widget):
        widget.bind("<Button-1>", self._click)
        widget.configure(cursor="hand2")
        for c in widget.winfo_children():
            self._bind_children(c)

    def _click(self, event=None):
        self.on_click(self.data)


# =========================================================================
# 상세 분석 패널
# =========================================================================
class DetailPanel(ttk.Frame):
    def __init__(self, parent, on_generate_draft, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.on_generate_draft = on_generate_draft
        self.data = None

        ttk.Label(self, text="상세 분석", font=("맑은 고딕", 12, "bold")).pack(anchor="w", pady=(0, 6))

        self.body = scrolledtext.ScrolledText(self, wrap="word", height=18, font=("맑은 고딕", 10))
        self.body.pack(fill="both", expand=True)
        self.body.configure(state="disabled")

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", pady=8)
        ttk.Button(btn_row, text="글초안 생성", command=self._draft).pack(side="left")
        ttk.Button(btn_row, text="복사", command=self._copy).pack(side="left", padx=(6, 0))

    def _set_text(self, text):
        self.body.configure(state="normal")
        self.body.delete("1.0", "end")
        self.body.insert("1.0", text)
        self.body.configure(state="disabled")

    def show(self, data):
        self.data = data
        style = GRADE_STYLE.get(data.get("grade", "보류"), GRADE_STYLE["보류"])

        checks = {
            "뉴스 언급": data.get("verify_news", data.get("mentions", 0) > 0),
            "검색량 확보": data.get("verify_volume", data.get("search_volume", 0) > 0),
            "문서수 확보": data.get("verify_docs", data.get("doc_count", 0) > 0),
            "DataLab 상승률": data.get("verify_datalab", False),
        }
        check_lines = []
        for name, ok in checks.items():
            mark = "✔" if ok else "✘"
            check_lines.append(f"  {mark} {name}")

        lines = []
        lines.append(f"[{style['label']}]  {data.get('keyword', '')}")
        lines.append(f"카테고리: {data.get('category', '-')}   순위: {data.get('rank', '-')}")
        lines.append("")
        lines.append("=== 4단계 교차검증 ===")
        lines.extend(check_lines)
        lines.append("")
        lines.append("=== 점수 산출 ===")
        lines.append(f"IssueScore      : {data.get('issue_score', 0):.2f}")
        lines.append(f"OpportunityScore: {data.get('opportunity_score', 0):.2f}")
        lines.append(f"CategoryWeight  : {data.get('category_weight', 1.0):.2f}")
        lines.append(f"FinalScore      : {data.get('final_score', 0):.2f}")
        lines.append("")
        lines.append("=== 실측 지표 ===")
        lines.append(f"뉴스 언급 수 : {data.get('mentions', 0):,}")
        lines.append(f"검색량       : {data.get('search_volume', 0):,}")
        lines.append(f"문서수       : {data.get('doc_count', 0):,}")
        lines.append(f"검색량/문서수 효율 : {data.get('efficiency', 0):.2f}")
        lines.append(f"작성 타이밍  : {data.get('timing', '상시')}")

        if data.get("risk_reasons"):
            lines.append("")
            lines.append("=== 위험 사유 ===")
            for r in data["risk_reasons"]:
                lines.append(f"  - {r}")
        if data.get("hold_reasons"):
            lines.append("")
            lines.append("=== 보류 사유 ===")
            for r in data["hold_reasons"]:
                lines.append(f"  - {r}")

        self._set_text("\n".join(lines))

    def _draft(self):
        if not self.data:
            return
        draft = self.on_generate_draft(self.data)
        self._set_text(draft)

    def _copy(self):
        content = self.body.get("1.0", "end")
        self.clipboard_clear()
        self.clipboard_append(content)


def generate_draft_text(data):
    """외부 AI API 없이 템플릿 기반으로 글초안 뼈대를 생성."""
    kw = data.get("keyword", "")
    cat = data.get("category", "")
    timing = data.get("timing", "상시")
    lines = [
        f"[글초안] {kw}",
        "",
        f"■ 카테고리: {cat}   ■ 작성 타이밍: {timing}",
        "",
        "1. 제목 후보",
        f"   - {kw}, 놓치면 후회하는 이유",
        f"   - {kw} 조건 및 신청 방법 총정리",
        f"   - 2026년 {kw} 최신 정보",
        "",
        "2. 서론",
        f"   {kw}에 대한 관심이 높아지고 있는 배경과, 이 글에서 다룰 핵심 내용을 간단히 소개합니다.",
        "",
        "3. 본문 목차",
        f"   - {kw}란 무엇인가",
        f"   - {kw} 대상 및 조건",
        f"   - {kw} 신청/이용 방법",
        f"   - 주의할 점 및 자주 묻는 질문",
        "",
        "4. 결론",
        f"   {kw} 관련 핵심 요약과 행동 유도(신청 링크, 관련 글 안내 등)를 마무리합니다.",
        "",
        f"※ 검색량 {data.get('search_volume', 0):,} / 문서수 {data.get('doc_count', 0):,} "
        f"/ 효율 {data.get('efficiency', 0):.2f} 기준으로 산출된 초안입니다.",
    ]
    return "\n".join(lines)


# =========================================================================
# 메인 애플리케이션
# =========================================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE} {APP_VERSION}")
        self.geometry("1360x860")
        self.minsize(1100, 700)

        self.cfg = load_config()
        self.results = []
        self.current_filter = "전체"
        self.search_var = tk.StringVar()
        self.queue = queue.Queue()
        self.worker_thread = None

        self._build_style()
        self._build_layout()
        self._poll_queue()

    # ---------------------------------------------------------------
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except Exception:
            pass

    # ---------------------------------------------------------------
    def _build_layout(self):
        # 상단 툴바
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text=APP_TITLE, font=("맑은 고딕", 14, "bold")).pack(side="left")
        ttk.Label(top, text=APP_VERSION, foreground="#888888").pack(side="left", padx=(6, 0))

        self.run_btn = ttk.Button(top, text="\u25B6 오늘 분석 시작", command=self._start_analysis)
        self.run_btn.pack(side="right")

        self.settings_toggle_btn = ttk.Button(top, text="API 설정", command=self._toggle_settings)
        self.settings_toggle_btn.pack(side="right", padx=(0, 8))

        ttk.Button(top, text="CSV 내보내기", command=self._export_csv).pack(side="right", padx=(0, 8))

        # 설정 패널 (기본 접힘)
        self.settings_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        self._build_settings_panel(self.settings_frame)
        self.settings_visible = False

        # 진행률 바
        self.progress = ttk.Progressbar(self, mode="indeterminate")

        # 필터 칩 바
        filter_bar = ttk.Frame(self, padding=(10, 4, 10, 4))
        filter_bar.pack(fill="x")
        self.filter_buttons = {}
        for label in ["전체"] + GRADE_ORDER:
            btn = ttk.Button(filter_bar, text=label, command=lambda l=label: self._apply_filter(l))
            btn.pack(side="left", padx=4)
            self.filter_buttons[label] = btn

        ttk.Label(filter_bar, text="검색:").pack(side="left", padx=(16, 4))
        search_entry = ttk.Entry(filter_bar, textvariable=self.search_var, width=20)
        search_entry.pack(side="left")
        search_entry.bind("<KeyRelease>", lambda e: self._render_cards())

        self.summary_label = ttk.Label(filter_bar, text="", foreground="#555555")
        self.summary_label.pack(side="right")

        # 본문: 좌(카드 리스트) / 우(상세 패널)
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        self.card_area = ScrollableFrame(left, bg="#FAFAFA")
        self.card_area.pack(fill="both", expand=True)

        right = ttk.Frame(body, width=420)
        right.pack(side="right", fill="y", padx=(10, 0))
        self.detail_panel = DetailPanel(right, on_generate_draft=generate_draft_text)
        self.detail_panel.pack(fill="both", expand=True)

        # 로그 영역 (하단, 접기/펼치기)
        self.log_section = CollapsibleSection(self, "실행 로그", start_expanded=False)
        self.log_section.pack(fill="x", padx=10, pady=(0, 10))
        self.log_text = scrolledtext.ScrolledText(self.log_section.body, height=8, font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

    # ---------------------------------------------------------------
    def _build_settings_panel(self, parent):
        self.entries = {}

        sec_search = CollapsibleSection(parent, "① 검색/데이터랩 API (developers.naver.com)", start_expanded=True)
        sec_search.pack(fill="x", pady=4)
        self._add_field(sec_search.body, "Client ID", "naver_client_id")
        self._add_field(sec_search.body, "Client Secret", "naver_client_secret", secret=True)
        ttk.Button(sec_search.body, text="검색 API 연결 테스트",
                   command=self._test_search_api).grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Button(sec_search.body, text="데이터랩 API 연결 테스트",
                   command=self._test_datalab_api).grid(row=2, column=1, sticky="w", pady=(6, 0))

        sec_ads = CollapsibleSection(parent, "② 검색광고 API (searchad.naver.com)", start_expanded=True)
        sec_ads.pack(fill="x", pady=4)
        self._add_field(sec_ads.body, "Customer ID", "ads_customer_id")
        self._add_field(sec_ads.body, "License Key(엑세스라이선스)", "ads_license_key")
        self._add_field(sec_ads.body, "Secret Key(비밀키)", "ads_secret_key", secret=True)
        ttk.Button(sec_ads.body, text="검색광고 API 연결 테스트",
                   command=self._test_ads_api).grid(row=3, column=0, sticky="w", pady=(6, 0))

        sec_cat = CollapsibleSection(parent, "③ 수익형 카테고리 선택", start_expanded=True)
        sec_cat.pack(fill="x", pady=4)
        self.category_vars = {}
        cats = self.cfg.get("categories", DEFAULT_CATEGORIES)
        for i, cat in enumerate(DEFAULT_CATEGORIES):
            var = tk.BooleanVar(value=(cat in cats))
            cb = ttk.Checkbutton(sec_cat.body, text=cat, variable=var)
            cb.grid(row=i // 4, column=i % 4, sticky="w", padx=6, pady=2)
            self.category_vars[cat] = var

        ttk.Button(parent, text="설정 저장", command=self._save_settings).pack(anchor="e", pady=(8, 0))

    def _add_field(self, parent, label, key, secret=False):
        row = len(self.entries)
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=2)
        var = tk.StringVar(value=self.cfg.get(key, ""))
        entry = ttk.Entry(parent, textvariable=var, width=42, show="*" if secret else "")
        entry.grid(row=row, column=1, sticky="w", pady=2)
        self.entries[key] = var

    def _toggle_settings(self):
        if self.settings_visible:
            self.settings_frame.pack_forget()
        else:
            self.settings_frame.pack(fill="x", after=self.children[list(self.children)[0]])
        self.settings_visible = not self.settings_visible

    def _save_settings(self):
        for key, var in self.entries.items():
            self.cfg[key] = var.get().strip()
        self.cfg["categories"] = [c for c, v in self.category_vars.items() if v.get()]
        if save_config(self.cfg):
            messagebox.showinfo("저장 완료", "설정이 저장되었습니다.")
        else:
            messagebox.showerror("저장 실패", "설정 파일 저장에 실패했습니다.")

    # ---- 연결 테스트 -------------------------------------------------
    def _test_search_api(self):
        self._save_settings()
        api = NaverSearchAPI(self.cfg["naver_client_id"], self.cfg["naver_client_secret"])
        ok, msg = api.test_connection()
        (messagebox.showinfo if ok else messagebox.showerror)("검색 API 테스트", msg)

    def _test_datalab_api(self):
        self._save_settings()
        api = NaverDataLabAPI(self.cfg["naver_client_id"], self.cfg["naver_client_secret"])
        ok, msg = api.test_connection()
        (messagebox.showinfo if ok else messagebox.showerror)("데이터랩 API 테스트", msg)

    def _test_ads_api(self):
        self._save_settings()
        api = NaverAdsAPI(self.cfg["ads_customer_id"], self.cfg["ads_license_key"], self.cfg["ads_secret_key"])
        ok, msg = api.test_connection()
        (messagebox.showinfo if ok else messagebox.showerror)("검색광고 API 테스트", msg)

    # ---- 로그 ----------------------------------------------------------
    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.queue.put(("log", line))
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self.log_text.insert("end", payload + "\n")
                    self.log_text.see("end")
                elif kind == "done":
                    self._on_analysis_done(payload)
                elif kind == "error":
                    self._on_analysis_error(payload)
        except queue.Empty:
            pass
        self.after(150, self._poll_queue)

    # ---- 분석 실행 -------------------------------------------------------
    def _start_analysis(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning("진행 중", "이미 분석이 진행 중입니다.")
            return

        self._save_settings()
        required = ["naver_client_id", "naver_client_secret",
                    "ads_customer_id", "ads_license_key", "ads_secret_key"]
        missing = [k for k in required if not self.cfg.get(k)]
        if missing:
            messagebox.showerror("설정 필요", "API 설정을 모두 입력한 뒤 연결 테스트를 통과해야 합니다.")
            return

        self.run_btn.config(state="disabled")
        self.progress.pack(fill="x", padx=10, pady=(0, 4))
        self.progress.start(12)
        self.log_text.delete("1.0", "end")
        self._log("=== 분석 시작 ===")

        categories = self.cfg.get("categories") or DEFAULT_CATEGORIES
        max_workers = int(self.cfg.get("max_workers", 4))

        self.worker_thread = threading.Thread(
            target=self._worker, args=(categories, max_workers), daemon=True
        )
        self.worker_thread.start()

    def _worker(self, categories, max_workers):
        try:
            search_api = NaverSearchAPI(self.cfg["naver_client_id"], self.cfg["naver_client_secret"])
            datalab_api = NaverDataLabAPI(self.cfg["naver_client_id"], self.cfg["naver_client_secret"])
            ads_api = NaverAdsAPI(self.cfg["ads_customer_id"], self.cfg["ads_license_key"], self.cfg["ads_secret_key"])
            apis = {"search": search_api, "datalab": datalab_api, "ads": ads_api}

            self._log(f"후보 수집 시작 (카테고리: {', '.join(categories)})")
            candidates = collector.collect_candidates(
                search_api=search_api,
                discovery_target=categories,
                light_filter_target=None,
                log=self._log,
                max_workers=max_workers,
            )
            self._log(f"후보 수집 완료: {len(candidates)}건")

            filtered = profit_filter.filter_candidates(candidates, log=self._log)
            self._log(f"수익형 필터 통과: {len(filtered)}건")

            results = scorer.score_candidates(filtered, apis, log=self._log)
            self._log(f"점수 산출 및 등급 분류 완료: {len(results)}건")

            self.queue.put(("done", results))
        except Exception as e:
            tb = traceback.format_exc()
            self._log(f"[오류] {e}")
            self._log(tb)
            self.queue.put(("error", str(e)))

    def _on_analysis_done(self, results):
        self.progress.stop()
        self.progress.pack_forget()
        self.run_btn.config(state="normal")
        self.results = results
        self._log(f"=== 분석 종료: 총 {len(results)}건 ===")
        self._render_cards()

    def _on_analysis_error(self, err):
        self.progress.stop()
        self.progress.pack_forget()
        self.run_btn.config(state="normal")
        messagebox.showerror("분석 실패", f"오류가 발생했습니다:\n{err}\n\n로그를 확인해 주세요.")

    # ---- 필터 -------------------------------------------------------------
    def _apply_filter(self, label):
        self.current_filter = label
        self._render_cards()

    def _filtered_results(self):
        data = self.results
        if self.current_filter != "전체":
            data = [d for d in data if d.get("grade") == self.current_filter]
        kw = self.search_var.get().strip()
        if kw:
            data = [d for d in data if kw in d.get("keyword", "")]
        return data

    # ---- 카드 렌더링 --------------------------------------------------------
    def _render_cards(self):
        self.card_area.clear()
        data = self._filtered_results()

        counts = {g: len([d for d in self.results if d.get("grade") == g]) for g in GRADE_ORDER}
        self.summary_label.config(
            text=f"TOP5 {counts['TOP5']} · TOP10 {counts['TOP10']} · 보류 {counts['보류']} · 위험 {counts['위험']}"
        )

        if not data:
            tk.Label(self.card_area.inner, text="표시할 결과가 없습니다. '오늘 분석 시작'을 눌러 분석을 실행하세요.",
                     bg="#FAFAFA", fg="#999999", font=("맑은 고딕", 11)).pack(pady=40)
            return

        sorted_data = sorted(
            data,
            key=lambda d: (GRADE_ORDER.index(d.get("grade", "보류"))
                           if d.get("grade") in GRADE_ORDER else 99,
                           -(d.get("final_score", 0)))
        )

        for item in sorted_data:
            KeywordCard(self.card_area.inner, item, on_click=self._show_detail)

    def _show_detail(self, data):
        self.detail_panel.show(data)

    # ---- CSV 내보내기 --------------------------------------------------------
    def _export_csv(self):
        if not self.results:
            messagebox.showwarning("내보내기 불가", "내보낼 분석 결과가 없습니다.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV 파일", "*.csv")],
            initialfile=f"keywords_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        )
        if not path:
            return
        fields = ["rank", "grade", "keyword", "category", "final_score", "issue_score",
                  "opportunity_score", "search_volume", "doc_count", "efficiency",
                  "mentions", "timing", "risk_reasons", "hold_reasons"]
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                for item in self.results:
                    row = dict(item)
                    row["risk_reasons"] = ", ".join(item.get("risk_reasons", []))
                    row["hold_reasons"] = ", ".join(item.get("hold_reasons", []))
                    writer.writerow(row)
            messagebox.showinfo("내보내기 완료", f"저장되었습니다:\n{path}")
        except Exception as e:
            messagebox.showerror("내보내기 실패", str(e))


if __name__ == "__main__":
    app = App()
    app.mainloop()

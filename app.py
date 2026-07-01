# -*- coding: utf-8 -*-
"""
app.py (v19)
네이버 블로그 수익형 키워드 발굴 시스템 - 대시보드형 UI

사용 흐름: [분석 시작] -> [요약바에서 등급 확인] -> [표에서 키워드 클릭]
           -> [우측 상세에서 추천 이유 확인] -> [하단에서 글초안/제목/FAQ 작성]

API 설정은 상단 메뉴 '설정 > API 설정'에서만 접근 가능한 별도 팝업창으로 분리.
평소 화면에는 절대 노출되지 않음.

파이프라인:
  collector.collect_candidates()   -> 후보 수집
  profit_filter.filter_candidates()-> 수익형 필터링(카테고리 가중치/검색의도)
  scorer.score_candidates()        -> 4단계 교차검증 + 점수화 + 등급 분류
                                       (v18.6부터 (results, api_health) 튜플 반환)

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

import collector
import profit_filter
import scorer
from naver_search_api import NaverSearchAPI, NaverDataLabAPI, NaverAdsAPI

APP_TITLE = "오늘의 수익형 키워드 발굴기"
APP_VERSION = "v19"

BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LOG_FILE = os.path.join(BASE_DIR, "trend_debug_log.txt")

DEFAULT_CATEGORIES = ["보험", "대출", "환급", "지원금", "연금", "세금",
                       "청약", "부동산", "카드", "자동차보험", "건강보험"]

GRADE_ORDER = ["TOP5", "TOP10", "보류", "위험"]
GRADE_COLOR = {
    "TOP5":  {"bg": "#FFF6DC", "fg": "#8A6D00", "row_bg": "#FFF9EA"},
    "TOP10": {"bg": "#EEF1F4", "fg": "#4A5A68", "row_bg": "#F5F7F9"},
    "보류":   {"bg": "#FFEFDC", "fg": "#B25900", "row_bg": "#FFF5E9"},
    "위험":   {"bg": "#FBE0E0", "fg": "#B02B2B", "row_bg": "#FDEDED"},
}
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

DEFAULT_CONFIG = {
    "naver_client_id": "",
    "naver_client_secret": "",
    "ads_customer_id": "",
    "ads_license_key": "",
    "ads_secret_key": "",
    "categories": DEFAULT_CATEGORIES,
    "max_workers": 4,
    "api_status": {"search": None, "datalab": None, "ads": None},
    "last_run": None,
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
# 설정 팝업창 (파일/설정/도움말 메뉴에서만 진입, 평소엔 절대 비노출)
# =========================================================================
class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, cfg, on_saved):
        super().__init__(parent)
        self.title("API 설정")
        self.geometry("560x520")
        self.resizable(False, False)
        self.cfg = cfg
        self.on_saved = on_saved
        self.entries = {}
        self.transient(parent)
        self.grab_set()

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        tab1 = ttk.Frame(nb, padding=14)
        tab2 = ttk.Frame(nb, padding=14)
        tab3 = ttk.Frame(nb, padding=14)
        nb.add(tab1, text="① 검색/데이터랩")
        nb.add(tab2, text="② 검색광고")
        nb.add(tab3, text="③ 카테고리")

        ttk.Label(tab1, text="developers.naver.com 애플리케이션 등록 정보",
                  foreground="#777777").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self._field(tab1, "Client ID", "naver_client_id", 1)
        self._field(tab1, "Client Secret", "naver_client_secret", 2, secret=True)
        ttk.Button(tab1, text="검색 API 테스트", command=self._test_search).grid(row=3, column=0, pady=10, sticky="w")
        ttk.Button(tab1, text="데이터랩 API 테스트", command=self._test_datalab).grid(row=3, column=1, pady=10, sticky="w")
        self.status1 = ttk.Label(tab1, text="", foreground="#555555")
        self.status1.grid(row=4, column=0, columnspan=2, sticky="w")

        ttk.Label(tab2, text="searchad.naver.com 별도 인증 정보 (Customer ID / License Key / Secret Key)",
                  foreground="#777777").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self._field(tab2, "Customer ID", "ads_customer_id", 1)
        self._field(tab2, "License Key(엑세스라이선스)", "ads_license_key", 2)
        self._field(tab2, "Secret Key(비밀키)", "ads_secret_key", 3, secret=True)
        ttk.Button(tab2, text="검색광고 API 테스트", command=self._test_ads).grid(row=4, column=0, pady=10, sticky="w")
        self.status2 = ttk.Label(tab2, text="", foreground="#555555")
        self.status2.grid(row=5, column=0, columnspan=2, sticky="w")

        ttk.Label(tab3, text="분석 대상 수익형 카테고리를 선택하세요.",
                  foreground="#777777").pack(anchor="w", pady=(0, 10))
        self.category_vars = {}
        grid = ttk.Frame(tab3)
        grid.pack(anchor="w")
        cats = self.cfg.get("categories", DEFAULT_CATEGORIES)
        for i, cat in enumerate(DEFAULT_CATEGORIES):
            var = tk.BooleanVar(value=(cat in cats))
            ttk.Checkbutton(grid, text=cat, variable=var).grid(row=i // 4, column=i % 4, sticky="w", padx=8, pady=4)
            self.category_vars[cat] = var

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(btn_row, text="저장", command=self._save).pack(side="right")
        ttk.Button(btn_row, text="취소", command=self.destroy).pack(side="right", padx=(0, 6))

    def _field(self, parent, label, key, row, secret=False):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
        var = tk.StringVar(value=self.cfg.get(key, ""))
        ttk.Entry(parent, textvariable=var, width=38, show="*" if secret else "").grid(row=row, column=1, pady=4)
        self.entries[key] = var

    def _current_values(self):
        for k, v in self.entries.items():
            self.cfg[k] = v.get().strip()
        self.cfg["categories"] = [c for c, v in self.category_vars.items() if v.get()]
        return self.cfg

    def _test_search(self):
        cfg = self._current_values()
        api = NaverSearchAPI(cfg["naver_client_id"], cfg["naver_client_secret"])
        ok, msg, hint = api.test_connection()
        text = ("✔ " if ok else "✘ ") + msg + (f"\n{hint}" if hint else "")
        self.cfg["api_status"]["search"] = bool(ok)
        self.status1.config(text=text, foreground="#2E7D32" if ok else "#C62828")

    def _test_datalab(self):
        cfg = self._current_values()
        api = NaverDataLabAPI(cfg["naver_client_id"], cfg["naver_client_secret"])
        ok, msg, hint = api.test_connection()
        text = ("✔ " if ok else "✘ ") + msg + (f"\n{hint}" if hint else "")
        self.cfg["api_status"]["datalab"] = bool(ok)
        self.status1.config(text=text, foreground="#2E7D32" if ok else "#C62828")

    def _test_ads(self):
        cfg = self._current_values()
        api = NaverAdsAPI(cfg["ads_customer_id"], cfg["ads_license_key"], cfg["ads_secret_key"])
        ok, msg, hint = api.test_connection()
        text = ("✔ " if ok else "✘ ") + msg + (f"\n{hint}" if hint else "")
        self.cfg["api_status"]["ads"] = bool(ok)
        self.status2.config(text=text, foreground="#2E7D32" if ok else "#C62828")

    def _save(self):
        cfg = self._current_values()
        if save_config(cfg):
            messagebox.showinfo("저장 완료", "API 설정이 저장되었습니다.", parent=self)
            self.on_saved(cfg)
            self.destroy()
        else:
            messagebox.showerror("저장 실패", "설정 파일 저장에 실패했습니다.", parent=self)


# =========================================================================
# 로그 창 (도움말 메뉴에서만 진입)
# =========================================================================
class LogWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("실행 로그")
        self.geometry("700x420")
        self.text = scrolledtext.ScrolledText(self, font=("Consolas", 9))
        self.text.pack(fill="both", expand=True)
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    self.text.insert("1.0", f.read())
            except Exception:
                pass
        self.text.see("end")

    def append(self, line):
        self.text.insert("end", line + "\n")
        self.text.see("end")


# =========================================================================
# 추천 이유 산출 (왜 TOP5/TOP10인지 한눈에 보여주는 로직)
# =========================================================================
def build_recommendation(data):
    """scorer.py 결과 필드를 바탕으로 별점과 추천 배지를 계산."""
    badges = []
    stars = 3  # 기본 3점에서 가감

    search_volume = data.get("search_volume", 0)
    doc_count = data.get("doc_count", 0) or 0
    efficiency = data.get("efficiency", 0)
    datalab_rising = data.get("verify_datalab", False)
    category_weight = data.get("category_weight", 1.0)

    if search_volume >= 5000:
        badges.append("검색량 ▲")
        stars += 1
    if doc_count > 0 and doc_count <= 1500:
        badges.append("문서수 적음")
        stars += 1
    if datalab_rising:
        badges.append("DataLab 상승")
        stars += 1
    if category_weight >= 1.3:
        badges.append("예상수익 상")
        stars += 1
    if data.get("timing") in ("오늘", "오후"):
        badges.append("오늘 작성 추천")

    # reason_tags가 이미 scorer.py에서 만들어졌다면 그대로 병합해 보여준다.
    for tag in data.get("reason_tags", []):
        if tag not in badges:
            badges.append(tag)

    stars = max(1, min(5, stars))
    return stars, badges


def profit_label(data):
    cw = data.get("category_weight", 1.0)
    if cw >= 1.3:
        return "상"
    if cw >= 1.0:
        return "중"
    return "하"


def generate_draft_text(data):
    kw = data.get("keyword", "")
    cat = data.get("category", "")
    timing = data.get("timing", "상시")
    lines = [
        f"[글초안] {kw}",
        "",
        f"■ 카테고리: {cat}   ■ 작성 타이밍: {timing}",
        "",
        "1. 서론",
        f"   {kw}에 대한 관심이 높아지는 배경과 이 글에서 다룰 핵심 내용을 소개합니다.",
        "",
        "2. 본문 목차",
        f"   - {kw}란 무엇인가",
        f"   - {kw} 대상 및 조건",
        f"   - {kw} 신청/이용 방법",
        f"   - 주의할 점 및 자주 묻는 질문",
        "",
        "3. 결론",
        f"   {kw} 핵심 요약과 행동 유도(신청 링크, 관련 글 안내) 문단으로 마무리합니다.",
        "",
        f"※ 검색량 {data.get('search_volume', 0):,} / 문서수 {data.get('doc_count', 0) or 0:,} "
        f"/ 효율 {data.get('efficiency', 0):.2f} 기준 초안입니다.",
    ]
    return "\n".join(lines)


def generate_title_candidates(data):
    kw = data.get("keyword", "")
    return "\n".join([
        f"1. {kw}, 놓치면 후회하는 이유",
        f"2. {kw} 조건 및 신청 방법 총정리",
        f"3. 2026년 {kw} 최신 정보",
        f"4. {kw} 신청 전 반드시 확인할 3가지",
        f"5. {kw} A to Z, 이 글 하나로 끝내기",
    ])


def generate_faq_candidates(data):
    kw = data.get("keyword", "")
    return "\n".join([
        f"Q1. {kw}는 누구나 신청할 수 있나요?",
        f"A1. 대상 조건은 본문의 '대상 및 조건' 항목을 참고해 주세요.",
        "",
        f"Q2. {kw} 신청 기한은 언제까지인가요?",
        f"A2. 최신 공고 기준으로 확인이 필요하며, 변경될 수 있습니다.",
        "",
        f"Q3. {kw} 신청 시 필요한 서류는 무엇인가요?",
        f"A3. 신분증, 관련 증빙서류 등이 일반적으로 요구됩니다.",
    ])


# =========================================================================
# 상세 패널 (우측)
# =========================================================================
class DetailPanel(ttk.Frame):
    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.data = None

        self.title_label = ttk.Label(self, text="키워드를 선택하세요", font=("맑은 고딕", 13, "bold"))
        self.title_label.pack(anchor="w", pady=(0, 4))

        self.meta_label = ttk.Label(self, text="", foreground="#666666")
        self.meta_label.pack(anchor="w", pady=(0, 8))

        self.star_label = tk.Label(self, text="", font=("맑은 고딕", 14), fg="#E6B800")
        self.star_label.pack(anchor="w")

        self.badge_frame = ttk.Frame(self)
        self.badge_frame.pack(anchor="w", pady=(4, 10))

        stat_box = ttk.LabelFrame(self, text="실측 지표", padding=8)
        stat_box.pack(fill="x", pady=(0, 10))
        self.stat_text = tk.Label(stat_box, text="", justify="left", anchor="w")
        self.stat_text.pack(fill="x")

        check_box = ttk.LabelFrame(self, text="4단계 교차검증", padding=8)
        check_box.pack(fill="x", pady=(0, 10))
        self.check_text = tk.Label(check_box, text="", justify="left", anchor="w")
        self.check_text.pack(fill="x")

        reason_box = ttk.LabelFrame(self, text="위험 / 보류 사유", padding=8)
        reason_box.pack(fill="both", expand=True)
        self.reason_text = tk.Label(reason_box, text="해당 없음", justify="left", anchor="w",
                                     wraplength=380, fg="#555555")
        self.reason_text.pack(fill="both", expand=True)

    def show(self, data):
        self.data = data
        style = GRADE_COLOR.get(data.get("grade", "보류"), GRADE_COLOR["보류"])

        self.title_label.config(text=data.get("keyword", ""))
        self.meta_label.config(
            text=f"[{data.get('grade', '-')}]  카테고리: {data.get('category', '-')}  "
                 f"순위: {data.get('rank', '-')}  FinalScore: {data.get('final_score', 0):.1f}"
        )

        stars, badges = build_recommendation(data)
        self.star_label.config(text="★" * stars + "☆" * (5 - stars))

        for w in self.badge_frame.winfo_children():
            w.destroy()
        for b in badges:
            tk.Label(self.badge_frame, text=b, bg="#EAF3FF", fg="#1565C0",
                     font=("맑은 고딕", 9, "bold"), padx=6, pady=2).pack(side="left", padx=(0, 6))
        if not badges:
            ttk.Label(self.badge_frame, text="특이 추천 요소 없음", foreground="#999999").pack(side="left")

        doc_count = data.get("doc_count")
        doc_count_text = f"{doc_count:,}" if doc_count is not None else "확인 불가"

        self.stat_text.config(text=(
            f"검색량 : {data.get('search_volume', 0):,}\n"
            f"문서수 : {doc_count_text}\n"
            f"검색량/문서수 효율 : {data.get('efficiency', 0):.2f}\n"
            f"작성 타이밍 : {data.get('timing', '상시')}\n"
            f"예상 수익성 : {profit_label(data)}\n"
            f"출처 : {', '.join(data.get('source', []))}"
        ))

        checks = {
            "뉴스 언급": data.get("verify_news", data.get("mentions", 0) > 0),
            "검색량 확보": data.get("verify_volume", data.get("search_volume", 0) > 0),
            "문서수 확보": data.get("verify_docs", False),
            "DataLab 상승률": data.get("verify_datalab", False),
        }
        check_lines = [("✔ " if ok else "✘ ") + name for name, ok in checks.items()]
        self.check_text.config(text="\n".join(check_lines))

        reasons = []
        if data.get("risk_reasons"):
            reasons.append("위험 사유: " + ", ".join(data["risk_reasons"]))
        if data.get("hold_reasons"):
            reasons.append("보류 사유: " + ", ".join(data["hold_reasons"]))
        self.reason_text.config(text="\n".join(reasons) if reasons else "해당 없음")

    def clear(self):
        self.data = None
        self.title_label.config(text="키워드를 선택하세요")
        self.meta_label.config(text="")
        self.star_label.config(text="")
        for w in self.badge_frame.winfo_children():
            w.destroy()
        self.stat_text.config(text="")
        self.check_text.config(text="")
        self.reason_text.config(text="해당 없음")


# =========================================================================
# 하단 작성 패널 (글초안 / 제목 / FAQ)
# =========================================================================
class WritingPanel(ttk.Frame):
    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.data = None

        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 4))
        self.gen_btn = ttk.Button(top, text="✎ 콘텐츠 생성", command=self._generate, state="disabled")
        self.gen_btn.pack(side="left")
        self.hint_label = ttk.Label(top, text="TOP5 / TOP10 키워드만 콘텐츠 생성이 가능합니다.",
                                     foreground="#999999")
        self.hint_label.pack(side="left", padx=(10, 0))

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        self.tabs = {}
        for name in ["글초안", "제목", "FAQ"]:
            frame = ttk.Frame(nb, padding=6)
            nb.add(frame, text=name)
            text = scrolledtext.ScrolledText(frame, wrap="word", height=8, font=("맑은 고딕", 10))
            text.pack(fill="both", expand=True)
            btn_row = ttk.Frame(frame)
            btn_row.pack(fill="x", pady=(4, 0))
            ttk.Button(btn_row, text="복사", command=lambda t=text: self._copy(t)).pack(side="right")
            self.tabs[name] = text

    def _copy(self, text_widget):
        content = text_widget.get("1.0", "end")
        self.clipboard_clear()
        self.clipboard_append(content)

    def set_selected(self, data):
        self.data = data
        if data and data.get("grade") in ("TOP5", "TOP10"):
            self.gen_btn.config(state="normal")
            self.hint_label.config(text="")
        else:
            self.gen_btn.config(state="disabled")
            self.hint_label.config(text="TOP5 / TOP10 키워드만 콘텐츠 생성이 가능합니다.")

    def _generate(self):
        if not self.data:
            return
        self.tabs["글초안"].delete("1.0", "end")
        self.tabs["글초안"].insert("1.0", generate_draft_text(self.data))
        self.tabs["제목"].delete("1.0", "end")
        self.tabs["제목"].insert("1.0", generate_title_candidates(self.data))
        self.tabs["FAQ"].delete("1.0", "end")
        self.tabs["FAQ"].insert("1.0", generate_faq_candidates(self.data))


# =========================================================================
# 메인 애플리케이션
# =========================================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE} {APP_VERSION}")
        self.geometry("1420x900")
        self.minsize(1180, 720)

        self.cfg = load_config()
        self.results = []
        self.log_window = None
        self.queue = queue.Queue()
        self.worker_thread = None
        self.item_map = {}  # treeview row id -> data dict

        self._build_menu()
        self._build_header()
        self._build_summary_bar()
        self._build_body()
        self._build_writing_panel()
        self._poll_queue()

    # ---------------------------------------------------------------
    def _build_menu(self):
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="CSV 내보내기", command=self._export_csv)
        file_menu.add_separator()
        file_menu.add_command(label="종료", command=self.quit)
        menubar.add_cascade(label="파일", menu=file_menu)

        setting_menu = tk.Menu(menubar, tearoff=0)
        setting_menu.add_command(label="API 설정", command=self._open_settings)
        menubar.add_cascade(label="설정", menu=setting_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="실행 로그 보기", command=self._open_log_window)
        help_menu.add_command(label="정보", command=self._show_about)
        menubar.add_cascade(label="도움말", menu=help_menu)

        self.config(menu=menubar)

    def _open_settings(self):
        SettingsDialog(self, self.cfg, on_saved=self._on_settings_saved)

    def _on_settings_saved(self, cfg):
        self.cfg = cfg
        self._refresh_summary_bar()

    def _open_log_window(self):
        if self.log_window is None or not self.log_window.winfo_exists():
            self.log_window = LogWindow(self)
        else:
            self.log_window.lift()

    def _show_about(self):
        messagebox.showinfo("정보", f"{APP_TITLE}\n{APP_VERSION}\n\ncollector → profit_filter → scorer 파이프라인 기반")

    # ---------------------------------------------------------------
    def _build_header(self):
        header = ttk.Frame(self, padding=(14, 12, 14, 8))
        header.pack(fill="x")

        ttk.Label(header, text=APP_TITLE, font=("맑은 고딕", 16, "bold")).pack(side="left")
        ttk.Label(header, text=APP_VERSION, foreground="#999999").pack(side="left", padx=(6, 0))

        self.run_btn = ttk.Button(header, text="▶  분석 시작", command=self._start_analysis)
        self.run_btn.pack(side="right")

        self.progress = ttk.Progressbar(header, mode="indeterminate", length=160)
        self.progress_label = ttk.Label(header, text="", foreground="#1565C0")

    # ---------------------------------------------------------------
    def _build_summary_bar(self):
        bar = tk.Frame(self, bg="#F4F6F8")
        bar.pack(fill="x", padx=14, pady=(0, 10))

        self.summary_labels = {}

        def add_stat(key, text, fg="#333333"):
            box = tk.Frame(bar, bg="#F4F6F8")
            box.pack(side="left", padx=14, pady=8)
            val = tk.Label(box, text=text, bg="#F4F6F8", fg=fg, font=("맑은 고딕", 13, "bold"))
            val.pack()
            tk.Label(box, text=key, bg="#F4F6F8", fg="#888888", font=("맑은 고딕", 9)).pack()
            self.summary_labels[key] = val

        add_stat("TOP5", "0", GRADE_COLOR["TOP5"]["fg"])
        add_stat("TOP10", "0", GRADE_COLOR["TOP10"]["fg"])
        add_stat("보류", "0", GRADE_COLOR["보류"]["fg"])
        add_stat("위험", "0", GRADE_COLOR["위험"]["fg"])
        add_stat("성공률", "0%")
        add_stat("API 상태", "미확인")
        add_stat("마지막 분석", "-")

    def _refresh_summary_bar(self):
        counts = {g: 0 for g in GRADE_ORDER}
        for d in self.results:
            g = d.get("grade")
            if g in counts:
                counts[g] += 1
        total = len(self.results)
        success = counts["TOP5"] + counts["TOP10"]
        rate = (success / total * 100) if total else 0

        self.summary_labels["TOP5"].config(text=str(counts["TOP5"]))
        self.summary_labels["TOP10"].config(text=str(counts["TOP10"]))
        self.summary_labels["보류"].config(text=str(counts["보류"]))
        self.summary_labels["위험"].config(text=str(counts["위험"]))
        self.summary_labels["성공률"].config(text=f"{rate:.0f}%")

        # ★ api_status는 이제 scorer.score_candidates()가 반환한 api_health 딕셔너리
        #    {"search": "ok"|"partial"|"fail", "ads": ..., "datalab": ...} 형태.
        status = self.cfg.get("api_status", {})
        vals = [status.get(k) for k in ("search", "datalab", "ads")]
        if vals and all(v == "ok" for v in vals):
            self.summary_labels["API 상태"].config(text="정상", fg="#2E7D32")
        elif any(v == "fail" for v in vals):
            self.summary_labels["API 상태"].config(text="오류", fg="#C62828")
        elif any(v == "partial" for v in vals):
            self.summary_labels["API 상태"].config(text="일부 실패", fg="#F39C12")
        else:
            self.summary_labels["API 상태"].config(text="미확인", fg="#888888")

        last_run = self.cfg.get("last_run")
        self.summary_labels["마지막 분석"].config(text=last_run or "-")

    # ---------------------------------------------------------------
    def _build_body(self):
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        paned = ttk.PanedWindow(body, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # ---- 좌측: 순위표 (등급별 그룹 접기/펼치기) ----
        left = ttk.Frame(paned)
        paned.add(left, weight=3)

        columns = ("keyword", "category", "volume", "docs", "eff", "profit")
        self.tree = ttk.Treeview(left, columns=columns, show="tree headings", height=26)
        self.tree.heading("#0", text="등급/순위")
        self.tree.heading("keyword", text="키워드")
        self.tree.heading("category", text="카테고리")
        self.tree.heading("volume", text="검색량")
        self.tree.heading("docs", text="문서수")
        self.tree.heading("eff", text="효율")
        self.tree.heading("profit", text="수익성")

        self.tree.column("#0", width=110, anchor="w")
        self.tree.column("keyword", width=180, anchor="w")
        self.tree.column("category", width=90, anchor="center")
        self.tree.column("volume", width=80, anchor="e")
        self.tree.column("docs", width=80, anchor="e")
        self.tree.column("eff", width=70, anchor="e")
        self.tree.column("profit", width=60, anchor="center")

        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        for g in GRADE_ORDER:
            self.tree.tag_configure(f"group_{g}", background=GRADE_COLOR[g]["bg"],
                                     foreground=GRADE_COLOR[g]["fg"], font=("맑은 고딕", 10, "bold"))
            self.tree.tag_configure(f"row_{g}", background=GRADE_COLOR[g]["row_bg"])

        self.tree.bind("<<TreeviewSelect>>", self._on_select_row)

        # ---- 우측: 상세 패널 ----
        right = ttk.Frame(paned, padding=(12, 0, 0, 0))
        paned.add(right, weight=2)
        self.detail_panel = DetailPanel(right)
        self.detail_panel.pack(fill="both", expand=True)

    # ---------------------------------------------------------------
    def _build_writing_panel(self):
        wrap = ttk.LabelFrame(self, text="콘텐츠 작성", padding=10)
        wrap.pack(fill="both", expand=False, padx=14, pady=(0, 14))
        self.writing_panel = WritingPanel(wrap)
        self.writing_panel.pack(fill="both", expand=True)

    # ---------------------------------------------------------------
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
                    if self.log_window and self.log_window.winfo_exists():
                        self.log_window.append(payload)
                elif kind == "done":
                    self._on_analysis_done(payload)
                elif kind == "error":
                    self._on_analysis_error(payload)
        except queue.Empty:
            pass
        self.after(150, self._poll_queue)

    # ---- 분석 실행 -------------------------------------------------
    def _start_analysis(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning("진행 중", "이미 분석이 진행 중입니다.")
            return

        required = ["naver_client_id", "naver_client_secret",
                    "ads_customer_id", "ads_license_key", "ads_secret_key"]
        missing = [k for k in required if not self.cfg.get(k)]
        if missing:
            if messagebox.askyesno("설정 필요", "API 설정이 비어 있습니다. 지금 설정하시겠습니까?"):
                self._open_settings()
            return

        self.run_btn.config(state="disabled")
        self.progress.pack(side="right", padx=(0, 10))
        self.progress_label.pack(side="right", padx=(0, 6))
        self.progress.start(12)
        self.progress_label.config(text="분석 중...")

        categories = self.cfg.get("categories") or DEFAULT_CATEGORIES
        max_workers = int(self.cfg.get("max_workers", 4))

        self.worker_thread = threading.Thread(
            target=self._worker, args=(categories, max_workers), daemon=True
        )
        self.worker_thread.start()

    # =================================================================
    # ★★★ 수정된 함수 ①: _worker
    #     scorer.score_candidates()가 (results, api_health) 튜플을
    #     반환하도록 v18.6에서 바뀌었으므로, 그에 맞춰 두 값을 함께 받아
    #     딕셔너리 형태로 큐에 전달한다.
    # =================================================================
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

            # ★ scorer.py v18.6: (results, api_health) 튜플 반환
            results, api_health = scorer.score_candidates(filtered, apis, log=self._log)
            self._log(f"점수 산출 및 등급 분류 완료: {len(results)}건")
            self._log(
                f"API 상태 - 검색:{api_health.get('search')} "
                f"광고:{api_health.get('ads')} DataLab:{api_health.get('datalab')}"
            )

            # ★ results 단독이 아니라 results + api_health를 함께 전달
            self.queue.put(("done", {"results": results, "api_health": api_health}))
        except Exception as e:
            tb = traceback.format_exc()
            self._log(f"[오류] {e}")
            self._log(tb)
            self.queue.put(("error", str(e)))

    # =================================================================
    # ★★★ 수정된 함수 ②: _on_analysis_done
    #     payload가 이제 {"results":..., "api_health":...} 딕셔너리이므로
    #     이를 풀어서 self.results와 self.cfg["api_status"]에 각각 반영한다.
    # =================================================================
    def _on_analysis_done(self, payload):
        self.progress.stop()
        self.progress.pack_forget()
        self.progress_label.pack_forget()
        self.run_btn.config(state="normal")

        self.results = payload["results"]
        self.cfg["api_status"] = payload["api_health"]
        self.cfg["last_run"] = datetime.now().strftime("%m-%d %H:%M")
        save_config(self.cfg)

        self._log(f"=== 분석 종료: 총 {len(self.results)}건 ===")
        self._render_table()
        self._refresh_summary_bar()
        self.detail_panel.clear()
        self.writing_panel.set_selected(None)

    def _on_analysis_error(self, err):
        self.progress.stop()
        self.progress.pack_forget()
        self.progress_label.pack_forget()
        self.run_btn.config(state="normal")
        messagebox.showerror("분석 실패", f"오류가 발생했습니다:\n{err}\n\n도움말 > 실행 로그 보기에서 상세 내용을 확인해 주세요.")

    # ---- 표 렌더링 (등급별 그룹 + 접기/펼치기) --------------------------
    def _render_table(self):
        self.tree.delete(*self.tree.get_children())
        self.item_map.clear()

        grouped = {g: [] for g in GRADE_ORDER}
        for d in self.results:
            g = d.get("grade", "보류")
            if g in grouped:
                grouped[g].append(d)

        for g in GRADE_ORDER:
            items = sorted(grouped[g], key=lambda d: -(d.get("final_score", 0)))
            group_id = self.tree.insert(
                "", "end", text=f"{g} ({len(items)})",
                open=(g in ("TOP5", "TOP10")), tags=(f"group_{g}",)
            )
            for idx, d in enumerate(items, start=1):
                overall_rank = d.get("rank", idx)
                rank_text = MEDALS.get(overall_rank, str(overall_rank))
                doc_count = d.get("doc_count")
                doc_count_text = f"{doc_count:,}" if doc_count is not None else "-"
                row_id = self.tree.insert(
                    group_id, "end", text=rank_text,
                    values=(
                        d.get("keyword", ""),
                        d.get("category", "-"),
                        f"{d.get('search_volume', 0):,}",
                        doc_count_text,
                        f"{d.get('efficiency', 0):.2f}",
                        profit_label(d),
                    ),
                    tags=(f"row_{g}",)
                )
                self.item_map[row_id] = d

    def _on_select_row(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        row_id = sel[0]
        data = self.item_map.get(row_id)
        if data is None:
            return  # 그룹 헤더 행 클릭 시 무시
        self.detail_panel.show(data)
        self.writing_panel.set_selected(data)

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
                  "mentions", "timing", "risk_reasons", "hold_reasons", "reason_tags", "source"]
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                for item in self.results:
                    row = dict(item)
                    row["risk_reasons"] = ", ".join(item.get("risk_reasons", []))
                    row["hold_reasons"] = ", ".join(item.get("hold_reasons", []))
                    row["reason_tags"] = ", ".join(item.get("reason_tags", []))
                    row["source"] = ", ".join(item.get("source", []))
                    writer.writerow(row)
            messagebox.showinfo("내보내기 완료", f"저장되었습니다:\n{path}")
        except Exception as e:
            messagebox.showerror("내보내기 실패", str(e))


if __name__ == "__main__":
    app = App()
    app.mainloop()

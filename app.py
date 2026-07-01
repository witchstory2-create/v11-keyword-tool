import tkinter as tk
from tkinter import ttk, messagebox

from collector import collect_issue_keywords
from naver_api import get_keyword_data
from scorer import score_keyword
from title_engine import make_titles


analysis_results = []


def run_analysis():
    global analysis_results

    tree.delete(*tree.get_children())
    title_box.delete("1.0", tk.END)
    analysis_results = []

    cid = customer_entry.get().strip()
    api = api_entry.get().strip()
    secret = secret_entry.get().strip()

    if not cid or not api or not secret:
        messagebox.showwarning("입력 필요", "네이버 API 정보를 입력하세요.")
        return

    status.config(text="이슈 키워드 수집 중...")
    root.update()

    issue_keywords = collect_issue_keywords()

    if not issue_keywords:
        messagebox.showwarning("수집 실패", "이슈 키워드를 수집하지 못했습니다.")
        status.config(text="이슈 키워드 수집 실패")
        return

    status.config(text=f"후보 키워드 {len(issue_keywords)}개 수집, 네이버 API 분석 중...")
    root.update()

    results = []
    error_count = 0

    for kw in issue_keywords[:100]:
        try:
            data = get_keyword_data(kw, cid, api, secret)

            if not data:
                continue

            for item in data[:5]:
                scored = score_keyword(item, kw)
                results.append(scored)

        except Exception as e:
            error_count += 1
            print("API 오류:", kw, e)

    if not results:
        messagebox.showwarning(
            "분석 결과 없음",
            f"분석 결과가 0개입니다.\n후보 키워드 수: {len(issue_keywords)}개\nAPI 오류 수: {error_count}개"
        )
        status.config(text="분석 결과 없음")
        return

    results.sort(key=lambda x: x["final_score"], reverse=True)

    seen = set()
    unique_results = []

    for r in results:
        if r["keyword"] not in seen:
            seen.add(r["keyword"])
            unique_results.append(r)

    analysis_results = unique_results

    for r in unique_results[:100]:
        tree.insert("", "end", values=(
            r["keyword"],
            r["pc"],
            r["mobile"],
            r["competition"],
            r["issue_score"],
            r["profit_score"],
            r["final_score"],
            r["type"]
        ))

    status.config(text=f"완료: {len(unique_results)}개 분석 / API 오류 {error_count}개")


def show_titles():
    selected = tree.selection()

    if not selected:
        messagebox.showwarning("선택 필요", "키워드를 선택하세요.")
        return

    values = tree.item(selected[0], "values")
    keyword = values[0]

    search_titles, home_titles = make_titles(keyword)

    title_box.delete("1.0", tk.END)
    title_box.insert(tk.END, f"키워드: {keyword}\n\n")

    title_box.insert(tk.END, "[검색용 제목 3개]\n")
    for i, title in enumerate(search_titles, 1):
        title_box.insert(tk.END, f"{i}. {title}\n")

    title_box.insert(tk.END, "\n[홈판용 제목 3개]\n")
    for i, title in enumerate(home_titles, 1):
        title_box.insert(tk.END, f"{i}. {title}\n")


def make_writing_guide(keyword):
    if "환급" in keyword:
        return [
            "환급 대상이 누구인지 먼저 설명",
            "조회 방법과 확인 경로 정리",
            "신청 전 확인해야 할 조건 정리",
            "놓치기 쉬운 주의사항 설명"
        ]

    if "연금" in keyword:
        return [
            "연금 종류와 기본 개념 정리",
            "수령 조건과 방식 비교",
            "세금이나 공제와 연결되는 부분 설명",
            "독자가 확인해야 할 체크포인트 정리"
        ]

    if "보험" in keyword:
        return [
            "보장 범위와 차이 설명",
            "가입 유도 없이 정보형 비교로 작성",
            "비슷한 보험 용어 차이 정리",
            "주의해야 할 약관·조건 중심 설명"
        ]

    if "청약" in keyword:
        return [
            "청약 조건과 자격 기준 설명",
            "일정이나 신청 전 확인사항 정리",
            "무주택·소득·가점 기준 비교",
            "처음 보는 사람이 헷갈리는 부분 설명"
        ]

    if "대출" in keyword:
        return [
            "대출 종류와 조건을 정보형으로 설명",
            "승인 유도 표현 없이 기준만 정리",
            "금리·한도·상환 방식 차이 비교",
            "주의할 점과 확인사항 중심으로 작성"
        ]

    if "세금" in keyword or "세액" in keyword or "공제" in keyword:
        return [
            "공제 대상과 조건 설명",
            "연말정산 또는 세금 환급과 연결",
            "사례 중심으로 이해 쉽게 구성",
            "확정 표현 없이 확인사항 중심 작성"
        ]

    return [
        "이슈가 왜 생겼는지 배경 설명",
        "독자가 궁금해할 핵심 질문 정리",
        "기존 정보와 달라진 점 비교",
        "마지막에 확인해야 할 사항 정리"
    ]


def show_recommended_posts():
    if not analysis_results:
        messagebox.showwarning("분석 필요", "먼저 키워드 분석을 실행하세요.")
        return

    top_results = analysis_results[:5]

    title_box.delete("1.0", tk.END)
    title_box.insert(tk.END, "[오늘 작성 추천 글 TOP 5]\n\n")

    for idx, item in enumerate(top_results, 1):
        keyword = item["keyword"]
        search_titles, home_titles = make_titles(keyword)
        guides = make_writing_guide(keyword)

        title_box.insert(tk.END, "━━━━━━━━━━━━━━━━━━━━\n")
        title_box.insert(tk.END, f"{idx}. 추천 키워드: {keyword}\n")
        title_box.insert(
            tk.END,
            f"추천 이유: 이슈점수 {item['issue_score']} / 수익점수 {item['profit_score']} / 최종점수 {item['final_score']} / 유형 {item['type']}\n\n"
        )

        title_box.insert(tk.END, "[검색용 제목 3개]\n")
        for i, title in enumerate(search_titles, 1):
            title_box.insert(tk.END, f"{i}. {title}\n")

        title_box.insert(tk.END, "\n[홈판용 제목 3개]\n")
        for i, title in enumerate(home_titles, 1):
            title_box.insert(tk.END, f"{i}. {title}\n")

        title_box.insert(tk.END, "\n[글 작성 방향]\n")
        for i, guide in enumerate(guides, 1):
            title_box.insert(tk.END, f"{i}. {guide}\n")

        title_box.insert(tk.END, "\n")


root = tk.Tk()
root.title("v15 이슈 수익형 블로그 글 추천기")
root.geometry("1180x780")

title_label = tk.Label(
    root,
    text="v15 이슈 수익형 블로그 글 추천기",
    font=("맑은 고딕", 17, "bold")
)
title_label.pack(pady=10)

api_frame = tk.LabelFrame(root, text="네이버 검색광고 API")
api_frame.pack(fill="x", padx=15, pady=5)

tk.Label(api_frame, text="CUSTOMER_ID").grid(row=0, column=0, padx=5, pady=5)
customer_entry = tk.Entry(api_frame, width=25)
customer_entry.grid(row=0, column=1, padx=5, pady=5)

tk.Label(api_frame, text="API_KEY").grid(row=0, column=2, padx=5, pady=5)
api_entry = tk.Entry(api_frame, width=35, show="*")
api_entry.grid(row=0, column=3, padx=5, pady=5)

tk.Label(api_frame, text="SECRET_KEY").grid(row=0, column=4, padx=5, pady=5)
secret_entry = tk.Entry(api_frame, width=40, show="*")
secret_entry.grid(row=0, column=5, padx=5, pady=5)

button_frame = tk.Frame(root)
button_frame.pack(pady=10)

analyze_button = tk.Button(
    button_frame,
    text="오늘 이슈+수익 키워드 분석",
    command=run_analysis,
    width=28
)
analyze_button.pack(side="left", padx=5)

title_button = tk.Button(
    button_frame,
    text="선택 키워드 제목 생성",
    command=show_titles,
    width=22
)
title_button.pack(side="left", padx=5)

recommend_button = tk.Button(
    button_frame,
    text="오늘 TOP 5 추천 글 보기",
    command=show_recommended_posts,
    width=25
)
recommend_button.pack(side="left", padx=5)

columns = (
    "keyword",
    "pc",
    "mobile",
    "competition",
    "issue",
    "profit",
    "final",
    "type"
)

tree = ttk.Treeview(root, columns=columns, show="headings", height=19)

headers = {
    "keyword": "키워드",
    "pc": "PC",
    "mobile": "모바일",
    "competition": "경쟁도",
    "issue": "이슈점수",
    "profit": "수익점수",
    "final": "최종점수",
    "type": "유형"
}

for col in columns:
    tree.heading(col, text=headers[col])
    tree.column(col, width=120)

tree.column("keyword", width=320)
tree.column("type", width=130)

tree.pack(fill="both", expand=True, padx=15, pady=10)

title_box = tk.Text(root, height=12)
title_box.pack(fill="x", padx=15, pady=5)

status = tk.Label(root, text="대기 중")
status.pack(pady=5)

root.mainloop()

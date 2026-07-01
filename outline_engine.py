def make_writing_guide(keyword):
    if "환급" in keyword:
        return [
            "환급 대상이 누구인지 먼저 설명",
            "조회 방법과 확인 경로 정리",
            "신청 전 확인해야 할 조건 정리",
            "놓치기 쉬운 주의사항 설명",
        ]
    if "연금" in keyword:
        return [
            "연금 종류와 기본 개념 정리",
            "수령 조건과 방식 비교",
            "세금이나 공제와 연결되는 부분 설명",
            "독자가 확인해야 할 체크포인트 정리",
        ]
    if "보험" in keyword:
        return [
            "보장 범위와 차이 설명",
            "가입 유도 없이 정보형 비교로 작성",
            "비슷한 보험 용어 차이 정리",
            "주의해야 할 약관·조건 중심 설명",
        ]
    if "청약" in keyword:
        return [
            "청약 조건과 자격 기준 설명",
            "일정이나 신청 전 확인사항 정리",
            "무주택·소득·가점 기준 비교",
            "처음 보는 사람이 헷갈리는 부분 설명",
        ]
    if "대출" in keyword:
        return [
            "대출 종류와 조건을 정보형으로 설명",
            "승인 유도 표현 없이 기준만 정리",
            "금리·한도·상환 방식 차이 비교",
            "주의할 점과 확인사항 중심으로 작성",
        ]
    if "세금" in keyword or "세액" in keyword or "공제" in keyword:
        return [
            "공제 대상과 조건 설명",
            "연말정산 또는 세금 환급과 연결",
            "사례 중심으로 이해 쉽게 구성",
            "확정 표현 없이 확인사항 중심 작성",
        ]
    return [
        "이슈가 왜 생겼는지 배경 설명",
        "독자가 궁금해할 핵심 질문 정리",
        "기존 정보와 달라진 점 비교",
        "마지막에 확인해야 할 사항 정리",
    ]


def generate_outline(keyword):
    guide = make_writing_guide(keyword)
    return {
        "intro": f"'{keyword}'을(를) 찾아보게 되는 이유와 이 글에서 확인할 핵심 내용 요약",
        "sections": [f"{i+1}. {g}" for i, g in enumerate(guide)],
        "faq": [
            f"Q. {keyword} 관련해서 가장 많이 묻는 질문은 무엇인가요?",
            f"Q. {keyword} 대상이 아니라면 어떻게 해야 하나요?",
            f"Q. {keyword} 신청이나 확인에 기한이 있나요?",
        ],
        "image_suggestions": [
            f"{keyword} 관련 공식 안내/캡처 이미지",
            "체크리스트 또는 표 형태 인포그래픽",
            "본문 중간 핵심 요약 카드형 이미지",
        ],
        "tag_suggestions": [keyword.replace(" ", ""), "정리", "총정리", "확인방법"],
    }

from outline_engine import generate_full_draft


def build_prompt(keyword, outline, difficulty="보통"):
    sections = "\n".join(outline["sections"])
    faq = "\n".join(outline["faq"])

    caution = ""
    if difficulty == "어려움":
        caution = "- 세금/보험/대출/청약처럼 민감한 주제이니 확정적 표현을 특히 조심하고, 마지막에 전문가 상담 권장 문구를 넣을 것\n"

    return f"""너는 네이버 블로그·티스토리에 올릴 정보성 글을 작성하는 블로그 작가야.
아래 키워드와 개요를 참고해서, 실제로 복사해서 바로 발행할 수 있는 완성된 글을 작성해줘.

키워드: {keyword}

포함해야 할 소제목:
{sections}

포함해야 할 FAQ 질문(각 질문에 실제 답변까지 작성):
{faq}

작성 형식:
- 제목은 `# {keyword} 총정리` 형태로 맨 위에 한 줄
- 도입부 3~4문장으로 이 글에서 다룰 내용 요약
- 각 소제목은 `## 소제목` 형식으로 표시하고, 그 아래 300~400자 분량의 본문 문단 작성
- FAQ는 `## FAQ` 아래에 `**Q. 질문**` / `A. 답변` 형식으로 작성
- 마지막에 `## 마무리` 섹션으로 요약 및 확인 권장 문구
{caution}
- 확정적 표현(무조건, 100%, 보장 등)은 쓰지 말 것
- 가입/신청을 유도하는 문구 없이 정보 전달 중심으로 작성
- 전체 2000자 내외의 자연스러운 문체로 작성
"""


def write_draft(keyword, outline, api_key=None, difficulty="보통"):
    """
    api_key가 있으면 Gemini로 실제 글을 생성하고,
    없거나 호출이 실패하면 outline_engine의 템플릿 기반 완성 초안으로 대체한다.
    """
    fallback = generate_full_draft(keyword, difficulty)

    if not api_key:
        return fallback

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")
        prompt = build_prompt(keyword, outline, difficulty)
        resp = model.generate_content(prompt)
        if resp and resp.text and len(resp.text.strip()) > 50:
            return resp.text
        return fallback
    except Exception as e:
        return fallback + f"\n\n[Gemini 호출 실패로 템플릿 초안을 표시합니다: {e}]"

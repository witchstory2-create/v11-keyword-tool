def build_prompt(keyword, outline):
    sections = "\n".join(outline["sections"])
    faq = "\n".join(outline["faq"])
    return f"""티스토리 블로그 글을 작성해줘.
키워드: {keyword}
아래 개요를 참고해서 정보성 글로 자연스럽게 작성해줘.

소제목 구성:
{sections}

FAQ:
{faq}

작성 조건:
- 확정적 표현(무조건, 100% 등)은 쓰지 말 것
- 가입/신청 유도 문구 없이 정보 전달 중심으로 작성
- 2000자 내외로 작성
"""


def build_fallback_draft(keyword, outline):
    lines = [f"[{keyword}] 글 초안 뼈대 (API 키 없이 생성됨)\n", "도입: " + outline["intro"], "\n본문 구성:"]
    lines.extend(outline["sections"])
    lines.append("\nFAQ:")
    lines.extend(outline["faq"])
    return "\n".join(lines)


def write_draft(keyword, outline, api_key=None):
    """
    Google Gemini API 무료 티어 사용.
    - https://aistudio.google.com/apikey 에서 무료로 키 발급 (신용카드 불필요)
    - 결제를 별도로 활성화하지 않으면 과금되지 않음
    """
    if not api_key:
        return build_fallback_draft(keyword, outline)

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")  # 무료 티어에서 사용 가능한 모델
        prompt = build_prompt(keyword, outline)
        resp = model.generate_content(prompt)
        return resp.text
    except Exception as e:
        return build_fallback_draft(keyword, outline) + f"\n\n[Gemini 호출 실패: {e}]"

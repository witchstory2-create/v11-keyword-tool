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
    lines = [f"[{keyword}] 글 초안 뼈대\n", "도입: " + outline["intro"], "\n본문 구성:"]
    lines.extend(outline["sections"])
    lines.append("\nFAQ:")
    lines.extend(outline["faq"])
    return "\n".join(lines)


def write_draft(keyword, outline, api_key=None):
    if not api_key:
        return build_fallback_draft(keyword, outline)

    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        prompt = build_prompt(keyword, outline)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        return resp.choices[0].message.content
    except Exception as e:
        return build_fallback_draft(keyword, outline) + f"\n\n[GPT 호출 실패: {e}]"

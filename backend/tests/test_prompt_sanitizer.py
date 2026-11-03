def test_sanitize_prompt_replaces_pii_with_stable_placeholders():
    from app.prompt_sanitizer import sanitize_prompt

    original_prompt = (
        "候选人姓名：张伟，邮箱 zhangwei@example.com，"
        "手机 13812345678，身份证 110101199001011234，张伟有 5 年 Python 经验"
    )

    safe_prompt = sanitize_prompt(original_prompt)

    assert safe_prompt == (
        "候选人姓名：__PII_PERSON_001__，邮箱 __PII_EMAIL_001__，"
        "手机 __PII_PHONE_001__，身份证 __PII_ID_CARD_001__，"
        "__PII_PERSON_001__有 5 年 Python 经验"
    )


def test_sanitize_prompt_returns_a_new_string_without_changing_normal_text():
    from app.prompt_sanitizer import sanitize_prompt

    assert sanitize_prompt("候选人有 5 年 Python 经验") == "候选人有 5 年 Python 经验"

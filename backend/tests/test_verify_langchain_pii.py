def test_langchain_pii_demo_shows_all_four_strategies():
    from scripts.verify_langchain_pii import SAMPLE_TEXT, run_demo

    results = run_demo()

    assert results == {
        "original": SAMPLE_TEXT,
        "redact": "模型收到：候选人邮箱 [REDACTED_EMAIL]，有 5 年 Python 经验",
        "mask": "模型收到：候选人邮箱 zhangwei@****.com，有 5 年 Python 经验",
        "hash": "模型收到：候选人邮箱 <email_hash:36503c16>，有 5 年 Python 经验",
        "block": "PIIDetectionError: Detected 1 instance(s) of email in text content",
    }

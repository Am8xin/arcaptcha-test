from app.bots.capture import from_payload, from_url


def test_from_url_reads_query():
    assert from_url("https://x/api/challenge?challenge_id=abc") == "abc"


def test_from_url_ignores_unrelated():
    assert from_url("https://x/api/challenge?foo=1") is None


def test_from_payload_nested():
    assert from_payload({"data": {"challenge_id": "abc"}}) == "abc"


def test_from_payload_camel_case():
    assert from_payload({"challengeId": 123}) == "123"
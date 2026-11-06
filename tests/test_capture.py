from urllib.parse import parse_qs, urlparse


def from_url(url):
    query = parse_qs(urlparse(url).query)
    value = query.get("challenge_id", [None])[0]
    return str(value) if value is not None else None


def from_payload(payload):
    if not isinstance(payload, dict):
        return None

    for key in ("challenge_id", "challengeId"):
        if key in payload and payload[key] is not None:
            return str(payload[key])

    data = payload.get("data")
    if isinstance(data, dict):
        return from_payload(data)

    return None


def test_from_url_reads_query():
    assert from_url("https://x/api/challenge?challenge_id=abc") == "abc"


def test_from_url_ignores_unrelated():
    assert from_url("https://x/api/challenge?foo=1") is None


def test_from_payload_nested():
    assert from_payload({"data": {"challenge_id": "abc"}}) == "abc"


def test_from_payload_camel_case():
    assert from_payload({"challengeId": 123}) == "123"
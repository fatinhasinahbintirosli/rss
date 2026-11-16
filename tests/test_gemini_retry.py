import pytest

from video_checker import gemini as g


class R:
    def __init__(self, code, text="{}", headers=None):
        self.status_code, self.text, self.headers = code, text, headers or {}

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]}


class S:
    def __init__(self, codes):
        self.codes, self.calls = list(codes), 0

    def request(self, method, url, **kw):
        self.calls += 1
        c = self.codes.pop(0)
        return R(c, '{"error": {"details": [{"quotaMetric": "x/generate_requests_per_minute"}]}}' if c == 429 else "{}")


def client(codes, monkeypatch):
    monkeypatch.setattr(g.time, "sleep", lambda s: None)
    return g.GeminiClient("k", "m", session=S(codes))


def test_retries_then_succeeds(monkeypatch):
    c = client([429, 503, 200], monkeypatch)
    assert c.generate_json({"uri": "u"}, "p") == {"ok": 1} and c.s.calls == 3


def test_gives_up_with_clear_message(monkeypatch):
    c = client([429] * 4, monkeypatch)
    with pytest.raises(g.GeminiError, match="had kadar.*generate_requests_per_minute"):
        c.generate_json({"uri": "u"}, "p")


def test_daily_quota_not_retried(monkeypatch):
    c = client([], monkeypatch)
    c.s.request = lambda *a, **k: R(429, "Quota exceeded for metric GenerateRequestsPerDayPerProjectPerModel")
    with pytest.raises(g.GeminiError, match="HARIAN"):
        c.generate_json({"uri": "u"}, "p")


def test_spend_cap_not_retried(monkeypatch):
    c = client([], monkeypatch)
    c.s.request = lambda *a, **k: R(429, '{"error": {"message": "Your project has exceeded its monthly spending cap."}}')
    with pytest.raises(g.GeminiError, match="BULANAN"):
        c.generate_json({"uri": "u"}, "p")

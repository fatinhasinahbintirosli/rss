import json
import claude_writer as cw
import captions as cap


class Resp:
    def __init__(self, code, data):
        self.status_code, self._d, self.text = code, data, json.dumps(data)
    def json(self):
        return self._d


class Sess:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append((url, headers, json))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def ok(caps):
    return Resp(200, {"content": [{"type": "text", "text": "```json\n" + json.dumps({"captions": caps}) + "\n```"}]})


DESC = {"ringkasan": "adik cari ibu di sisi jenazah", "tersembunyi": ["ibu meninggal sbb sakit jantung"]}


def test_claude_used_with_description_and_bearer_for_proxy():
    s = Sess([ok(["Yg kesiannya, mak dia meninggal sbb"])])
    w = cw.ClaudeWriter("k", "https://api.magnetapi.org/v1", "claude-opus-5-5", session=s, log=lambda *_: None)
    gem_calls = []
    ask = w.wrap(lambda p: gem_calls.append(p) or {"captions": ["gem"]}, describe=lambda: DESC)
    out = ask(cap.build_prompt(3, "", []))
    assert out["captions"] == ["Yg kesiannya, mak dia meninggal sbb"] and not gem_calls
    url, h, body = s.calls[0]
    assert url == "https://api.magnetapi.org/v1/messages"
    assert h["authorization"] == "Bearer k" and body["model"] == "claude-opus-5-5"
    prompt = body["messages"][0]["content"]
    assert "sakit jantung" in prompt and not prompt.startswith("Tonton video ini") and "see more" in prompt


def test_official_anthropic_has_no_bearer():
    w = cw.ClaudeWriter("k", session=Sess([]))
    assert "authorization" not in w._headers() and w._headers()["x-api-key"] == "k"


def test_falls_back_to_gemini_on_error_and_stops_trying():
    s = Sess([Resp(402, {"error": "no credit"}), Resp(500, {})])
    w = cw.ClaudeWriter("k", "https://x.org", session=s, log=lambda *_: None)
    ask = w.wrap(lambda p: {"captions": ["gem"]}, describe=lambda: DESC)
    assert ask("Tonton video ini. a")["captions"] == ["gem"]
    assert ask("Tonton video ini. b")["captions"] == ["gem"]
    assert ask("Tonton video ini. c")["captions"] == ["gem"]
    assert len(s.calls) == 2          # selepas 2 gagal, Claude tidak dicuba lagi


def test_bad_description_goes_straight_to_gemini():
    s = Sess([])
    w = cw.ClaudeWriter("k", session=s, log=lambda *_: None)
    ask = w.wrap(lambda p: {"captions": ["gem"]}, describe=lambda: {})
    assert ask("x")["captions"] == ["gem"] and ask("y")["captions"] == ["gem"] and not s.calls


def test_from_env(monkeypatch):
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    assert cw.from_env() is None
    monkeypatch.setenv("CLAUDE_API_KEY", "k")
    monkeypatch.setenv("CLAUDE_BASE_URL", "https://api.magnetapi.org")
    w = cw.from_env()
    assert w.base == "https://api.magnetapi.org" and w.model == "claude-sonnet-5-5"

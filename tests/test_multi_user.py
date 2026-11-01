import json
import postolab_relay as relay
import fb_reels_rss as fbr


class R:
    def __init__(self, code, data):
        self.status_code, self._d, self.text = code, data, json.dumps(data)
    def json(self):
        return self._d


class S:
    def __init__(self, routes):
        self.routes, self.calls = routes, []
    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        user = (headers or {}).get("X-Postolab-User", "")
        for suffix, fn in self.routes.items():
            if url.endswith(suffix):
                return fn(user)
        return R(404, {})


def test_users_and_for_user_header():
    s = S({"/api/external/users": lambda u: R(200, {"users": [{"email": "a@x", "exists": True},
                                                               {"email": "b@x", "exists": False},
                                                               {"email": "c@x", "exists": True}]})})
    api = relay.Postolab("https://p", "k", session=s)
    out = relay.user_apis(api)
    assert [e for e, _ in out] == ["a@x", "c@x"]
    assert out[1][1].h["X-Postolab-User"] == "c@x" and out[1][1].h["Authorization"] == "Bearer k"
    assert "X-Postolab-User" not in s.calls[0][1]          # senarai pengguna dibaca tanpa bertindak


def test_falls_back_to_single_user_when_not_supported():
    for code in (403, 404):
        api = relay.Postolab("https://p", "k", session=S({"/api/external/users": lambda u, c=code: R(c, {})}))
        out = relay.user_apis(api, log=lambda *_: None)
        assert out == [("", api)]


def test_rss_sources_from_all_users(monkeypatch):
    remotes = {
        "a@x": {"settings": {"profiles": {"deli": {"enabled": True, "sources": ["https://fb.com/p1/reels/"]}}}},
        "c@x": {"settings": {"profiles": {"x": {"enabled": True, "sources": ["https://fb.com/p1/reels/", "https://fb.com/p2/reels/"]},
                                          "off": {"enabled": False, "sources": ["https://fb.com/p3/reels/"]}}}},
    }
    s = S({"/api/external/users": lambda u: R(200, {"users": [{"email": "a@x", "exists": True}, {"email": "c@x", "exists": True}]}),
           "/api/external/auto-reels": lambda u: R(200, remotes[u])})
    monkeypatch.setenv("POSTOLAB_API_KEY", "k")
    monkeypatch.setattr(relay, "_KeepAuthSession", lambda: s)
    assert fbr.postolab_sources() == ["https://fb.com/p1/reels/", "https://fb.com/p2/reels/"]

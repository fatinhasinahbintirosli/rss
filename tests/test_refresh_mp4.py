import types
import requests
import postolab_relay as relay


def test_expired_mp4_is_refreshed(monkeypatch, tmp_path):
    calls = []

    def fake_download(url, dest, session=None):
        calls.append(url)
        if url == "https://old/a.mp4":
            resp = types.SimpleNamespace(status_code=403)
            raise requests.HTTPError("403 Forbidden", response=resp)
        open(dest, "wb").write(b"x")
    monkeypatch.setattr(relay, "download", fake_download)
    item = {"mp4": "https://old/a.mp4", "url": "https://www.facebook.com/reel/1/"}
    relay.download_reel(item, str(tmp_path / "v.mp4"), log=lambda *_: None,
                        extract=lambda url: types.SimpleNamespace(mp4="https://new/a.mp4", error=""))
    assert calls == ["https://old/a.mp4", "https://new/a.mp4"] and item["mp4"] == "https://new/a.mp4"


def test_other_errors_not_retried(monkeypatch, tmp_path):
    def fake_download(url, dest, session=None):
        raise requests.HTTPError("500", response=types.SimpleNamespace(status_code=500))
    monkeypatch.setattr(relay, "download", fake_download)
    try:
        relay.download_reel({"mp4": "u", "url": "r"}, str(tmp_path / "v"), log=lambda *_: None,
                            extract=lambda url: (_ for _ in ()).throw(AssertionError("tidak patut dipanggil")))
        raise AssertionError("patut gagal")
    except requests.HTTPError:
        pass


def test_refresh_fails_cleanly(monkeypatch, tmp_path):
    monkeypatch.setattr(relay, "download", lambda u, d, session=None: (_ for _ in ()).throw(
        requests.HTTPError("410", response=types.SimpleNamespace(status_code=410))))
    try:
        relay.download_reel({"mp4": "u", "url": "r"}, str(tmp_path / "v"), log=lambda *_: None,
                            extract=lambda url: types.SimpleNamespace(mp4="", error="Cannot parse data"))
        raise AssertionError("patut gagal")
    except RuntimeError as e:
        assert "luput" in str(e)

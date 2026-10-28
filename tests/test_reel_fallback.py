import fb_reels_rss as f


def test_parse_reel_html_prefers_hd_and_unescapes():
    html = ('<meta property="og:description" content="Adik ni &amp; mak dia">'
            '<meta property="og:image" content="https://x/t.jpg">'
            '"browser_native_sd_url":"https:\\/\\/v.fbcdn.net\\/sd.mp4?a=1\\u00262",'
            '"browser_native_hd_url":"https:\\/\\/v.fbcdn.net\\/hd.mp4?a=1\\u00262"')
    got = f.parse_reel_html(html)
    assert got["mp4"] == "https://v.fbcdn.net/hd.mp4?a=1&2"
    assert got["caption"] == "Adik ni & mak dia" and got["thumb"] == "https://x/t.jpg"


def test_parse_reel_html_playable_and_message_text():
    got = f.parse_reel_html('"message":{"ranges":[],"text":"Katanya staff ni"} "playable_url":"https:\\/\\/a\\/b.mp4"')
    assert got["mp4"] == "https://a/b.mp4" and got["caption"] == "Katanya staff ni"


def test_parse_reel_html_none():
    assert f.parse_reel_html("<html>log in</html>")["mp4"] == ""


def test_netscape_cookies(tmp_path):
    p = tmp_path / "c.txt"
    p.write_text("# Netscape\n.facebook.com\tTRUE\t/\tTRUE\t1999999999\tc_user\t123\n#HttpOnly_.facebook.com\tTRUE\t/\tTRUE\t0\txs\tabc\n")
    ck = f._netscape_cookies(str(p))
    assert [c["name"] for c in ck] == ["c_user", "xs"] and ck[0]["expires"] == 1999999999 and "expires" not in ck[1]


def test_link_facebook_fallback(monkeypatch, tmp_path):
    import link_relay as lr
    monkeypatch.setattr(lr, "ensure_chromium", lambda: None)

    def fill(reels, proxy, cookiefile):
        reels[0].mp4, reels[0].caption = "https://v.fbcdn.net/hd.mp4", "Katanya"
        return 1
    monkeypatch.setattr(f, "browser_fill", fill)
    monkeypatch.setattr(lr, "download", lambda u, p: open(p, "wb").write(b"x"))
    monkeypatch.setattr(lr, "_has_video", lambda p: True)
    monkeypatch.setattr(lr, "ensure_facebook_mp4", lambda p: p)
    got = lr.facebook_browser_video("https://www.facebook.com/reel/1/", str(tmp_path))
    assert got["description"] == "Katanya" and got["path"].endswith("video_fb.mp4")
    assert lr.is_facebook("https://www.facebook.com/watch/?v=1") and not lr.is_facebook("https://youtu.be/x")
    monkeypatch.setattr(f, "browser_fill", lambda *a: 0)
    assert lr.facebook_browser_video("https://www.facebook.com/reel/1/", str(tmp_path)) is None


def test_mobile_and_generic():
    assert f.mobile_url("https://www.facebook.com/reel/12/") == "https://m.facebook.com/reel/12/"
    assert f.mobile_url("https://web.facebook.com/watch/?v=3") == "https://m.facebook.com/watch/?v=3"
    attr = '<div data-store="{&quot;src&quot;:&quot;https:\\/\\/video.fbcdn.net\\/v.mp4?x=1&quot;}">'
    assert f.parse_reel_html(attr)["mp4"] == "https://video.fbcdn.net/v.mp4?x=1"
    t = '"x":"https:\\/\\/video.fbcdn.net\\/seg.mp4?bytestart=0" "y":"https:\\/\\/video.fbcdn.net\\/full.mp4?a=1"'
    assert f.generic_mp4(t) == "https://video.fbcdn.net/full.mp4?a=1"
    assert f.parse_reel_html('<video src="https://v.fbcdn.net/a.mp4"></video>')["mp4"] == "https://v.fbcdn.net/a.mp4"


def test_extract_reel_retries_with_impersonate(monkeypatch):
    import sys, types
    calls = []

    class YDL:
        def __init__(self, o): self.o = o
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False):
            calls.append(self.o.get("impersonate"))
            if not self.o.get("impersonate"):
                raise Exception("ERROR: [facebook] 1: Cannot parse data; please report")
            return {"description": "Kapsyen", "formats": [{"url": "https://v/a.mp4", "ext": "mp4", "vcodec": "h264", "acodec": "aac", "height": 720}]}
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=YDL))
    monkeypatch.setattr(f, "set_impersonate", lambda o, t: o.__setitem__("impersonate", t) or True)
    r = f.extract_reel("https://www.facebook.com/reel/5/")
    assert r.mp4 == "https://v/a.mp4" and not r.error and calls == [None, "firefox"]

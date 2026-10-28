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

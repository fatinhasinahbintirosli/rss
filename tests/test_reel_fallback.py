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

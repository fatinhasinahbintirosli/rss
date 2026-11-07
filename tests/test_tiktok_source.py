import fb_reels_rss as f
import postolab_relay as pr


def test_tiktok_slug_and_normalize():
    assert f.page_slug("https://www.tiktok.com/@siakap.keli?lang=ms") == "tt_siakap_keli"
    assert f.normalize_reels_url("tiktok.com/@siakap.keli/") == ("https://www.tiktok.com/@siakap.keli", "tt_siakap_keli")
    # Facebook tidak berubah
    assert f.normalize_reels_url("https://www.facebook.com/penmerahdotcoms")[1] == "penmerahdotcoms"


def test_tiktok_video_links():
    hrefs = ["https://www.tiktok.com/@a.b/video/7430000000000000001?is_from_webapp=1",
             "https://www.tiktok.com/@a.b/video/7430000000000000001", "https://www.tiktok.com/@a.b",
             "https://www.tiktok.com/@a.b/video/7430000000000000002"]
    assert f.collect_video_links(hrefs, 10) == ["https://www.tiktok.com/@a.b/video/7430000000000000001",
                                               "https://www.tiktok.com/@a.b/video/7430000000000000002"]
    # pautan video Facebook masih sama
    assert f.canonical_video_url("https://www.facebook.com/x/videos/123456789/") == "https://www.facebook.com/watch/?v=123456789"


def test_rss_roundtrip_and_relay_source():
    r = f.Reel(id="7430000000000000001", url="https://www.tiktok.com/@a.b/video/7430000000000000001",
               caption="hai #fyp", mp4="https://v16.tiktokcdn.com/x.mp4", source_page="https://www.tiktok.com/@a.b",
               first_seen="Sat, 07 Nov 2026 10:00:00 GMT")
    items = pr.parse_feed(f.build_rss("tt_a_b", "https://www.tiktok.com/@a.b", [r]))
    assert items[0]["id"] == "7430000000000000001" and items[0]["source"] == "tt_a_b"
    assert pr.source_slug("https://www.tiktok.com/@a.b") == "tt_a_b"


def test_tiktok_download_uses_ytdlp(tmp_path):
    called = {}

    def fake(url, dest):
        called["url"] = url
        return {"description": "asal", "thumb": "t.jpg", "watermark_free": True}
    item = {"url": "https://www.tiktok.com/@a.b/video/7430000000000000001", "mp4": "https://v16.tiktokcdn.com/x.mp4"}
    pr.download_reel(item, str(tmp_path / "v.mp4"), log=lambda *_: None, tiktok=fake)
    assert called["url"] == item["url"] and item["caption"] == "asal" and item["thumb"] == "t.jpg"

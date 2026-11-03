import fb_reels_rss as f
import postolab_relay as relay


def test_group_urls_normalize_and_slug_match_relay():
    for u in ["https://www.facebook.com/groups/1821714008117947/media/videos",
              "facebook.com/groups/1821714008117947/", "https://web.facebook.com/groups/1821714008117947"]:
        url, slug = f.normalize_reels_url(u)
        assert url == "https://www.facebook.com/groups/1821714008117947/media/videos"
        assert slug == "g1821714008117947" == relay.source_slug(u)
    assert f.normalize_reels_url("https://www.facebook.com/penmerahdotcoms")[1] == "penmerahdotcoms"


def test_collect_group_video_links():
    hrefs = ["https://www.facebook.com/groups/1/posts/123/", "https://www.facebook.com/watch/?v=1111111111&ref=g",
             "https://www.facebook.com/PenMerah/videos/2222222222/", "https://www.facebook.com/reel/333333333/",
             "https://www.facebook.com/watch/?v=1111111111", "https://www.facebook.com/page/videos/"]
    assert f.collect_video_links(hrefs, 10) == ["https://www.facebook.com/watch/?v=1111111111",
                                                "https://www.facebook.com/watch/?v=2222222222",
                                                "https://www.facebook.com/reel/333333333/"]
    assert f.collect_video_links(hrefs, 1) == ["https://www.facebook.com/watch/?v=1111111111"]


def test_feed_with_group_video_and_reel():
    xml = """<rss><channel>
      <item><link>https://www.facebook.com/watch/?v=1111111111</link><category>g1821714008117947</category>
        <description></description><enclosure url="https://v/a.mp4" type="video/mp4"/></item>
      <item><link>https://www.facebook.com/reel/333333333/</link><category>penmerahdotcoms</category>
        <description></description><enclosure url="https://v/b.mp4" type="video/mp4"/></item>
    </channel></rss>"""
    items = relay.parse_feed(xml)
    assert [(i["id"], i["source"]) for i in items] == [("1111111111", "g1821714008117947"), ("333333333", "penmerahdotcoms")]

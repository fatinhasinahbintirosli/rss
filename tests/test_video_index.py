import pytest
import link_relay as lr


def test_split_video_index():
    assert lr.split_video_index("https://www.threads.com/@a/post/X#video=2") == ("https://www.threads.com/@a/post/X", 2)
    assert lr.split_video_index("https://youtu.be/x") == ("https://youtu.be/x", 1)


def test_pick_nth_message():
    assert lr.pick_nth(["a", "b"], 2) == "b"
    with pytest.raises(lr.DownloadError, match="cuma ada 2 video"):
        lr.pick_nth(["a", "b"], 3)


def test_page_video_list_one_per_item():
    html = ('{"video_versions":[{"type":101,"url":"https:\\/\\/cdn.x\\/v\\/A.mp4?q=1"},'
            '{"type":102,"url":"https:\\/\\/cdn.x\\/v\\/A.mp4?q=2"}]} ... '
            '{"video_versions":[{"type":101,"url":"https:\\/\\/cdn.x\\/v\\/B.mp4?q=1"}]}')
    vids = lr.page_video_list(html)
    assert [lr._mp4_key(v) for v in vids] == ["A.mp4", "B.mp4"]


def test_page_video_url_second(monkeypatch):
    html = ('<meta property="og:title" content="T">{"video_versions":[{"url":"https:\\/\\/cdn.x\\/A.mp4"}]}'
            '{"video_versions":[{"url":"https:\\/\\/cdn.x\\/B.mp4"}]}')

    class R:
        text = html

        def raise_for_status(self):
            pass

    class S:
        def get(self, *a, **k):
            return R()
    mp4, title, _ = lr._page_video_url("https://www.threads.com/@a/post/X/embed", S(), index=2)
    assert mp4 == "https://cdn.x/B.mp4" and title == "T"
    with pytest.raises(lr.DownloadError, match="video ke-3"):
        lr._page_video_url("u", S(), index=3)

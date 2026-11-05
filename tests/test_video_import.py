import link_relay as lr


class FakeApi:
    def __init__(self, items):
        self.items, self.saved, self.uploads = items, [], []

    def imports_remote(self):
        return self.items

    def save_import(self, item_id, **f):
        self.saved.append((item_id, f))

    def upload(self, path, content_type="video/mp4"):
        self.uploads.append(content_type)
        return f"https://media.tikt0k.me/external/{len(self.uploads)}"


def test_import_done(monkeypatch, tmp_path):
    monkeypatch.setattr(lr, "screenshot", lambda p, d=0: p)
    api = FakeApi([{"id": "a", "url": "https://www.tiktok.com/@x/video/1"}])
    fetch = lambda url, td, *a: {"path": str(tmp_path / "v.mp4"), "title": "T", "description": "D", "duration": 5}
    assert lr.run_imports(api, fetch, log=lambda *_: None) == 1
    assert api.saved[0] == ("a", {"status": "processing"})
    st = api.saved[1][1]
    assert st["status"] == "done" and st["video_url"].endswith("/2") and st["thumb_url"].endswith("/1")
    assert st["title"] == "T" and st["description"] == "D"


def test_import_error_is_friendly(monkeypatch):
    def boom(*a):
        raise lr.DownloadError("ERROR: This video is private")
    api = FakeApi([{"id": "b", "url": "https://youtu.be/x"}])
    lr.run_imports(api, boom, log=lambda *_: None)
    assert api.saved[1] == ("b", {"status": "error", "error": "Video ini peribadi atau perlu log masuk."})


def test_import_not_deployed():
    api = FakeApi(None)
    assert lr.run_imports(api, log=lambda *_: None) == 0 and not api.saved

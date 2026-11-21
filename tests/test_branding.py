import os
import shutil
import subprocess

import pytest

import branding
import video_text

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="tiada ffmpeg")


class Api:
    base, h = "http://x", {}

    def __init__(self):
        self.uploads = []
        self._brand_pages = {"1": {"name": "Trending Sekarang", "picture": ""},
                             "2": {"name": "Halaman Kedua Yang Namanya Sangat Panjang Sekali", "picture": ""}}

    def upload(self, path, content_type="video/mp4"):
        assert os.path.getsize(path) > 1000
        self.uploads.append(path)
        return f"https://media.tikt0k.me/external/{len(self.uploads)}.mp4"


@pytest.fixture
def clip(tmp_path, monkeypatch):
    src = str(tmp_path / "in.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=360x640:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", src], check=True)
    from PIL import Image
    pic = str(tmp_path / "dp.png")
    Image.new("RGB", (200, 200), (30, 90, 200)).save(pic)
    monkeypatch.setattr(branding, "_download_picture", lambda pid, url: pic)
    return src


def test_each_page_gets_own_video(clip):
    api = Api()
    posts = [{"pageId": "1", "message": "a"}, {"pageId": "2", "message": "b"}]
    targets = [{"page_id": "1", "page_name": "P1"}, {"page_id": "2", "page_name": "P2"}]
    url, info = branding.finalize(clip, posts, targets, api, brand=True, log=lambda *_: None)
    assert [p["videoUrl"] for p in posts] == ["https://media.tikt0k.me/external/1.mp4", "https://media.tikt0k.me/external/2.mp4"]
    assert url == posts[0]["videoUrl"] and info["branding"]["videos"] == 2


def test_text_skipped_when_it_must_go_top(clip):
    api = Api()
    posts = [{"pageId": "1", "message": "a"}]
    _, info = branding.finalize(clip, posts, [{"page_id": "1"}], api, brand=True, want_text=True,
                                overlay_data={"teks_video": "Gila Seram", "teks_sedia_ada": ["bawah"]}, caps=["x"],
                                log=lambda *_: None)
    assert "logo" in info["video_text"]["skip"] and posts[0]["videoUrl"]


def test_without_branding_one_video(clip):
    api = Api()
    posts = [{"pageId": "1", "message": "a"}, {"pageId": "2", "message": "b"}]
    url, _ = branding.finalize(clip, posts, [], api, brand=False, log=lambda *_: None)
    assert len(api.uploads) == 1 and "videoUrl" not in posts[0]


def test_failure_falls_back(clip, monkeypatch):
    api = Api()
    monkeypatch.setattr(branding, "compose", lambda *a: (_ for _ in ()).throw(RuntimeError("rosak")))
    posts = [{"pageId": "1", "message": "a"}]
    url, info = branding.finalize(clip, posts, [], api, brand=True, log=lambda *_: None)
    assert "rosak" in info["branding"]["error"] and "videoUrl" not in posts[0] and len(api.uploads) == 1


def test_brand_png_fits_long_name(tmp_path):
    from PIL import Image
    pic = str(tmp_path / "dp.png")
    Image.new("RGB", (200, 200), (200, 30, 30)).save(pic)
    x, y, w, h = branding.brand_png("Nama Page Yang Sangat Sangat Panjang Untuk Diuji", pic, 720, 1280, str(tmp_path / "b.png"))
    assert x + w <= 720 and y > 1280 * 0.05

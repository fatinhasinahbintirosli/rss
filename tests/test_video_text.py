import os
import shutil
import subprocess

import pytest

import captions as cap
import video_text as vt


def test_position_avoids_existing_text():
    assert vt.choose_position(set()) == "bawah"
    assert vt.choose_position({"bawah"}) == "atas"
    assert vt.choose_position({"tengah"}) == "bawah"
    assert vt.choose_position({"atas", "bawah"}) is None
    assert vt.plan({"teks_video": "Gila Seram", "teks_sedia_ada": ["atas", "bawah"]}, ["x"])["skip"]


def test_emoji_rules():
    assert vt.clean_emoji("😱😱😱😱 abc") == "😱😱😱"
    assert vt.clean_emoji("😂😢", "sedih") == "😢"          # tiada emoji ketawa untuk video sedih
    assert vt.clean_emoji("") == ""


def test_text_must_differ_from_caption():
    caps = ["Katanya driver tu hilang sbb...see-moo.re"]
    assert vt.plan({"teks_video": "Katanya driver tu hilang"}, caps).get("skip")
    p = vt.plan({"teks_video": "Mana Driver Dia? Gila Seram 😱", "emoji": "😱", "mood": "seram"}, caps)
    assert p["text"] == "Mana Driver Dia? Gila Seram" and p["emoji"] == "😱" and p["position"] == "bawah"


def test_manual_text_keeps_emoji():
    p = vt.plan({"teks_sedia_ada": ["bawah"]}, ["sama"], manual="Siapa Salah?  😡")
    assert p == {"text": "Siapa Salah?", "emoji": "😡", "position": "atas", "mood": "", "existing": ["bawah"]}


def test_overlay_requested_in_same_ai_call():
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return {"captions": ["Kejadian berpunca sbb", "Dpt info dorg ni rupanya", "Katanya staff ni mrh sbb"],
                "teks_video": "Tak Sangka Jadi Macam Ni", "emoji": "😳", "mood": "terkejut", "teks_sedia_ada": []}
    ov = {}
    caps, _ = cap.generate("v.mp4", 1, "", [], ask, overlay=ov)
    assert caps and len(prompts) == 1 and "teks_video" in prompts[0]
    assert ov["data"]["teks_video"] == "Tak Sangka Jadi Macam Ni"


@pytest.mark.skipif(not shutil.which("ffmpeg") or not __import__("importlib").util.find_spec("PIL"), reason="tiada ffmpeg/Pillow")
def test_burn_real_video(tmp_path):
    src = str(tmp_path / "in.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=360x640:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", src], check=True)
    out, info = vt.apply(src, {"teks_video": "Mana Driver Dia? Gila Seram", "emoji": "😱"}, ["x"], log=lambda *_: None)
    assert out != src and os.path.getsize(out) > 1000 and info["position"] == "bawah"
    assert vt.video_size(out) == (360, 640)

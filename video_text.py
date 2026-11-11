"""Tulisan atas video (macam "Mana Driver Dia? Gila Seram 😱"): kotak kuning, tulisan hitam tebal.

- Teks dijana oleh AI dalam permintaan caption yang SAMA (tiada panggilan AI tambahan), atau ditulis sendiri.
- AI juga laporkan di mana tulisan asal dalam video berada; tulisan kita dielak dari bertindih dengannya:
  lalai di bahagian bawah (tidak terlalu bawah), pindah ke atas jika bawah sudah ada tulisan,
  dan dilangkau jika atas dan bawah kedua-duanya sudah ada tulisan.
- Tulisan yang terlalu serupa dengan caption post tidak digunakan.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import unicodedata

PROMPT_EXTRA = """

TAMBAHAN — TULISAN ATAS VIDEO (dibakar dalam video, berasingan dari caption):
- "teks_video": SATU ayat pendek 2 hingga 7 perkataan, gaya tajuk yang buat orang terus tonton,
  contoh: "Mana Driver Dia? Gila Seram", "Tak Sangka Jadi Macam Ni", "Siapa Salah Sebenarnya?".
  Huruf besar di awal setiap perkataan. Ayat lengkap (soalan atau reaksi), BUKAN ayat tergantung.
  Mesti BERBEZA sepenuhnya dari caption di atas: perkataan lain, sudut lain, bukan ringkasan caption.
  Jangan reka fakta, jangan tuduh, jangan sebut bangsa/agama, jangan carut. Tiada hashtag atau pautan.
- "emoji": 0 hingga 3 emoji yang sesuai dengan mood video (cth. seram 😱, lucu 😂, comel 🥰, marah 😡,
  terkejut 😳). KOSONGKAN ("") jika video melibatkan kematian, kecederaan teruk, mangsa, musibah, agama
  atau kesedihan mendalam. Jangan guna emoji ketawa untuk video sedih, marah, bergaduh atau kemalangan.
- "mood": satu perkataan (seram, lucu, sedih, marah, terharu, terkejut, comel, tegang, biasa).
- "teks_sedia_ada": di mana TULISAN BESAR yang sudah dibakar dalam video (tajuk, sari kata, teks berita)
  berada sepanjang video: senarai antara "atas", "tengah", "bawah"; [] jika tiada.
  Abaikan watermark atau logo kecil di penjuru."""

JSON_LINE = 'Balas JSON sahaja: {"captions": ["...", "..."]}'
JSON_LINE_OVERLAY = ('Balas JSON sahaja: {"captions": ["...", "..."], "teks_video": "...", "emoji": "...", '
                     '"mood": "...", "teks_sedia_ada": []}')


def add_to_prompt(prompt: str) -> str:
    """Tambah permintaan tulisan atas video ke dalam prompt caption (permintaan AI yang sama)."""
    if JSON_LINE not in prompt:
        return prompt + PROMPT_EXTRA + "\n\n" + JSON_LINE_OVERLAY
    return prompt.replace(JSON_LINE, PROMPT_EXTRA.strip() + "\n\n" + JSON_LINE_OVERLAY)


def only_prompt(caption: str, source: str) -> str:
    """Untuk mod 'caption asal sahaja' (tiada permintaan caption AI): satu permintaan kecil."""
    return ("Tonton video ini. Tulis tulisan pendek untuk dibakar di atas video ini.\n"
            f"Caption post (tulisan atas video mesti BERBEZA dari ini): {caption[:600] or '(tiada)'}\n"
            f"Caption asal video (konteks sahaja): {source[:500] or '(tiada)'}\n"
            + PROMPT_EXTRA.replace("dari caption di atas", "dari caption post")
            + '\n\nBalas JSON sahaja: {"teks_video": "...", "emoji": "...", "mood": "...", "teks_sedia_ada": []}')


LAUGH = set("😂🤣😆😹😝😜😛🤪")
SOMBRE = {"sedih", "marah", "tegang", "tragis", "pilu", "duka"}
POSITIONS = ("atas", "tengah", "bawah")


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return (0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or 0x2300 <= cp <= 0x23FF
            or ch in "‍️⭐⭕‼⁉")


def clean_emoji(raw: str, mood: str = "") -> str:
    """Hanya aksara emoji, maksimum 3; buang emoji ketawa untuk mood sedih/marah/tegang."""
    out, n = [], 0
    for ch in str(raw or ""):
        if not _is_emoji(ch):
            continue
        if ch in "‍️":
            if out:
                out.append(ch)
            continue
        if ch in LAUGH and (mood or "").lower() in SOMBRE:
            continue
        if n >= 3:
            break
        out.append(ch)
        n += 1
    return "".join(out).strip("‍")


def clean_text(raw: str) -> str:
    t = "".join(ch for ch in str(raw or "") if not _is_emoji(ch))
    t = re.sub(r"https?://\S+|#\w+", "", t)
    t = re.sub(r"\s+", " ", t).strip(" \"'“”")
    words = t.split()
    return " ".join(words[:9])


def _words(s: str) -> set[str]:
    s = unicodedata.normalize("NFKD", s.lower())
    return {w for w in re.findall(r"[a-z0-9²]+", s) if len(w) > 2}


def too_similar(text: str, captions: list[str]) -> bool:
    """Tulisan atas video tidak boleh sama/serupa dengan caption post."""
    tw = _words(text)
    if not tw:
        return True
    for c in captions:
        cw = _words(c)
        low_t, low_c = text.lower().strip(), c.lower()
        if low_t and low_t in low_c:
            return True
        if cw and len(tw & cw) / len(tw) >= 0.6:
            return True
    return False


def existing_positions(raw) -> set[str]:
    if isinstance(raw, str):
        raw = re.split(r"[,/ ]+", raw)
    return {str(p).strip().lower() for p in (raw or []) if str(p).strip().lower() in POSITIONS}


def choose_position(existing: set[str]) -> str | None:
    """Bawah (lalai) → atas jika bawah sudah ada tulisan → None jika kedua-duanya penuh."""
    if "bawah" not in existing:
        return "bawah"
    if "atas" not in existing:
        return "atas"
    return None


def plan(data: dict | None, captions: list[str], manual: str = "") -> dict:
    """Pulangkan {text, emoji, position, mood} atau {skip: sebab}."""
    data = data if isinstance(data, dict) else {}
    mood = str(data.get("mood") or "").strip().lower()
    existing = existing_positions(data.get("teks_sedia_ada"))
    pos = choose_position(existing)
    if manual.strip():
        # Tulisan sendiri: guna seperti ditulis; emoji dalam teks dilukis berwarna di baris akhir
        emoji = clean_emoji(manual)
        text = re.sub(r"\s+", " ", "".join(ch for ch in manual if not _is_emoji(ch))).strip()[:120]
        if not text:
            return {"skip": "tulisan sendiri kosong"}
    else:
        text = clean_text(data.get("teks_video"))
        emoji = clean_emoji(data.get("emoji"), mood)
        if not text:
            return {"skip": "AI tidak beri tulisan atas video"}
        if too_similar(text, captions):
            return {"skip": f"tulisan terlalu serupa dengan caption ({text})"}
    if pos is None:
        return {"skip": "video sudah ada tulisan di atas dan di bawah", "text": text}
    return {"text": text, "emoji": emoji, "position": pos, "mood": mood, "existing": sorted(existing)}


# ------------------------------------------------------------------ lukis
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]
EMOJI_FONTS = ["/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", "/usr/share/fonts/noto/NotoColorEmoji.ttf"]
YELLOW = (255, 230, 0, 255)
BLACK = (0, 0, 0, 255)


def _font(size: int):
    from PIL import ImageFont
    for f in [os.environ.get("VIDEO_TEXT_FONT", "")] + FONT_CANDIDATES:
        if f and os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size=size)


def _emoji_images(emoji: str, height: int) -> list:
    """Setiap emoji sebagai gambar berwarna setinggi `height` (Noto Color Emoji hanya saiz 109)."""
    from PIL import Image, ImageDraw, ImageFont
    path = next((f for f in [os.environ.get("VIDEO_TEXT_EMOJI_FONT", "")] + EMOJI_FONTS if f and os.path.exists(f)), "")
    if not path or not emoji:
        return []
    font = ImageFont.truetype(path, 109)
    out = []
    for g in re.findall(r".️?(?:‍.️?)*", emoji):
        img = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((10, 10), g, font=font, embedded_color=True)
        box = img.getbbox()
        if not box:
            continue
        img = img.crop(box)
        out.append(img.resize((max(1, round(img.width * height / img.height)), height)))
    return out


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if cur and draw.textlength(t, font=font) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def _balanced(draw, text: str, font, max_w: int) -> list[str]:
    """Bilangan baris sama seperti biasa, tetapi panjang baris seimbang (elak satu perkataan tergantung).
    Jika ada '?' / '!' / ',' di tengah, pecah di situ dahulu (cth. "Mana Driver Dia?" / "Gila Seram")."""
    parts = [p.strip() for p in re.split(r"(?<=[?!,])\s+", text) if p.strip()]
    if 1 < len(parts) <= 3 and all(draw.textlength(p, font=font) <= max_w for p in parts):
        return parts
    lines = _wrap(draw, text, font, max_w)
    w = max_w
    while len(lines) > 1 and w > 40:
        test = _wrap(draw, text, font, w - 8)
        if len(test) > len(lines):
            break
        w -= 8
        lines = test
    return lines


def render_png(text: str, emoji: str, width: int, out: str) -> tuple[int, int]:
    """Lukis tulisan (setiap baris dalam kotak kuning sendiri, emoji di baris akhir). Pulangkan (lebar, tinggi)."""
    from PIL import Image, ImageDraw
    max_w = int(width * 0.86)
    size = max(18, int(width * 0.068))
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    while True:
        font = _font(size)
        lines = _balanced(probe, text, font, max_w - int(size * 0.6))
        if len(lines) <= 3 or size <= 18:
            break
        size = int(size * 0.88)
    pad_x, pad_y, gap = int(size * 0.3), int(size * 0.14), int(size * 0.08)
    asc, desc = font.getmetrics()
    line_h = asc + desc
    rows = []                                   # (lebar kotak, lukis(draw, canvas, x, y))
    for ln in lines:
        rows.append(("text", ln, int(probe.textlength(ln, font=font)) + 2 * pad_x))
    emos = _emoji_images(emoji, int(line_h * 0.95))
    if emos:
        rows.append(("emoji", emos, sum(e.width for e in emos) + int(size * 0.15) * (len(emos) - 1) + 2 * pad_x))
    W = max(r[2] for r in rows)
    H = len(rows) * (line_h + 2 * pad_y) + (len(rows) - 1) * gap
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    y = 0
    for kind, val, w in rows:
        x = (W - w) // 2
        draw.rectangle([x, y, x + w, y + line_h + 2 * pad_y], fill=YELLOW)
        if kind == "text":
            draw.text((x + pad_x, y + pad_y), val, font=font, fill=BLACK)
        else:
            ex = x + pad_x
            for e in val:
                canvas.alpha_composite(e, (ex, y + pad_y + (line_h - e.height) // 2))
                ex += e.width + int(size * 0.15)
        y += line_h + 2 * pad_y + gap
    canvas.save(out)
    return W, H


def video_size(path: str) -> tuple[int, int]:
    """Saiz paparan sebenar (ambil kira putaran video telefon)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height:stream_side_data=rotation:stream_tags=rotate", "-of", "json", path],
                       capture_output=True, text=True, timeout=60)
    s = (json.loads(r.stdout or "{}").get("streams") or [{}])[0]
    w, h = int(s.get("width") or 0), int(s.get("height") or 0)
    rot = str((s.get("tags") or {}).get("rotate") or "")
    for sd in s.get("side_data_list") or []:
        rot = str(sd.get("rotation", rot))
    if rot.strip("-") in ("90", "270"):
        w, h = h, w
    return w, h


def overlay_y(position: str, video_h: int, box_h: int) -> int:
    """Bawah: tengah kotak pada ~72% tinggi (tidak terlalu bawah, elak butang Facebook). Atas: ~16%."""
    centre = 0.72 if position == "bawah" else 0.16 if position == "atas" else 0.5
    return max(0, min(video_h - box_h, int(video_h * centre - box_h / 2)))


def burn(path: str, text: str, emoji: str = "", position: str = "bawah") -> str:
    """Bakar tulisan ke dalam video. Pulangkan laluan video baharu."""
    w, h = video_size(path)
    if not w or not h:
        raise RuntimeError("saiz video tidak diketahui")
    png = os.path.splitext(path)[0] + "_text.png"
    bw, bh = render_png(text, emoji, w, png)
    x, y = max(0, (w - bw) // 2), overlay_y(position, h, bh)
    out = os.path.splitext(path)[0] + "_text.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-i", png, "-filter_complex",
           f"[0:v][1:v]overlay={x}:{y}:format=auto,format=yuv420p[v]", "-map", "[v]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "copy", "-movflags", "+faststart", out]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 1000:
        raise RuntimeError(f"ffmpeg gagal: {(r.stderr or '')[-300:]}")
    return out


def apply(path: str, data: dict | None, captions: list[str], manual: str = "", log=print) -> tuple[str, dict]:
    """Rancang + bakar. Tidak pernah menggagalkan post: jika ada masalah, video asal digunakan."""
    p = plan(data, captions, manual)
    if p.get("skip"):
        log(f"   tulisan atas video dilangkau: {p['skip']}")
        return path, p
    try:
        new = burn(path, p["text"], p.get("emoji", ""), p["position"])
        log(f"   tulisan atas video ({p['position']}): {p['text']} {p.get('emoji', '')}".rstrip())
        return new, p
    except Exception as e:  # noqa: BLE001
        log(f"   tulisan atas video gagal ({str(e)[:150]}) → video asal digunakan")
        return path, {**p, "skip": f"gagal: {str(e)[:150]}"}

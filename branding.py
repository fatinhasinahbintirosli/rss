"""Logo + nama page di kiri atas video (berlainan untuk setiap page).

- Logo = gambar profil page Facebook, dipotong bulat dengan gelang putih.
- Nama page di sebelah kanan logo: tulisan putih tebal dengan bayang.
- Diletak ~8.5% dari atas video (tidak terlalu atas: elak butang/nama akaun yang Facebook papar di atas).
- Setiap page dapat fail video sendiri. Tulisan atas video (kotak kuning) dibakar dalam encode yang SAMA.
- Jika tulisan atas video terpaksa diletak di ATAS (video sudah ada tulisan di bawah), tulisan kuning
  dilangkau supaya tidak berlanggar dengan logo.
- Tidak pernah menggagalkan post: jika ada masalah, video tanpa logo digunakan.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import time

import requests

import video_text

LOGO_W = 0.13        # saiz logo: 13% lebar video
LEFT = 0.04          # jarak dari kiri
TOP = 0.085          # jarak dari atas (8.5% tinggi video)
RING = (255, 255, 255, 255)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CACHE = os.path.join(tempfile.gettempdir(), "postolab_brand")


# ------------------------------------------------------------------ data page
def page_info(api, log=print) -> dict:
    """{page_id: {"name", "picture"}} sekali setiap larian (disimpan pada objek api)."""
    if getattr(api, "_brand_pages", None) is not None:
        return api._brand_pages
    info = {}
    try:
        r = api.s.get(f"{api.base}/api/external/page-pictures", headers=api.h, timeout=90)
        if r.status_code == 200:
            for p in r.json().get("pages") or []:
                info[str(p.get("page_id"))] = {"name": p.get("page_name") or "", "picture": p.get("picture") or ""}
        elif r.status_code != 404:
            log(f"   logo page: Postolab {r.status_code}")
    except Exception as e:  # noqa: BLE001
        log(f"   logo page: gagal ambil gambar profil ({str(e)[:120]})")
    api._brand_pages = info
    return info


def _download_picture(page_id: str, url: str) -> str:
    """Muat turun gambar profil page (cache setiap larian). Sandaran: pautan awam Graph API."""
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, f"{page_id}.img")
    if os.path.exists(out) and os.path.getsize(out) > 500:
        return out
    for u in [url, f"https://graph.facebook.com/{page_id}/picture?width=480&height=480"]:
        if not u:
            continue
        try:
            r = requests.get(u, headers={"User-Agent": UA}, timeout=30)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") and len(r.content) > 500:
                with open(out, "wb") as f:
                    f.write(r.content)
                return out
        except requests.RequestException:
            continue
    return ""


# ------------------------------------------------------------------ lukis
def circle_logo(path: str, size: int):
    """Gambar profil → bulatan dengan gelang putih (PNG lutsinar)."""
    from PIL import Image, ImageDraw
    big = size * 3                                    # lukis besar kemudian kecilkan: tepi lebih licin
    img = Image.open(path).convert("RGBA")
    s = min(img.size)
    img = img.crop(((img.width - s) // 2, (img.height - s) // 2, (img.width + s) // 2, (img.height + s) // 2))
    img = img.resize((big, big), Image.LANCZOS)
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, big - 1, big - 1], fill=255)
    out = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    ring = max(3, big // 25)
    ImageDraw.Draw(out).ellipse([ring // 2, ring // 2, big - 1 - ring // 2, big - 1 - ring // 2], outline=RING, width=ring)
    return out.resize((size, size), Image.LANCZOS)


def brand_png(name: str, picture: str, video_w: int, video_h: int, out: str) -> tuple[int, int, int, int]:
    """Lukis logo + nama page. Pulangkan (x, y, lebar, tinggi) untuk overlay."""
    from PIL import Image, ImageDraw, ImageFilter
    L = max(40, int(video_w * LOGO_W))
    x0, y0 = int(video_w * LEFT), int(video_h * TOP)
    logo = circle_logo(picture, L) if picture else None
    gap = int(L * 0.18) if logo else 0
    max_text_w = video_w - x0 - (L + gap if logo else 0) - int(video_w * 0.04)
    size = int(L * 0.36)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    name = (name or "").strip()
    while name and size > 14 and probe.textlength(name, font=video_text._font(size)) > max_text_w:
        size -= 2
    font = video_text._font(size)
    tw = int(probe.textlength(name, font=font)) if name else 0
    W = (L if logo else 0) + (gap + tw + 12 if name else 0)
    H = max(L if logo else 0, size + 16)
    if W <= 0:
        raise RuntimeError("tiada logo dan tiada nama page")
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if logo:
        canvas.alpha_composite(logo, (0, (H - L) // 2))
    if name:
        tx = (L + gap) if logo else 0
        bb = probe.textbbox((0, 0), name, font=font)
        ty = (H - (bb[3] - bb[1])) // 2 - bb[1]
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).text((tx + 2, ty + 3), name, font=font, fill=(0, 0, 0, 200))
        canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(3)))
        ImageDraw.Draw(canvas).text((tx, ty), name, font=font, fill=(255, 255, 255, 255),
                                    stroke_width=max(1, size // 18), stroke_fill=(0, 0, 0, 170))
    canvas.save(out)
    return x0, y0 - max(0, (H - L) // 2), W, H


def compose(path: str, overlays: list[tuple[str, int, int]], out: str) -> str:
    """Satu encode: tampal beberapa PNG pada video."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", path]
    for png, _, _ in overlays:
        cmd += ["-i", png]
    chain, last = [], "[0:v]"
    for i, (_, x, y) in enumerate(overlays, 1):
        tag = f"[v{i}]"
        chain.append(f"{last}[{i}:v]overlay={x}:{y}:format=auto{tag}")
        last = tag
    chain.append(f"{last}format=yuv420p[v]")
    cmd += ["-filter_complex", ";".join(chain), "-map", "[v]", "-map", "0:a?", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "20", "-c:a", "copy", "-movflags", "+faststart", out]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 1000:
        raise RuntimeError(f"ffmpeg gagal: {(r.stderr or '')[-300:]}")
    return out


# ------------------------------------------------------------------ aliran utama
def finalize(path: str, posts: list[dict], targets: list[dict], api, *, brand: bool = False,
             overlay_data: dict | None = None, want_text: bool = False, caps: list[str] | None = None,
             manual: str = "", log=print, max_seconds: int = 900) -> tuple[str, dict]:
    """Sediakan video untuk upload. Pulangkan (video_url utama, info).

    Tanpa logo: satu video untuk semua page (tulisan atas video jika diminta).
    Dengan logo: satu video untuk SETIAP page; posts[i]["videoUrl"] diisi dengan video page itu.
    """
    info: dict = {}
    if not brand:
        upload_path = path
        if want_text:
            upload_path, info["video_text"] = video_text.apply(path, overlay_data, caps or [], manual=manual, log=log)
        return api.upload(upload_path), info

    started = time.time()
    try:
        w, h = video_text.video_size(path)
        if not w or not h:
            raise RuntimeError("saiz video tidak diketahui")
        # Tulisan kuning: dilukis sekali, dikongsi semua page
        text_ov = None
        if want_text:
            p = video_text.plan(overlay_data, caps or [], manual)
            info["video_text"] = p
            if p.get("skip"):
                log(f"   tulisan atas video dilangkau: {p['skip']}")
            elif p["position"] == "atas":
                info["video_text"] = {**p, "skip": "tulisan perlu di atas tetapi bertembung dengan logo"}
                log("   tulisan atas video dilangkau: video sudah ada tulisan di bawah, dan bahagian atas untuk logo")
            else:
                png = os.path.splitext(path)[0] + "_text.png"
                bw, bh = video_text.render_png(p["text"], p.get("emoji", ""), w, png)
                text_ov = (png, max(0, (w - bw) // 2), video_text.overlay_y(p["position"], h, bh))
                log(f"   tulisan atas video (bawah): {p['text']} {p.get('emoji', '')}".rstrip())
        pinfo = page_info(api, log)
        names = {str(t["page_id"]): t.get("page_name") or "" for t in targets}
        urls: dict[str, str] = {}
        made = 0
        for post in posts:
            pid = str(post["pageId"])
            if time.time() - started > max_seconds:
                raise TimeoutError(f"masa tamat selepas {made} page")
            meta = pinfo.get(pid, {})
            name = meta.get("name") or names.get(pid, "")
            pic = _download_picture(pid, meta.get("picture", ""))
            key = hashlib.md5(f"{name}|{pic}".encode()).hexdigest()[:10]
            if key not in urls:                         # page dengan nama & gambar sama → kongsi video
                bpng = os.path.splitext(path)[0] + f"_brand_{pid}.png"
                x, y, _, _ = brand_png(name, pic, w, h, bpng)
                out = os.path.splitext(path)[0] + f"_brand_{pid}.mp4"
                compose(path, [o for o in [text_ov, (bpng, x, y)] if o], out)
                urls[key] = api.upload(out)
                os.remove(out)
                made += 1
            post["videoUrl"] = urls[key]
        info["branding"] = {"pages": len(posts), "videos": made, "seconds": round(time.time() - started)}
        log(f"   logo & nama page: {made} video untuk {len(posts)} page ({round(time.time() - started)} saat)")
        return posts[0]["videoUrl"], info
    except Exception as e:  # noqa: BLE001
        log(f"   logo & nama page gagal ({str(e)[:150]}) → video tanpa logo digunakan")
        for post in posts:
            post.pop("videoUrl", None)
        info["branding"] = {"error": str(e)[:200]}
        upload_path = path
        if want_text:
            upload_path, info["video_text"] = video_text.apply(path, overlay_data, caps or [], manual=manual, log=log)
        return api.upload(upload_path), info

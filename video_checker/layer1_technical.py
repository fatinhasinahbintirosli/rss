"""Layer 1: technical checks against Facebook upload limits.

Limits (Meta Video API / Reels Publishing API docs, checked Oct 2026):
  Reels:      3-90 s, 9:16, min 540x960 (1080x1920 recommended), H.264/H.265 (VP9/AV1 accepted),
              AAC 48 kHz recommended, <= 25 Mbps, API limit 30 reels per Page per 24 h.
  Page video: up to 10 GB and 4 hours, MP4/MOV recommended.
"""

from __future__ import annotations

import os

from . import media
from .models import BLOCK, INFO, WARN, Finding, LayerResult

GB = 1024 ** 3

LIMITS = {
    "reel": {"min_dur": 3.0, "max_dur": 90.0, "max_bytes": 1 * GB, "min_w": 540, "min_h": 960,
             "max_bitrate": 25_000_000},
    "video": {"min_dur": 1.0, "max_dur": 4 * 3600.0, "max_bytes": 10 * GB, "min_w": 0, "min_h": 0,
              "max_bitrate": None},
}

GOOD_VIDEO_CODECS = {"h264", "hevc", "vp9", "av1"}
GOOD_CONTAINERS = ("mov", "mp4", "m4a", "3gp", "3g2", "mj2")   # ffprobe reports "mov,mp4,m4a,3gp,3g2,mj2"


def _fps(stream: dict) -> float | None:
    for key in ("avg_frame_rate", "r_frame_rate"):
        v = stream.get(key) or ""
        if "/" in v:
            n, d = v.split("/", 1)
            try:
                if float(d):
                    return float(n) / float(d)
            except ValueError:
                pass
    return None


def run(path: str, target: str = "reel") -> LayerResult:
    lr = LayerResult(layer=1, name="Teknikal")
    lim = LIMITS.get(target, LIMITS["video"])

    if not os.path.exists(path):
        lr.status = "error"
        lr.findings.append(Finding(1, BLOCK, "file_missing", "Fail video tidak dijumpai."))
        return lr

    try:
        info = media.probe(path)
    except media.MediaError as e:
        lr.status = "error"
        lr.findings.append(Finding(1, BLOCK, "unreadable", "Video rosak atau format tidak dikenali.",
                                   evidence=str(e)[:300], fix="Eksport semula video sebagai MP4 (H.264 + AAC)."))
        return lr

    fmt = info.get("format", {})
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)

    size = int(fmt.get("size") or os.path.getsize(path))
    duration = float(fmt.get("duration") or (v or {}).get("duration") or 0)
    bitrate = int(fmt.get("bit_rate") or 0)

    data = {"size_bytes": size, "duration": duration, "bit_rate": bitrate,
            "format": fmt.get("format_name"), "has_audio": a is not None}
    lr.data = data

    if v is None:
        lr.findings.append(Finding(1, BLOCK, "no_video_stream", "Fail ini tiada trek video."))
        return lr

    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    # Phone videos often store rotation as metadata; swap so we judge what the viewer sees.
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = abs(int(float(sd["rotation"])))
    rot = rot or abs(int(float((v.get("tags") or {}).get("rotate", 0) or 0)))
    if rot in (90, 270):
        w, h = h, w
    fps = _fps(v)
    data.update({"width": w, "height": h, "video_codec": v.get("codec_name"), "fps": fps,
                 "audio_codec": (a or {}).get("codec_name"),
                 "audio_sample_rate": int((a or {}).get("sample_rate") or 0) or None})

    container = (fmt.get("format_name") or "").split(",")
    if not any(c in GOOD_CONTAINERS for c in container):
        lr.findings.append(Finding(1, WARN if target == "video" else BLOCK, "container",
                                   f"Format fail '{fmt.get('format_name')}' tidak disyorkan.",
                                   fix="Tukar ke MP4."))

    if v.get("codec_name") not in GOOD_VIDEO_CODECS:
        lr.findings.append(Finding(1, BLOCK if target == "reel" else WARN, "video_codec",
                                   f"Codec video '{v.get('codec_name')}' mungkin ditolak.",
                                   fix="Eksport semula dengan H.264."))

    if duration < lim["min_dur"]:
        lr.findings.append(Finding(1, BLOCK, "too_short",
                                   f"Video terlalu pendek ({duration:.1f}s). Minimum {lim['min_dur']:.0f}s."))
    if duration > lim["max_dur"]:
        lr.findings.append(Finding(1, BLOCK, "too_long",
                                   f"Video terlalu panjang ({duration:.0f}s). Maksimum {lim['max_dur']:.0f}s"
                                   + (" untuk Reels." if target == "reel" else "."),
                                   fix="Potong video" + (" atau upload sebagai video biasa." if target == "reel" else ".")))
    if size > lim["max_bytes"]:
        lr.findings.append(Finding(1, BLOCK, "too_big",
                                   f"Fail terlalu besar ({size / GB:.2f} GB). Maksimum {lim['max_bytes'] / GB:.0f} GB.",
                                   fix="Kecilkan bitrate atau resolusi."))

    if target == "reel":
        if w < lim["min_w"] or h < lim["min_h"]:
            lr.findings.append(Finding(1, BLOCK, "low_resolution",
                                       f"Resolusi {w}x{h} terlalu rendah untuk Reels (min 540x960).",
                                       fix="Guna 1080x1920."))
        if w and h:
            ratio = w / h
            if abs(ratio - 9 / 16) > 0.03:
                sev = WARN if ratio < 1 else BLOCK
                lr.findings.append(Finding(1, sev, "aspect_ratio",
                                           f"Nisbah {w}x{h} bukan 9:16. Reels perlu video menegak.",
                                           fix="Crop/pad ke 1080x1920."))
        if lim["max_bitrate"] and bitrate > lim["max_bitrate"]:
            lr.findings.append(Finding(1, WARN, "bitrate",
                                       f"Bitrate {bitrate / 1e6:.1f} Mbps melebihi 25 Mbps.",
                                       fix="Eksport semula pada bitrate lebih rendah."))
        if fps and fps > 60.5:
            lr.findings.append(Finding(1, WARN, "fps", f"Frame rate {fps:.0f} fps melebihi 60 fps."))
        elif fps and fps < 23:
            lr.findings.append(Finding(1, WARN, "fps_low", f"Frame rate rendah ({fps:.0f} fps). Disyorkan 24-60 fps."))

    if a is None:
        lr.findings.append(Finding(1, INFO, "no_audio", "Video tiada audio."))
    else:
        if a.get("codec_name") != "aac":
            lr.findings.append(Finding(1, WARN, "audio_codec",
                                       f"Codec audio '{a.get('codec_name')}'. AAC disyorkan.",
                                       fix="Eksport audio sebagai AAC 48 kHz."))
        sr = data.get("audio_sample_rate")
        if sr and sr not in (44100, 48000):
            lr.findings.append(Finding(1, INFO, "audio_rate", f"Sample rate audio {sr} Hz. 48 kHz disyorkan."))

    if "mov" in container or "mp4" in container:
        try:
            boxes = media.top_level_boxes(path)
            if "moov" in boxes and "mdat" in boxes and boxes.index("moov") > boxes.index("mdat"):
                lr.findings.append(Finding(1, INFO, "faststart",
                                           "Metadata 'moov' di hujung fail (tiada faststart).",
                                           fix="ffmpeg -i in.mp4 -c copy -movflags +faststart out.mp4"))
        except OSError:
            pass

    return lr

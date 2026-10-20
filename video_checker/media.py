"""ffmpeg / ffprobe helpers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class MediaError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as e:
        raise MediaError(f"Program tidak dijumpai: {cmd[0]} (pasang ffmpeg)") from e
    except subprocess.TimeoutExpired as e:
        raise MediaError(f"{cmd[0]} mengambil masa terlalu lama") from e


def probe(path: str) -> dict:
    r = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], 120)
    if r.returncode != 0:
        raise MediaError(r.stderr.decode(errors="replace").strip() or "ffprobe gagal")
    return json.loads(r.stdout or b"{}")


@dataclass
class Frame:
    path: str
    t: float   # seconds from start


def extract_frames(path: str, out_dir: str, duration: float, interval: float, max_frames: int,
                   width: int = 768) -> list[Frame]:
    """Sample one frame every `interval` seconds (widened so we never exceed max_frames)."""
    os.makedirs(out_dir, exist_ok=True)
    if duration <= 0:
        duration = interval
    if duration / interval > max_frames:
        interval = duration / max_frames
    frames: list[Frame] = []
    # Start a little after 0 to skip black first frames, always include near-end.
    t = min(0.5, duration / 2)
    times = []
    while t < duration and len(times) < max_frames:
        times.append(t)
        t += interval
    for i, ts in enumerate(times):
        out = str(Path(out_dir) / f"frame_{i:04d}.jpg")
        r = _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{ts:.3f}", "-i", path, "-frames:v", "1",
                  "-vf", f"scale='min({width},iw)':-2", "-q:v", "4", out], 120)
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
            frames.append(Frame(out, ts))
    return frames


def extract_audio_clip(path: str, out_path: str, start: float, seconds: float) -> bool:
    r = _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", path,
              "-vn", "-ac", "1", "-ar", "44100", "-b:a", "128k", out_path], 120)
    return r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1000


def extract_audio_wav(path: str, out_path: str) -> bool:
    """16 kHz mono wav for speech recognition."""
    r = _run(["ffmpeg", "-v", "error", "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000", out_path], 900)
    return r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1000


def mean_volume_db(path: str, start: float, seconds: float) -> float | None:
    r = _run(["ffmpeg", "-v", "info", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", path, "-vn",
              "-af", "volumedetect", "-f", "null", "-"], 120)
    for line in r.stderr.decode(errors="replace").splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0])
            except ValueError:
                return None
    return None


def top_level_boxes(path: str, limit: int = 64) -> list[str]:
    """Return MP4/MOV top-level atom names in file order (to check 'moov' before 'mdat')."""
    names: list[str] = []
    size_total = os.path.getsize(path)
    with open(path, "rb") as f:
        pos = 0
        while pos < size_total and len(names) < limit:
            f.seek(pos)
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            size = int.from_bytes(hdr[:4], "big")
            name = hdr[4:8].decode("latin-1")
            if size == 1:
                ext = f.read(8)
                if len(ext) < 8:
                    break
                size = int.from_bytes(ext, "big")
            elif size == 0:
                size = size_total - pos
            if size < 8 or not name.isprintable():
                break
            names.append(name)
            pos += size
    return names


def have_binary(name: str) -> bool:
    return shutil.which(name) is not None

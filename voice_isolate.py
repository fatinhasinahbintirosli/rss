"""Buang muzik, kekalkan suara (macam "Isolate voice" di CapCut).

Guna Demucs (htdemucs, mod dua-stem "vocals") untuk asingkan suara daripada muzik, kemudian
gabung semula suara bersih itu dengan video asal (video tidak di-encode semula).

Demucs dipasang hanya bila diperlukan (pertama kali fungsi ini dipanggil dalam satu larian),
supaya larian tanpa pilihan ini tidak jadi perlahan.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import tempfile

MODEL = os.environ.get("VOICE_MODEL", "htdemucs")
# Purata kuat bunyi (dB) di bawah ini = hampir senyap = video itu sebenarnya tiada suara orang.
SILENCE_DB = float(os.environ.get("VOICE_SILENCE_DB", "-50"))


class NoVoice(RuntimeError):
    """Video ini muzik sahaja / tiada suara orang."""


def _run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def has_audio(path: str) -> bool:
    r = _run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
              "-of", "csv=p=0", path], timeout=60)
    return bool(r.stdout.strip())


def mean_volume_db(path: str) -> float | None:
    """Purata kuat bunyi dalam dB (None jika tidak dapat diukur)."""
    r = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af", "volumedetect",
              "-f", "null", "-"], timeout=300)
    m = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", r.stderr)
    if not m:
        return None
    return float("-inf") if m.group(1) == "-inf" else float(m.group(1))


def ensure_demucs(log=print) -> None:
    try:
        import demucs  # noqa: F401
        return
    except ImportError:
        pass
    log("   Pasang Demucs (sekali sahaja, ~2 minit)...")
    # torch versi CPU (lebih kecil dan cepat dipasang di GitHub Actions)
    cmd = [sys.executable, "-m", "pip", "install", "-q", "torch", "torchaudio", "--index-url",
           "https://download.pytorch.org/whl/cpu"]
    r = _run(cmd, timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"Gagal pasang torch: {r.stderr[-300:]}")
    r = _run([sys.executable, "-m", "pip", "install", "-q", "demucs", "soundfile"], timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"Gagal pasang demucs: {r.stderr[-300:]}")


def separate_vocals(wav_in: str, out_dir: str, log=print) -> str:
    """Jalankan Demucs pada fail wav; pulangkan laluan fail suara (vocals.wav)."""
    ensure_demucs(log)
    r = _run([sys.executable, "-m", "demucs.separate", "-n", MODEL, "--two-stems", "vocals",
              "--device", "cpu", "-o", out_dir, wav_in], timeout=1500)
    if r.returncode != 0:
        raise RuntimeError(f"Demucs gagal: {(r.stderr or r.stdout)[-300:]}")
    found = glob.glob(os.path.join(out_dir, "**", "vocals.wav"), recursive=True)
    if not found:
        raise RuntimeError("Demucs tidak menghasilkan fail suara.")
    return found[0]


def mux(video_path: str, audio_path: str, out_path: str) -> None:
    """Video asal (disalin tanpa encode) + audio baharu (AAC)."""
    r = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", video_path, "-i", audio_path,
              "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
              "-shortest", "-movflags", "+faststart", out_path], timeout=600)
    if r.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"Gabung audio gagal: {r.stderr[-300:]}")


def isolate(path: str, log=print, separate=None) -> str:
    """Pulangkan laluan video baharu (di sebelah fail asal) yang muziknya dibuang.
    Lempar NoVoice jika video tiada suara orang. `separate` untuk ujian (ganti Demucs)."""
    if not has_audio(path):
        raise NoVoice("Video tiada audio.")
    separate = separate or separate_vocals
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "audio.wav")
        r = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-vn", "-ac", "2",
                  "-ar", "44100", wav], timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"Tarik audio gagal: {r.stderr[-300:]}")
        vocals = separate(wav, td, log)
        db = mean_volume_db(vocals)
        if db is not None and db < SILENCE_DB:
            raise NoVoice(f"Tiada suara orang dikesan (purata {db:.0f} dB): muzik sahaja.")
        out = os.path.splitext(path)[0] + ".voice.mp4"
        mux(path, vocals, out)
    log("   Muzik dibuang, suara dikekalkan.")
    return out

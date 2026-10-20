"""Layer 2: copyrighted music detection via audio fingerprinting.

Facebook's Rights Manager mutes, blocks or demonetises videos that contain
registered music. We sample short clips across the video and ask a
fingerprinting service whether any known recording is present.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import tempfile
import time
from dataclasses import dataclass

import requests

from . import media
from .config import Settings
from .models import BLOCK, INFO, WARN, Finding, LayerResult

SILENCE_DB = -50.0


@dataclass
class Match:
    title: str
    artist: str
    label: str = ""
    score: float | None = None   # 0-100 when provider gives it
    link: str = ""


class ProviderError(RuntimeError):
    pass


# ---------------------------------------------------------------- providers
def identify_audd(clip_path: str, token: str, session: requests.Session | None = None) -> Match | None:
    s = session or requests
    with open(clip_path, "rb") as f:
        r = s.post("https://api.audd.io/", data={"api_token": token}, files={"file": f}, timeout=60)
    r.raise_for_status()
    j = r.json()
    if j.get("status") != "success":
        raise ProviderError(f"AudD: {j.get('error', {}).get('error_message', j)}")
    res = j.get("result")
    if not res:
        return None
    return Match(title=res.get("title", ""), artist=res.get("artist", ""), label=res.get("label", "") or "",
                 link=res.get("song_link", "") or "")


def acrcloud_signature(access_key: str, access_secret: str, timestamp: str) -> str:
    to_sign = "\n".join(["POST", "/v1/identify", access_key, "audio", "1", timestamp])
    digest = hmac.new(access_secret.encode(), to_sign.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def identify_acrcloud(clip_path: str, host: str, key: str, secret: str,
                      session: requests.Session | None = None) -> Match | None:
    s = session or requests
    ts = str(int(time.time()))
    size = os.path.getsize(clip_path)
    with open(clip_path, "rb") as f:
        r = s.post(f"https://{host}/v1/identify",
                   data={"access_key": key, "sample_bytes": size, "timestamp": ts,
                         "signature": acrcloud_signature(key, secret, ts),
                         "data_type": "audio", "signature_version": "1"},
                   files={"sample": f}, timeout=60)
    r.raise_for_status()
    j = r.json()
    code = (j.get("status") or {}).get("code")
    if code == 1001:          # no result
        return None
    if code != 0:
        raise ProviderError(f"ACRCloud: {(j.get('status') or {}).get('msg', j)}")
    music = (j.get("metadata") or {}).get("music") or []
    if not music:
        return None
    m = music[0]
    artists = ", ".join(a.get("name", "") for a in m.get("artists", []) if a.get("name"))
    return Match(title=m.get("title", ""), artist=artists, label=m.get("label", "") or "",
                 score=float(m["score"]) if m.get("score") is not None else None)


# ---------------------------------------------------------------- layer
def _clip_starts(duration: float, clip: int, every: int, max_clips: int) -> list[float]:
    if duration <= clip:
        return [0.0]
    starts = []
    t = 0.0
    while t + 3 < duration and len(starts) < max_clips:
        starts.append(t)
        t += every
    # Spread evenly if the video is long and we hit the cap.
    if len(starts) == max_clips and duration > every * max_clips:
        step = (duration - clip) / max(1, max_clips - 1)
        starts = [round(i * step, 2) for i in range(max_clips)]
    return starts


def run(path: str, duration: float, has_audio: bool, settings: Settings, identify=None) -> LayerResult:
    """`identify(clip_path) -> Match | None` can be injected for testing."""
    lr = LayerResult(layer=2, name="Hak cipta muzik")

    if not has_audio:
        lr.note = "Tiada audio, tiada risiko muzik."
        return lr

    provider = settings.music_provider.lower()
    if identify is None:
        if provider == "audd" and settings.audd_api_token:
            identify = lambda p: identify_audd(p, settings.audd_api_token)  # noqa: E731
        elif provider == "acrcloud" and settings.acrcloud_host and settings.acrcloud_access_key:
            identify = lambda p: identify_acrcloud(p, settings.acrcloud_host, settings.acrcloud_access_key,  # noqa: E731
                                                   settings.acrcloud_access_secret)
        else:
            lr.status = "skipped"
            lr.note = "Pembekal muzik belum disetkan (AUDD_API_TOKEN atau ACRCLOUD_*)."
            return lr

    matches: dict[tuple[str, str], Finding] = {}
    errors = 0
    checked = 0
    with tempfile.TemporaryDirectory() as td:
        for i, start in enumerate(_clip_starts(duration, settings.audio_clip_seconds,
                                               settings.audio_clip_every, settings.max_audio_clips)):
            vol = media.mean_volume_db(path, start, settings.audio_clip_seconds)
            if vol is not None and vol < SILENCE_DB:
                continue
            clip = os.path.join(td, f"clip_{i}.mp3")
            if not media.extract_audio_clip(path, clip, start, settings.audio_clip_seconds):
                continue
            checked += 1
            try:
                m = identify(clip)
            except Exception as e:  # network or provider failure
                errors += 1
                lr.note = f"Ralat pembekal: {e}"[:300]
                continue
            if not m:
                continue
            key = (m.title.lower(), m.artist.lower())
            if key in matches:
                matches[key].timestamps.append(start)
                continue
            low_conf = m.score is not None and m.score < 70
            sev = BLOCK if settings.music_match_severity.lower() == BLOCK and not low_conf else WARN
            matches[key] = Finding(
                2, sev, "copyright_music",
                f"Lagu berhak cipta dikesan: {m.artist} - {m.title}",
                timestamps=[start],
                evidence=" | ".join(x for x in [m.label and f"Label: {m.label}", m.link] if x),
                fix=("Kalau lagu ini dipilih dari pustaka muzik Facebook semasa buat reel, biasanya selamat untuk crosspost. "
                     "Kalau tidak, ganti dengan muzik bebas royalti (contoh: Meta Sound Collection)."),
                confidence=m.score,
            )

    lr.findings.extend(matches.values())
    lr.data = {"clips_checked": checked, "provider_errors": errors}
    if checked and errors == checked:
        lr.status = "error"
    elif errors:
        lr.findings.append(Finding(2, INFO, "partial_check",
                                   f"{errors} daripada {checked} klip audio gagal disemak."))
    return lr

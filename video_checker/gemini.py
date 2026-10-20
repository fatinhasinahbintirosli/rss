"""Gemini video review: one call watches the whole video (picture + sound).

Replaces frame sampling (layer 3), transcription and the AI text review (layer 4)
when VISUAL_PROVIDER=gemini. Keyword rules in layer 4 still run on Gemini's
transcript and on-screen text.

REST flow (ai.google.dev/gemini-api/docs/generate-content/video-understanding):
  1. resumable upload to /upload/v1beta/files
  2. poll file until state == ACTIVE
  3. models/{model}:generateContent with a file_data part
  4. delete the file
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import requests

from . import llm, policy
from .config import Settings
from .models import BLOCK, WARN, fmt_ts

BASE = "https://generativelanguage.googleapis.com"


class GeminiError(RuntimeError):
    pass


@dataclass
class GeminiReview:
    visual: list[dict] = field(default_factory=list)       # {category, severity, t, confidence, description}
    transcript: list[dict] = field(default_factory=list)   # {start, end, text}
    screen_text: list[dict] = field(default_factory=list)  # {t, text}
    text_issues: list[dict] = field(default_factory=list)  # same shape as layer 4 AI issues


def _ts(v) -> float | None:
    """Accept seconds (number) or 'MM:SS' / 'H:MM:SS'."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        pass
    try:
        parts = [float(p) for p in s.split(":")]
    except ValueError:
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def build_prompt(caption: str) -> str:
    vcats = "\n".join(f"- {k}: {v[1]}" for k, v in policy.VISUAL.items())
    tcats = "\n".join(f"- {k}: {v[1]}" for k, v in policy.TEXT.items())
    return f"""You are a strict but fair content moderator. This video will be posted as a Reel on a Malaysian
Facebook Page. Watch the whole video and listen to the audio. Normal everyday content, selling,
cooking, news reporting and religious greetings are fine. Flag only real issues under Meta
Community Standards and Malaysian law.

CAPTION that will be posted with it:
{caption or "(none)"}

VISUAL categories (use keys exactly):
{vcats}

TEXT / SPEECH categories (use keys exactly):
{tcats}

Severity: "block" = Facebook would very likely remove the video or restrict the Page.
"warn" = may reduce reach, needs an age gate, or a human should look. Do not list anything else.
Timestamps are seconds from the start (numbers).

Return JSON only:
{{
  "visual_issues": [{{"category": "...", "severity": "block|warn", "t": 12.0, "confidence": 0-100,
                      "description": "short reason in Bahasa Melayu"}}],
  "transcript": [{{"start": 0.0, "end": 4.0, "text": "exact words spoken, original language"}}],
  "screen_text": [{{"t": 0.0, "text": "exact on-screen text"}}],
  "text_issues": [{{"category": "...", "severity": "block|warn", "source": "caption|speech|screen",
                    "timestamp": 3.0, "quote": "short exact quote", "reason": "Bahasa Melayu",
                    "fix": "cadangan pembetulan dalam Bahasa Melayu"}}]
}}"""


def parse_review(data: dict) -> GeminiReview:
    r = GeminiReview()
    for it in data.get("visual_issues") or []:
        sev = str(it.get("severity", WARN)).lower()
        if sev not in (BLOCK, WARN):
            continue
        try:
            conf = float(it.get("confidence", 70))
        except (TypeError, ValueError):
            conf = 70.0
        r.visual.append({"category": str(it.get("category", "")).strip(), "severity": sev,
                         "t": _ts(it.get("t")), "confidence": conf,
                         "description": str(it.get("description", ""))[:200]})
    for s in data.get("transcript") or []:
        txt = str(s.get("text", "")).strip()
        if txt:
            r.transcript.append({"start": _ts(s.get("start")) or 0.0, "end": _ts(s.get("end")) or 0.0, "text": txt})
    for s in data.get("screen_text") or []:
        txt = str(s.get("text", "")).strip()
        if txt:
            r.screen_text.append({"t": _ts(s.get("t")) or 0.0, "text": txt})
    for it in data.get("text_issues") or []:
        it = dict(it)
        it["timestamp"] = _ts(it.get("timestamp"))
        r.text_issues.append(it)
    return r


class GeminiClient:
    def __init__(self, api_key: str, model: str, session: requests.Session | None = None):
        if not api_key:
            raise GeminiError("GEMINI_API_KEY belum diset")
        self.key = api_key
        self.model = model
        self.s = session or requests.Session()

    def _h(self, extra: dict | None = None) -> dict:
        return {"x-goog-api-key": self.key, **(extra or {})}

    def upload(self, path: str, mime: str = "video/mp4") -> dict:
        size = os.path.getsize(path)
        r = self.s.post(f"{BASE}/upload/v1beta/files", headers=self._h({
            "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size), "X-Goog-Upload-Header-Content-Type": mime,
            "Content-Type": "application/json"}), json={"file": {"display_name": os.path.basename(path)}}, timeout=60)
        r.raise_for_status()
        url = r.headers.get("x-goog-upload-url")
        if not url:
            raise GeminiError("Tiada upload URL daripada Gemini")
        with open(path, "rb") as f:
            r = self.s.post(url, headers={"Content-Length": str(size), "X-Goog-Upload-Offset": "0",
                                          "X-Goog-Upload-Command": "upload, finalize"}, data=f, timeout=600)
        r.raise_for_status()
        return r.json()["file"]

    def wait_active(self, file: dict, timeout: float = 300, poll: float = 5) -> dict:
        deadline = time.time() + timeout
        while True:
            state = file.get("state")
            if state == "ACTIVE":
                return file
            if state == "FAILED":
                raise GeminiError("Gemini gagal memproses video")
            if time.time() > deadline:
                raise GeminiError("Gemini mengambil masa terlalu lama memproses video")
            time.sleep(poll)
            r = self.s.get(f"{BASE}/v1beta/{file['name']}", headers=self._h(), timeout=30)
            r.raise_for_status()
            file = r.json()

    def delete(self, file: dict) -> None:
        try:
            self.s.delete(f"{BASE}/v1beta/{file['name']}", headers=self._h(), timeout=30)
        except requests.RequestException:
            pass

    def generate_json(self, file: dict, prompt: str) -> dict:
        body = {
            "contents": [{"parts": [
                {"file_data": {"mime_type": file.get("mimeType", "video/mp4"), "file_uri": file["uri"]}},
                {"text": prompt},
            ]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
        }
        r = self.s.post(f"{BASE}/v1beta/models/{self.model}:generateContent",
                        headers=self._h({"Content-Type": "application/json"}), json=body, timeout=600)
        if r.status_code >= 400:
            raise GeminiError(f"Gemini {r.status_code}: {r.text[:300]}")
        j = r.json()
        cand = (j.get("candidates") or [{}])[0]
        if cand.get("finishReason") in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            # Gemini itself refused to look at it: treat as a strong red flag.
            return {"visual_issues": [{"category": "nudity_sexual" if cand.get("finishReason") != "BLOCKLIST" else "disturbing",
                                       "severity": "block", "t": None, "confidence": 80,
                                       "description": f"Gemini menolak memproses video ({cand.get('finishReason')})."}]}
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
        if not text:
            raise GeminiError(f"Jawapan Gemini kosong: {json.dumps(j)[:300]}")
        data = llm.parse_json(text)
        if not isinstance(data, dict):
            raise GeminiError("Format jawapan Gemini tidak dijangka")
        return data


def review_video(path: str, caption: str, settings: Settings, client: GeminiClient | None = None) -> GeminiReview:
    client = client or GeminiClient(settings.gemini_api_key, settings.gemini_model)
    f = client.upload(path)
    try:
        f = client.wait_active(f)
        return parse_review(client.generate_json(f, build_prompt(caption)))
    finally:
        client.delete(f)


def describe(review: GeminiReview) -> str:
    """Short human summary (for logs)."""
    v = ", ".join(f"{i['category']}@{fmt_ts(i['t'])}" for i in review.visual) or "-"
    return f"visual: {v}; transcript lines: {len(review.transcript)}; text issues: {len(review.text_issues)}"

"""Layer 4: speech, on-screen text and caption.

Steps:
  a. transcribe speech (faster-whisper locally, or OpenAI Whisper API)
  b. on-screen text (from layer 3's vision output, else Tesseract OCR on frames)
  c. keyword rules (always run)
  d. LLM review of all text in context (hate, harassment, misinformation, scams...)
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from difflib import SequenceMatcher
from typing import Callable

import requests

from . import llm, media, policy, rules
from .config import Settings
from .media import Frame
from .models import BLOCK, INFO, SEVERITY_RANK, WARN, Finding, LayerResult, fmt_ts

Segment = dict   # {"start": float, "end": float, "text": str}


# ----------------------------------------------------------------- transcription
def transcribe_local(wav_path: str, settings: Settings) -> list[Segment]:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as e:
        raise RuntimeError("Pakej 'faster-whisper' belum dipasang") from e
    model = WhisperModel(settings.whisper_model, device="auto", compute_type="int8")
    segments, _info = model.transcribe(wav_path, vad_filter=True)
    return [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments if s.text.strip()]


def transcribe_openai(wav_path: str, settings: Settings) -> list[Segment]:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY belum diset")
    with open(wav_path, "rb") as f:
        r = requests.post("https://api.openai.com/v1/audio/transcriptions",
                          headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                          data={"model": "whisper-1", "response_format": "verbose_json"},
                          files={"file": ("audio.wav", f, "audio/wav")}, timeout=600)
    r.raise_for_status()
    j = r.json()
    segs = j.get("segments") or [{"start": 0.0, "end": 0.0, "text": j.get("text", "")}]
    return [{"start": float(s.get("start", 0)), "end": float(s.get("end", 0)), "text": s.get("text", "").strip()}
            for s in segs if s.get("text", "").strip()]


# ----------------------------------------------------------------- OCR
def ocr_tesseract(frames: list[Frame], langs: str) -> list[dict]:
    if not media.have_binary("tesseract"):
        raise RuntimeError("tesseract tidak dipasang")
    available = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout.split()
    use = "+".join(l for l in langs.split("+") if l in available) or "eng"
    out = []
    for f in frames:
        r = subprocess.run(["tesseract", f.path, "stdout", "-l", use, "--psm", "11"],
                           capture_output=True, text=True, timeout=60)
        words = [w for w in r.stdout.split() if sum(c.isalnum() for c in w) >= 2 or w.isdigit()]
        text = " ".join(words)
        if len(text) >= 4:
            out.append({"t": f.t, "text": text})
    return out


def dedupe_screen_text(items: list[dict]) -> list[dict]:
    """Overlays stay on screen for many frames; keep the first occurrence of similar text."""
    kept: list[dict] = []
    for it in items:
        if any(SequenceMatcher(None, it["text"].lower(), k["text"].lower()).ratio() > 0.85 for k in kept[-5:]):
            continue
        kept.append(it)
    return kept


# ----------------------------------------------------------------- LLM review
TEXT_SYSTEM = (
    "You review text that will be posted with a video on a Facebook Page in Malaysia. "
    "The text may be in Bahasa Melayu, English, Manglish, Chinese or Tamil. "
    "Flag only real issues under Meta Community Standards, Meta advertising/branded content rules "
    "and Malaysian law. Normal selling, religious greetings and casual slang are fine. Reply with JSON only."
)


def _text_prompt(caption: str, transcript: list[Segment], screen: list[dict]) -> str:
    cats = "\n".join(f"- {k}: {v[1]}" for k, v in policy.TEXT.items())
    tr = "\n".join(f"[{fmt_ts(s['start'])}] {s['text']}" for s in transcript) or "(tiada)"
    sc = "\n".join(f"[{fmt_ts(s['t'])}] {s['text']}" for s in screen) or "(tiada)"
    return (
        f"CAPTION:\n{caption or '(tiada)'}\n\nSPEECH TRANSCRIPT:\n{tr}\n\nON-SCREEN TEXT:\n{sc}\n\n"
        f"Categories (use these keys exactly):\n{cats}\n\n"
        "Severity: 'block' if Facebook would very likely remove the post or restrict the Page; "
        "'warn' if it may reduce reach or should be reviewed by a human.\n"
        'Return {"issues":[{"category":"...","severity":"block|warn","source":"caption|speech|screen",'
        '"timestamp":seconds or null,"quote":"exact short quote","reason":"Bahasa Melayu",'
        '"fix":"cadangan pembetulan dalam Bahasa Melayu"}]}. Return {"issues":[]} if nothing is wrong.'
    )


def review_text_with_claude(caption: str, transcript: list[Segment], screen: list[dict],
                            settings: Settings) -> list[dict]:
    data = llm.ask_json(settings, TEXT_SYSTEM, [{"type": "text", "text": _text_prompt(caption, transcript, screen)}])
    return list(data.get("issues") or []) if isinstance(data, dict) else []


# ----------------------------------------------------------------- layer
SOURCE_MY = {"caption": "caption", "speech": "percakapan", "screen": "teks dalam video"}


def run(path: str, caption: str, has_audio: bool, frames: list[Frame], screen_text: list[dict] | None,
        settings: Settings,
        transcriber: Callable[[str, Settings], list[Segment]] | None = None,
        text_reviewer: Callable[..., list[dict]] | None = None) -> LayerResult:
    lr = LayerResult(layer=4, name="Suara & teks")
    notes: list[str] = []
    incomplete = False

    # a. transcript
    transcript: list[Segment] = []
    if has_audio:
        prov = settings.transcribe_provider.lower()
        if transcriber is None:
            transcriber = {"local": transcribe_local, "openai": transcribe_openai}.get(prov)
        if transcriber is None:
            notes.append("Transkrip suara dimatikan.")
            incomplete = True
        else:
            with tempfile.TemporaryDirectory() as td:
                wav = os.path.join(td, "audio.wav")
                try:
                    if media.extract_audio_wav(path, wav):
                        transcript = transcriber(wav, settings)
                except Exception as e:
                    notes.append(f"Transkrip gagal: {e}"[:200])
                    incomplete = True

    # b. on-screen text
    screen = list(screen_text or [])
    if not screen and settings.ocr_provider.lower() in ("auto", "tesseract") and frames:
        try:
            screen = ocr_tesseract(frames, settings.tesseract_langs)
        except Exception as e:
            notes.append(f"OCR gagal: {e}"[:200])
    screen = dedupe_screen_text(screen)

    lr.data = {"transcript": transcript, "screen_text": screen}

    findings: dict[tuple[str, str], Finding] = {}

    def add(cat: str, sev: str, source: str, quote: str, t: float | None, reason: str, fix: str, origin: str):
        label = policy.text_label(cat)[1]
        key = (cat, source, quote.lower()[:60])
        f = findings.get(key)
        if f is None:
            f = findings[key] = Finding(4, sev, f"text_{cat}",
                                        f"{label} ({SOURCE_MY.get(source, source)})",
                                        timestamps=[], evidence=f"\"{quote}\"" + (f" - {reason}" if reason else ""),
                                        fix=fix)
            f.code += f":{origin}"
        elif SEVERITY_RANK[sev] > SEVERITY_RANK[f.severity]:
            f.severity = sev
        if t is not None and t not in f.timestamps:
            f.timestamps.append(t)

    # c. rules
    for sev, note, fix in rules.caption_checks(caption):
        lr.findings.append(Finding(4, sev, "text_caption_format", note, fix=fix))
    hits = rules.scan(caption, "caption")
    for s in transcript:
        hits += rules.scan(s["text"], "speech", s["start"])
    for s in screen:
        hits += rules.scan(s["text"], "screen", s["t"])
    for h in hits:
        add(h.rule.category, h.rule.severity, h.source, h.match, h.t, h.rule.note, h.rule.fix, "rule")

    # d. LLM review
    has_text = bool(caption.strip() or transcript or screen)
    if settings.text_llm_review and has_text:
        if text_reviewer is None and settings.claude_ready():
            text_reviewer = review_text_with_claude
        if text_reviewer is None:
            notes.append("Semakan konteks AI dimatikan (ANTHROPIC_API_KEY belum diset); hanya peraturan kata kunci digunakan.")
            incomplete = True
        else:
            try:
                for iss in text_reviewer(caption, transcript, screen, settings):
                    sev = str(iss.get("severity", WARN)).lower()
                    if sev not in (BLOCK, WARN, INFO):
                        sev = WARN
                    t = iss.get("timestamp")
                    try:
                        t = float(t) if t is not None else None
                    except (TypeError, ValueError):
                        t = None
                    add(str(iss.get("category", "other")), sev, str(iss.get("source", "caption")),
                        str(iss.get("quote", ""))[:160], t, str(iss.get("reason", ""))[:300],
                        str(iss.get("fix", ""))[:300], "ai")
            except Exception as e:
                notes.append(f"Semakan AI gagal: {e}"[:200])
                incomplete = True

    lr.findings.extend(findings.values())
    if incomplete:
        lr.status = "partial"
    lr.note = " ".join(notes)
    return lr

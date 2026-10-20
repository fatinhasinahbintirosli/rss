"""Runs layers 1-4 and turns findings into a green / yellow / red verdict."""

from __future__ import annotations

import tempfile
import time
from typing import Any

from . import gemini, layer1_technical, layer2_copyright, layer3_visual, layer4_text, media
from .config import Settings
from .models import BLOCK, GREEN, RED, WARN, YELLOW, Finding, LayerResult, Report


def score(layers: list[LayerResult], strict: bool = True) -> tuple[str, str]:
    findings = [f for lr in layers for f in lr.findings]
    blocks = [f for f in findings if f.severity == BLOCK]
    warns = [f for f in findings if f.severity == WARN]
    not_run = [lr for lr in layers if lr.status in ("skipped", "error", "partial")]

    if blocks:
        return RED, f"{len(blocks)} masalah serius dikesan. Jangan upload sebelum dibetulkan."
    if warns:
        return YELLOW, f"{len(warns)} perkara perlu disemak manusia sebelum upload."
    if strict and not_run:
        names = ", ".join(f"lapisan {lr.layer} ({lr.name})" for lr in not_run)
        return YELLOW, f"Tiada masalah dikesan, tetapi {names} tidak dijalankan sepenuhnya. Semak manual."
    return GREEN, "Tiada masalah dikesan dalam semua lapisan. Risiko rendah (bukan jaminan 100%)."


def check_video(path: str, caption: str = "", target: str = "reel", settings: Settings | None = None,
                layers: tuple[int, ...] = (1, 2, 3, 4), overrides: dict[str, Any] | None = None) -> Report:
    """Run the checker.

    overrides (for tests / custom providers):
      music_identify(clip_path) -> Match|None
      visual_reviewer(frames, settings) -> FrameReview
      transcriber(wav_path, settings) -> [segments]
      text_reviewer(caption, transcript, screen, settings) -> [issues]
    """
    settings = settings or Settings()
    ov = overrides or {}
    t0 = time.time()
    results: list[LayerResult] = []

    l1 = layer1_technical.run(path, target)
    if 1 in layers:
        results.append(l1)
    duration = float(l1.data.get("duration") or 0)
    has_audio = bool(l1.data.get("has_audio"))

    # Unreadable file: nothing else can run.
    if l1.status == "error":
        if 1 not in layers:
            results.append(l1)
        verdict, summary = score(results, settings.strict)
        return Report(path, target, caption, verdict, summary, results, time.time() - t0)

    if 2 in layers:
        results.append(layer2_copyright.run(path, duration, has_audio, settings, ov.get("music_identify")))

    use_gemini = settings.visual_provider.lower() == "gemini" and (settings.gemini_ready() or "gemini_review" in ov)
    if use_gemini and (3 in layers or 4 in layers):
        results.extend(_gemini_layers(path, caption, has_audio, settings, layers, ov))
        verdict, summary = score(results, settings.strict)
        return Report(path, target, caption, verdict, summary, results, time.time() - t0)

    with tempfile.TemporaryDirectory() as td:
        frames = []
        if 3 in layers or 4 in layers:
            frames = media.extract_frames(path, td, duration, settings.frame_interval, settings.max_frames)

        screen_text = None
        if 3 in layers:
            l3 = layer3_visual.run(frames, settings, ov.get("visual_reviewer"))
            results.append(l3)
            # Vision model already read on-screen text; reuse it instead of running Tesseract.
            if l3.data.get("ocr"):
                screen_text = l3.data["ocr"]

        if 4 in layers:
            results.append(layer4_text.run(path, caption, has_audio, frames, screen_text, settings,
                                           ov.get("transcriber"), ov.get("text_reviewer")))

    verdict, summary = score(results, settings.strict)
    return Report(path, target, caption, verdict, summary, results, time.time() - t0)


def _gemini_layers(path: str, caption: str, has_audio: bool, settings: Settings, layers: tuple[int, ...],
                   ov: dict[str, Any]) -> list[LayerResult]:
    """Layers 3 and 4 from a single Gemini pass over the whole video."""
    out: list[LayerResult] = []
    review_fn = ov.get("gemini_review") or (lambda p, c, s: gemini.review_video(p, c, s))
    try:
        rv = review_fn(path, caption, settings)
        err = ""
    except Exception as e:
        rv, err = None, f"Ralat Gemini: {e}"[:300]

    if 3 in layers:
        l3 = LayerResult(layer=3, name="Visual")
        if rv is None:
            l3.status, l3.note = "error", err
        else:
            times = sorted({(i["t"] or 0.0) for i in rv.visual})
            frames = [media.Frame("", t) for t in times]
            idx = {t: n for n, t in enumerate(times)}
            fr_review: dict = {}
            for i in rv.visual:
                n = idx[i["t"] or 0.0]
                fr_review.setdefault(n, ([], ""))[0].append(
                    (i["category"], i["severity"], i["confidence"], i["description"]))
            l3.findings = layer3_visual.aggregate(frames, fr_review)
            l3.data = {"provider": "gemini", "model": settings.gemini_model}
        out.append(l3)

    if 4 in layers:
        if rv is None:
            # Gemini down: keyword rules on the caption only, and the layer is marked partial.
            l4 = layer4_text.run(path, caption, False, [], [], settings,
                                 text_reviewer=lambda *a: [])
            l4.status = "partial"
            l4.note = (l4.note + " " + err + " Hanya caption disemak.").strip()
        else:
            # Gemini's transcript stands in for Whisper, so keyword rules also run on the speech.
            l4 = layer4_text.run(path, caption, has_audio, [], rv.screen_text, settings,
                                 transcriber=lambda wav, s: rv.transcript,
                                 text_reviewer=lambda *a: rv.text_issues)
            if has_audio and not rv.transcript:
                l4.note = (l4.note + " Tiada percakapan dikesan.").strip()
        out.append(l4)
    return out


def top_reasons(report: Report, n: int = 5) -> list[Finding]:
    order = {BLOCK: 0, WARN: 1}
    fs = [f for f in report.findings if f.severity in order]
    return sorted(fs, key=lambda f: (order[f.severity], f.layer))[:n]

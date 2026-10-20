"""Layer 3: visual moderation of sampled frames.

Providers:
  claude       - vision model reviews frames in batches; also returns on-screen text (used by layer 4)
  rekognition  - AWS Rekognition DetectModerationLabels per frame (no OCR)
"""

from __future__ import annotations

from typing import Callable

from . import llm, policy
from .config import Settings
from .media import Frame
from .models import BLOCK, SEVERITY_RANK, WARN, Finding, LayerResult, fmt_ts

# A frame-level hit: (category, severity, confidence 0-100, description)
Hit = tuple[str, str, float, str]
# Provider returns, per frame index: (hits, ocr_text)
FrameReview = dict[int, tuple[list[Hit], str]]


# ----------------------------------------------------------------- Claude
CLAUDE_SYSTEM = (
    "You are a strict content moderator checking video frames before they are posted to a Facebook Page. "
    "Judge only what is visible. Do not flag normal everyday content. Reply with JSON only."
)


def _claude_prompt(times: list[float]) -> str:
    cats = "\n".join(f"- {k}: {v[1]}" for k, v in policy.VISUAL.items())
    listing = ", ".join(f"frame {i} at {fmt_ts(t)}" for i, t in enumerate(times))
    return (
        f"The images above are frames from one video ({listing}).\n\n"
        "For each frame, list any policy issue from these categories (keys must match exactly):\n"
        f"{cats}\n\n"
        "Severity: 'block' if Facebook would very likely remove the video or restrict the Page, "
        "'warn' if it may limit reach, need an age gate or a human should look, otherwise do not list it.\n"
        "Also transcribe any readable on-screen text (captions, signs, overlays) exactly, in the original language.\n\n"
        'Return: {"frames":[{"index":0,"issues":[{"category":"...","severity":"block|warn",'
        '"confidence":0-100,"description":"short reason in Bahasa Melayu"}],"text":"..."}]}'
    )


def review_with_claude(frames: list[Frame], settings: Settings) -> FrameReview:
    out: FrameReview = {}
    n = max(1, settings.frames_per_request)
    for b in range(0, len(frames), n):
        batch = frames[b:b + n]
        content = [llm.image_block(f.path) for f in batch]
        content.append({"type": "text", "text": _claude_prompt([f.t for f in batch])})
        data = llm.ask_json(settings, CLAUDE_SYSTEM, content)
        for fr in (data.get("frames") or []):
            try:
                idx = b + int(fr.get("index", -1))
            except (TypeError, ValueError):
                continue
            if not (b <= idx < b + len(batch)):
                continue
            hits: list[Hit] = []
            for iss in fr.get("issues") or []:
                cat = str(iss.get("category", "")).strip()
                sev = str(iss.get("severity", WARN)).lower()
                if sev not in (BLOCK, WARN):
                    continue
                try:
                    conf = float(iss.get("confidence", 70))
                except (TypeError, ValueError):
                    conf = 70.0
                hits.append((cat, sev, conf, str(iss.get("description", ""))[:200]))
            out[idx] = (hits, str(fr.get("text") or "").strip())
    return out


# ----------------------------------------------------------------- Rekognition
# Rekognition label names (v7 taxonomy, older names kept for compatibility) -> our category.
REKOG_MAP = [
    ("explicit", "nudity_sexual"),
    ("sexual activity", "nudity_sexual"),
    ("non-explicit nudity", "suggestive"),
    ("suggestive", "suggestive"),
    ("swimwear or underwear", "suggestive"),
    ("graphic violence", "graphic_violence"),
    ("blood", "graphic_violence"),
    ("corpses", "graphic_violence"),
    ("self-harm", "self_harm"),
    ("self injury", "self_harm"),
    ("weapon", "weapons"),
    ("violence", "violence"),
    ("visually disturbing", "disturbing"),
    ("emaciated", "disturbing"),
    ("drug", "drugs"),
    ("pills", "drugs"),
    ("tobacco", "tobacco_vape_alcohol"),
    ("smoking", "tobacco_vape_alcohol"),
    ("alcohol", "tobacco_vape_alcohol"),
    ("drinking", "tobacco_vape_alcohol"),
    ("gambling", "gambling"),
    ("hate symbol", "hate_symbols"),
    ("nazi", "hate_symbols"),
    ("white supremacy", "hate_symbols"),
    ("extremist", "hate_symbols"),
    ("rude gesture", "violence"),
    ("middle finger", "violence"),
]
# Swimwear is everyday content; only note it if confidence is very high.
REKOG_SOFT = {"swimwear or underwear"}


def rekog_category(name: str, parent: str = "") -> str | None:
    for candidate in (name.lower(), parent.lower()):
        for needle, cat in REKOG_MAP:
            if needle in candidate:
                return cat
    return None


def review_with_rekognition(frames: list[Frame], settings: Settings, client=None) -> FrameReview:
    if client is None:
        try:
            import boto3  # type: ignore
        except ImportError as e:
            raise RuntimeError("Pakej 'boto3' belum dipasang") from e
        client = boto3.client("rekognition", region_name=settings.aws_region)
    out: FrameReview = {}
    for i, f in enumerate(frames):
        with open(f.path, "rb") as fh:
            resp = client.detect_moderation_labels(Image={"Bytes": fh.read()},
                                                   MinConfidence=settings.rekognition_min_confidence)
        hits: list[Hit] = []
        for lab in resp.get("ModerationLabels", []):
            name, parent = lab.get("Name", ""), lab.get("ParentName", "")
            conf = float(lab.get("Confidence", 0))
            if (name.lower() in REKOG_SOFT or parent.lower() in REKOG_SOFT) and conf < 95:
                continue
            cat = rekog_category(name, parent)
            if not cat:
                continue
            sev = policy.visual_label(cat)[0]
            if sev == BLOCK and conf < 80:
                sev = WARN
            hits.append((cat, sev, conf, name))
        out[i] = (hits, "")
    return out


# ----------------------------------------------------------------- aggregate
def aggregate(frames: list[Frame], review: FrameReview) -> list[Finding]:
    """Merge per-frame hits into one finding per category, keeping the worst severity."""
    by_cat: dict[str, Finding] = {}
    for idx, (hits, _text) in sorted(review.items()):
        t = frames[idx].t if idx < len(frames) else None
        for cat, sev, conf, desc in hits:
            default_sev, label = policy.visual_label(cat)
            # Model may escalate but never above BLOCK; unknown categories stay as reported.
            if cat in policy.VISUAL and SEVERITY_RANK[sev] < SEVERITY_RANK[default_sev] and conf >= 85:
                sev = default_sev
            f = by_cat.get(cat)
            if f is None:
                f = by_cat[cat] = Finding(3, sev, f"visual_{cat}", label, timestamps=[], evidence=desc,
                                          confidence=conf)
            if SEVERITY_RANK[sev] > SEVERITY_RANK[f.severity]:
                f.severity = sev
                f.evidence = desc
            f.confidence = max(f.confidence or 0, conf)
            if t is not None and t not in f.timestamps:
                f.timestamps.append(t)
    for cat, f in by_cat.items():
        # A single low-confidence frame is noise more often than not.
        if len(f.timestamps) <= 1 and (f.confidence or 0) < 60:
            f.severity = WARN if f.severity == BLOCK else f.severity
        if cat == "third_party_watermark":
            f.fix = "Guna fail asal tanpa watermark. Facebook kurangkan capaian kandungan yang dimuat naik semula."
        elif cat == "copyrighted_media":
            f.fix = "Pastikan anda pemilik atau ada izin. Klip TV/filem/sukan sering kena block oleh Rights Manager."
        else:
            f.fix = "Potong atau kaburkan bahagian ini sebelum upload."
    return list(by_cat.values())


def run(frames: list[Frame], settings: Settings,
        reviewer: Callable[[list[Frame], Settings], FrameReview] | None = None) -> LayerResult:
    lr = LayerResult(layer=3, name="Visual")
    if not frames:
        lr.status = "error"
        lr.note = "Tiada frame dapat diambil daripada video."
        return lr

    provider = settings.visual_provider.lower()
    if reviewer is None:
        if provider == "claude" and settings.claude_ready():
            reviewer = review_with_claude
        elif provider == "rekognition":
            reviewer = review_with_rekognition
        else:
            lr.status = "skipped"
            lr.note = "Pembekal visual belum disetkan (ANTHROPIC_API_KEY atau AWS)."
            return lr

    try:
        review = reviewer(frames, settings)
    except Exception as e:
        lr.status = "error"
        lr.note = f"Ralat semakan visual: {e}"[:300]
        return lr

    lr.findings = aggregate(frames, review)
    ocr = [{"t": frames[i].t, "text": txt} for i, (_h, txt) in sorted(review.items())
           if txt and i < len(frames)]
    lr.data = {"frames_checked": len(frames), "provider": provider, "ocr": ocr}
    return lr

"""Shared data types for findings and reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

BLOCK = "block"   # red: do not upload
WARN = "warn"     # yellow: needs human review
INFO = "info"     # note only, does not change verdict

SEVERITY_RANK = {INFO: 0, WARN: 1, BLOCK: 2}

GREEN, YELLOW, RED = "green", "yellow", "red"


def fmt_ts(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dataclass
class Finding:
    layer: int
    severity: str
    code: str
    message: str                      # Malay, shown to the user
    timestamps: list[float] = field(default_factory=list)
    evidence: str = ""                # quote, label name, song title...
    fix: str = ""                     # suggestion to fix (Malay)
    confidence: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamps_text"] = [fmt_ts(t) for t in self.timestamps]
        return d


@dataclass
class LayerResult:
    layer: int
    name: str
    status: str = "ok"                # ok | skipped | error
    note: str = ""
    findings: list[Finding] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)   # extra output (transcript, ocr, probe)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "name": self.name,
            "status": self.status,
            "note": self.note,
            "findings": [f.to_dict() for f in self.findings],
            "data": self.data,
        }


@dataclass
class Report:
    video: str
    target: str
    caption: str
    verdict: str
    summary: str
    layers: list[LayerResult]
    elapsed_seconds: float = 0.0

    @property
    def findings(self) -> list[Finding]:
        out: list[Finding] = []
        for lr in self.layers:
            out.extend(lr.findings)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "video": self.video,
            "target": self.target,
            "caption": self.caption,
            "verdict": self.verdict,
            "summary": self.summary,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "layers": [lr.to_dict() for lr in self.layers],
            "disclaimer": (
                "Semakan ini mengurangkan risiko tetapi tidak menjamin video tidak akan "
                "diambil tindakan oleh Facebook."
            ),
        }

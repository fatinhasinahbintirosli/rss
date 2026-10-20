"""Standalone HTML report (Bahasa Melayu) for a Report."""

from __future__ import annotations

import html

from .models import BLOCK, GREEN, INFO, RED, WARN, Report

VERDICT = {
    GREEN: ("#1f9d55", "HIJAU", "Selamat untuk upload"),
    "yellow": ("#d69e2e", "KUNING", "Perlu semakan manual"),
    RED: ("#e53e3e", "MERAH", "Jangan upload"),
}
SEV = {BLOCK: ("#e53e3e", "Serius"), WARN: ("#d69e2e", "Semak"), INFO: ("#718096", "Nota")}
STATUS = {"ok": "Selesai", "skipped": "Tidak dijalankan", "error": "Ralat", "partial": "Separa"}


def render(report: Report) -> str:
    e = html.escape
    color, word, sub = VERDICT[report.verdict]
    rows = []
    for lr in report.layers:
        items = []
        for f in sorted(lr.findings, key=lambda f: {BLOCK: 0, WARN: 1, INFO: 2}[f.severity]):
            c, lbl = SEV[f.severity]
            ts = ", ".join(f.to_dict()["timestamps_text"])
            items.append(
                f'<li><span class="pill" style="background:{c}">{lbl}</span> <b>{e(f.message)}</b>'
                + (f' <span class="ts">@ {e(ts)}</span>' if ts else "")
                + (f'<div class="ev">{e(f.evidence)}</div>' if f.evidence else "")
                + (f'<div class="fix">Cadangan: {e(f.fix)}</div>' if f.fix else "")
                + "</li>")
        body = "<ul>" + "".join(items) + "</ul>" if items else '<p class="ok">Tiada isu.</p>'
        note = f'<p class="note">{e(lr.note)}</p>' if lr.note else ""
        rows.append(f'<section><h2>Lapisan {lr.layer}: {e(lr.name)} '
                    f'<small>{STATUS.get(lr.status, lr.status)}</small></h2>{note}{body}</section>')

    transcript = next((lr.data.get("transcript") for lr in report.layers if lr.layer == 4), None) or []
    tr_html = ""
    if transcript:
        lines = "".join(f"<div><span class='ts'>{int(s['start'])//60}:{int(s['start'])%60:02d}</span> "
                        f"{e(s['text'])}</div>" for s in transcript[:300])
        tr_html = f"<details><summary>Transkrip</summary>{lines}</details>"

    return f"""<!doctype html><html lang="ms"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Laporan Semakan Video</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;max-width:820px;margin:0 auto;padding:16px;color:#1a202c;background:#f7fafc}}
.verdict{{background:{color};color:#fff;border-radius:12px;padding:20px}}
.verdict h1{{margin:0;font-size:28px}} .verdict p{{margin:6px 0 0}}
section{{background:#fff;border-radius:10px;padding:14px 16px;margin:12px 0;box-shadow:0 1px 2px #0001}}
h2{{font-size:17px;margin:0 0 8px}} h2 small{{font-weight:400;color:#718096;font-size:13px}}
ul{{padding-left:0;list-style:none;margin:0}} li{{padding:8px 0;border-top:1px solid #edf2f7}} li:first-child{{border:0}}
.pill{{color:#fff;font-size:12px;padding:2px 8px;border-radius:99px}}
.ts{{color:#718096;font-size:13px}} .ev{{font-size:14px;color:#4a5568;margin-top:4px}}
.fix{{font-size:14px;color:#2b6cb0;margin-top:4px}} .note{{color:#718096;font-size:14px}} .ok{{color:#1f9d55}}
.meta,.disc{{color:#718096;font-size:13px}} details{{background:#fff;padding:12px 16px;border-radius:10px}}
</style></head><body>
<div class="verdict"><h1>{word}: {sub}</h1><p>{e(report.summary)}</p></div>
<p class="meta">Fail: {e(report.video)} · Jenis: {e(report.target)} · Masa semakan: {report.elapsed_seconds:.1f}s</p>
{''.join(rows)}
{tr_html}
<p class="disc">Semakan ini mengurangkan risiko tetapi tidak menjamin video tidak akan diambil tindakan oleh Facebook.</p>
</body></html>"""

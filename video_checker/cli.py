"""Command line:  python -m video_checker check VIDEO [--caption ...] [--target reel|video]"""

from __future__ import annotations

import argparse
import json
import sys

from .config import Settings
from .models import fmt_ts
from .pipeline import check_video, top_reasons
from .report_html import render

ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="video_checker", description="Semak video sebelum upload ke Facebook")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="Semak satu video")
    c.add_argument("video")
    c.add_argument("--caption", default="", help="Caption yang akan digunakan")
    c.add_argument("--caption-file", help="Baca caption daripada fail")
    c.add_argument("--target", choices=["reel", "video"], default="reel")
    c.add_argument("--layers", default="1,2,3,4", help="Contoh: 1,2,4")
    c.add_argument("--json", dest="json_out", help="Simpan laporan JSON")
    c.add_argument("--html", dest="html_out", help="Simpan laporan HTML")
    args = p.parse_args(argv)

    caption = args.caption
    if args.caption_file:
        with open(args.caption_file, encoding="utf-8") as f:
            caption = f.read()
    layers = tuple(int(x) for x in args.layers.split(",") if x.strip())
    report = check_video(args.video, caption, args.target, Settings(), layers)

    print(f"\n{ICON[report.verdict]}  {report.verdict.upper()}: {report.summary}\n")
    for lr in report.layers:
        print(f"  Lapisan {lr.layer} ({lr.name}): {lr.status}" + (f" - {lr.note}" if lr.note else ""))
    reasons = top_reasons(report, 10)
    if reasons:
        print("\nIsu utama:")
        for f in reasons:
            ts = ", ".join(fmt_ts(t) for t in f.timestamps[:5])
            print(f"  [{f.severity}] {f.message}" + (f" @ {ts}" if ts else ""))
            if f.evidence:
                print(f"           {f.evidence}")
            if f.fix:
                print(f"           -> {f.fix}")
    print()

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
    if args.html_out:
        with open(args.html_out, "w", encoding="utf-8") as f:
            f.write(render(report))
    return {"green": 0, "yellow": 1, "red": 2}[report.verdict]


if __name__ == "__main__":
    sys.exit(main())

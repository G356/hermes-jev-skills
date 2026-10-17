#!/usr/bin/env python3
"""One live call that proves TypeSafe accepts this repo's image requests, with a known answer.

Draws a 512x512 black PNG with one white square at a known place, then asks in one request:

* a Noul  "is there a white square?"            (expect high)
* a Noul  "is there a red circle?"              (expect low)
* a Choice "which quadrant is the square in?"   (expect the true one)
* a Box   "the white square"                    (expect overlap with the true box)

Uses the key ``jev setup-key`` stored; the key is never printed. Prints one JSON report and
exits 0 only when every check holds. Costs one request (a few thousand input tokens).

    python3 scripts/live_images_check.py [--model jev-1.14.0] [--no-box]
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jevkit import client, images  # noqa: E402

SIZE = 512
SQUARE = (300, 60, 440, 200)  # left, top, right, bottom in pixels: the top-right quadrant


def square_png() -> bytes:
    left, top, right, bottom = SQUARE
    rows = bytearray()
    for y in range(SIZE):
        rows.append(0)
        rows.extend(255 if top <= y < bottom and left <= x < right else 0 for x in range(SIZE))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b""))


def overlap(box: dict) -> float:
    """Intersection over union of the answered box with the drawn square."""
    truth = [v / SIZE for v in SQUARE]
    got = [box["left"], box["top"], box["right"], box["bottom"]]
    w = max(0.0, min(got[2], truth[2]) - max(got[0], truth[0]))
    h = max(0.0, min(got[3], truth[3]) - max(got[1], truth[1]))
    inter = w * h
    union = (got[2] - got[0]) * (got[3] - got[1]) + (truth[2] - truth[0]) * (truth[3] - truth[1]) - inter
    return inter / union if union > 0 else 0.0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default="jev-1.14.0", help="Box needs 1.14; jev-latest may be older")
    parser.add_argument("--no-box", action="store_true", help="skip the Box question (for Jev 1.13)")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    ref, picture = images.asset(square_png())
    questions = {
        "square": client.noul("Is there a white square in `picture`?"),
        "circle": client.noul("Is there a red circle in `picture`?"),
        "quadrant": client.choice("Which quadrant of `picture` holds the white square?",
                                  {"top_left": "Upper left quarter.", "top_right": "Upper right quarter.",
                                   "bottom_left": "Lower left quarter.", "bottom_right": "Lower right quarter."}),
    }
    if not args.no_box:
        questions["where"] = client.box("the white square in `picture`")
    report: dict = {"model_asked": args.model, "checks": {}}
    try:
        out = client.ask({"picture": ref}, questions, assets={ref: picture}, model=args.model,
                         timeout=args.timeout, provider="typesafe")
    except client.JevError as error:
        report.update(ok=False, error=error.code, detail=str(error)[:300])
        print(json.dumps(report, indent=2))
        return 1
    answers = out["answers"]
    checks = report["checks"]
    checks["square_yes"] = {"noul": answers["square"]["noul"], "pass": answers["square"]["noul"] >= 0.7}
    checks["circle_no"] = {"noul": answers["circle"]["noul"], "pass": answers["circle"]["noul"] <= 0.3}
    checks["quadrant"] = {"choice": answers["quadrant"]["choice"], "confidence": answers["quadrant"]["confidence"],
                          "pass": answers["quadrant"]["choice"] == "top_right"}
    if "where" in answers:
        iou = overlap(answers["where"]["box"])
        checks["box"] = {"box": answers["where"]["box"], "confidence": answers["where"]["confidence"],
                         "iou": round(iou, 3), "pass": iou >= 0.5}
    report.update(ok=all(c["pass"] for c in checks.values()), jev_model=out.get("jev_model"),
                  input_tokens=out.get("input_tokens"), latency_ms=out.get("latency_ms"))
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

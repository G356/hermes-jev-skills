#!/usr/bin/env python3
"""Measure TypeSafe Jev on hand-labelled picture checks, before trusting the vision_cloud cut-off.

Input: JSONL, one check per line, as the Jev-Omni A/B was labelled:

    {"image": "/abs/path.png", "question": "Is the logo clipped?", "label": "No"}

``label`` is PASS/FAIL or Yes/No, matching the question's shape (cloud_vision asks the same
closed sets). Each picture is sent to TypeSafe, asked exactly as the shadow asks it, and the
report gives accuracy overall and, at each confidence cut-off, how many checks would be kept
local and how accurate those were — the numbers THRESHOLD in cloud_vision.py should come from.

The report holds counts and rates only: never a path, question or picture.

    python3 scripts/vision_cloud_ab.py labelled.jsonl [--model jev-1.14.0] [--out report.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jevkit import client, cloud_vision, images, omni_vision  # noqa: E402

CUTOFFS = (0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9)


def run(rows: List[Dict[str, Any]], model: Optional[str] = None,
        asker: Optional[Callable[..., Dict[str, Any]]] = None) -> Dict[str, Any]:
    asker = asker or client.ask
    results, skipped = [], {}
    for row in rows:
        shape = omni_vision.question_shape(row.get("question"))
        label = str(row.get("label") or "")
        if shape == "open" or label.lower() not in {o.lower() for o in omni_vision.OPTIONS.get(shape, ())}:
            skipped["shape_or_label"] = skipped.get("shape_or_label", 0) + 1
            continue
        if cloud_vision.privacy.is_sensitive(str(row.get("question") or "")):
            skipped["sensitive_question"] = skipped.get("sensitive_question", 0) + 1
            continue
        try:
            ref, picture = images.asset(row["image"])
        except images.ImageError as error:
            skipped[error.code] = skipped.get(error.code, 0) + 1
            continue
        except OSError:
            skipped["image_gone"] = skipped.get("image_gone", 0) + 1
            continue
        try:
            out = asker({"image": ref}, {"verdict": cloud_vision._question(row["question"], shape, ref)},
                        assets={ref: picture}, timeout=cloud_vision.TIMEOUT_S, provider="typesafe",
                        **({"model": model} if model else {}))
        except client.JevError as error:
            skipped[f"jev_{error.code}"] = skipped.get(f"jev_{error.code}", 0) + 1
            continue
        verdict = out["answers"]["verdict"]
        results.append({"correct": verdict["choice"].lower() == label.lower(),
                        "confidence": float(verdict["confidence"]), "tokens": out.get("input_tokens") or 0})
    n = len(results)
    report: Dict[str, Any] = {"asked": n, "skipped": skipped,
                              "accuracy": round(sum(r["correct"] for r in results) / n, 4) if n else None,
                              "input_tokens": sum(r["tokens"] for r in results), "cutoffs": []}
    for cut in CUTOFFS:
        kept = [r for r in results if r["confidence"] >= cut]
        report["cutoffs"].append({"cutoff": cut, "kept": len(kept), "coverage": round(len(kept) / n, 4) if n else None,
                                  "accuracy_kept": round(sum(r["correct"] for r in kept) / len(kept), 4)
                                  if kept else None})
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("labelled", type=Path)
    parser.add_argument("--model", default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    rows = [json.loads(line) for line in args.labelled.read_text(encoding="utf-8").splitlines() if line.strip()]
    report = run(rows, model=args.model)
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["asked"] else 1


if __name__ == "__main__":
    sys.exit(main())

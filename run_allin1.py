#!/usr/bin/env python3
"""
run_allin1.py  —  Run All-In-One structure analysis and write a JSON sidecar.

Invoked by pipeline.py via venv_demucs:
    venv_demucs/bin/python3.11 run_allin1.py -i stabilised.wav -o sections.json

Writes a JSON array:
    [{"start": 0.0, "end": 14.3, "label": "Intro"}, ...]

Returns exit code 0 even on failure — sections are an enhancement, never
a blocker.  On failure it writes an empty array so chord_chart_render.py
can always read the file.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile


_LABEL_MAP = {
    "start":  "Intro",
    "end":    "Outro",
    "intro":  "Intro",
    "verse":  "Verse",
    "chorus": "Chorus",
    "bridge": "Bridge",
    "outro":  "Outro",
    "break":  "Break",
    "solo":   "Solo",
    "inst":   "Inst",
}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("-i", "--input",  required=True)
    p.add_argument("-o", "--output", required=True)
    args = p.parse_args()

    def bail(msg: str) -> None:
        print(f"[allin1] {msg}", flush=True)
        with open(args.output, "w") as f:
            json.dump([], f)
        sys.exit(0)

    try:
        import allin1  # noqa: PLC0415
    except ImportError:
        bail("not installed — skipping section detection")

    try:
        import torch
        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"
    except Exception:
        device = "cpu"
    print(f"[allin1] Analysing structure (device={device}) …", flush=True)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            result = allin1.analyze(
                args.input,
                out_dir=tmp,
                keep_byproducts=False,
                device=device,
            )
    except Exception as e:
        bail(f"analysis failed: {e}")
        return  # unreachable, but keeps type checkers happy

    segments = []
    for s in result.segments:
        raw = str(s.label).lower()
        label = _LABEL_MAP.get(raw, raw.capitalize())
        segments.append({"start": s.start, "end": s.end, "label": label})

    with open(args.output, "w") as f:
        json.dump(segments, f)

    print(f"[allin1] {len(segments)} segment(s) written → {args.output}", flush=True)


if __name__ == "__main__":
    main()

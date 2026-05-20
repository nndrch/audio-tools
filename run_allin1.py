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
import os
import sys
import tempfile

# Tell HuggingFace Hub to use only cached files — no network calls.
# This prevents failures on servers that cannot reach huggingface.co.
# The models must already be cached (run download_allin1_models.py once).
# Override by setting HF_HUB_OFFLINE=0 in the environment if you want
# the hub to check for updates.
if "HF_HUB_OFFLINE" not in os.environ:
    os.environ["HF_HUB_OFFLINE"] = "1"

# madmom's compiled Cython (hmm.pyx) still uses the removed np.int / np.float
# aliases.  Restore them before allin1 imports madmom so the runtime lookup
# doesn't fail.  These are safe no-op aliases to the built-in types.
try:
    import numpy as _np
    for _alias, _builtin in (
        ("int",     int),
        ("float",   float),
        ("complex", complex),
        ("bool",    bool),
        ("object",  object),
        ("str",     str),
    ):
        if not hasattr(_np, _alias):
            setattr(_np, _alias, _builtin)
except Exception:
    pass


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
    p.add_argument(
        "--section-threshold", type=float, default=0.0, dest="section_threshold",
        help=(
            "Minimum boundary-strength score required to accept a section cut "
            "(default: 0.0 = accept every local peak). Higher values produce fewer "
            "but more confident sections. Typical useful range: 0.0 – 0.5."
        ),
    )
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

    # ── Section-threshold patch ───────────────────────────────────────────────
    # allin1's postprocess_functional_structure hard-codes the boundary-strength
    # threshold as `boundary_candidates > 0.0`.  When --section-threshold is
    # non-zero we replace that function with an identical copy that uses the
    # requested threshold instead, giving callers control over section density.
    if args.section_threshold != 0.0:
        _thr = float(args.section_threshold)
        import numpy as _np
        import torch as _torch
        from allin1.postprocessing.helpers import (
            local_maxima as _local_maxima,
            peak_picking as _peak_picking,
            event_frames_to_time as _event_frames_to_time,
        )
        from allin1.config import HARMONIX_LABELS as _HARMONIX_LABELS
        from allin1.typings import Segment as _Segment

        def _patched_postprocess(logits, cfg):
            raw_prob_sections  = _torch.sigmoid(logits.logits_section[0])
            raw_prob_functions = _torch.softmax(logits.logits_function[0], dim=0)
            prob_sections, _   = _local_maxima(raw_prob_sections, filter_size=4 * cfg.min_hops_per_beat + 1)
            prob_sections  = prob_sections.cpu().numpy()
            prob_functions = raw_prob_functions.cpu().numpy()

            boundary_candidates = _peak_picking(
                boundary_activation=prob_sections,
                window_past=12 * cfg.fps,
                window_future=12 * cfg.fps,
            )
            boundary = boundary_candidates > _thr  # ← custom threshold

            duration = len(prob_sections) * cfg.hop_size / cfg.sample_rate
            pred_boundary_times = _event_frames_to_time(boundary, cfg)
            if pred_boundary_times[0] != 0:
                pred_boundary_times = _np.insert(pred_boundary_times, 0, 0)
            if pred_boundary_times[-1] != duration:
                pred_boundary_times = _np.append(pred_boundary_times, duration)
            pred_boundaries = _np.stack([pred_boundary_times[:-1], pred_boundary_times[1:]]).T

            pred_boundary_indices = _np.flatnonzero(boundary)
            pred_boundary_indices = pred_boundary_indices[pred_boundary_indices > 0]
            prob_segment_function = _np.split(prob_functions, pred_boundary_indices, axis=1)
            pred_labels = [p.mean(axis=1).argmax().item() for p in prob_segment_function]

            segments = []
            for (start, end), label in zip(pred_boundaries, pred_labels):
                segments.append(_Segment(start=start, end=end, label=_HARMONIX_LABELS[label]))
            return segments

        import allin1.helpers as _allin1_helpers
        _allin1_helpers.postprocess_functional_structure = _patched_postprocess
        print(f"[allin1] section_threshold={_thr}", flush=True)

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

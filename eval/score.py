#!/usr/bin/env python3
"""
eval/score.py — MIREX-style weighted chord-symbol recall via mir_eval.

Scores a detected chord sequence (.lab) against a ground-truth annotation
(.lab), using duration-weighted recall over several comparison levels. This is
the Phase-0 measurement gate from docs/chord-detection-implementation-plan.md:
no accuracy change ships without moving these numbers.

Label format (Harte / `.lab`): tab- or space-separated `start  end  label`,
one segment per line, times in seconds, label like `C:maj`, `A:min7`, `G:7`,
or `N` (no chord). This is exactly what `chord_chart_render.py --lab-out`
emits, so detected and reference files are directly comparable.

Metrics reported (all duration-weighted recall in [0, 1], higher is better):
  root      — correct root, quality ignored
  majmin    — correct root + major/minor third  (the headline pop/rock metric)
  sevenths  — correct root + triad + 7th         ("majmin7")
  mirex     — MIREX "at least 3 shared pitch classes" criterion
  seg       — segmentation quality (under/over-segmentation combined)

Usage:
    # one pair
    ./venv_crema/bin/python3.11 eval/score.py ref.lab est.lab
    ./venv_crema/bin/python3.11 eval/score.py ref.lab est.lab --json

    # importable
    from score import score_pair
    metrics = score_pair("ref.lab", "est.lab")

Runs in venv_crema (where mir_eval is installed via requirements_crema.txt).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# The comparison levels we surface, in report order. mir_eval.chord.evaluate
# computes many more; these are the ones that matter for the target material.
REPORTED_METRICS = ("root", "majmin", "sevenths", "mirex", "seg")


def load_lab(path: str):
    """Load a Harte .lab file → (intervals (N,2) float, labels list[str]).

    Uses mir_eval's loader so whitespace handling and ordering match the
    scorer exactly.
    """
    import mir_eval

    intervals, labels = mir_eval.io.load_labeled_intervals(path)
    return intervals, labels


def score_pair(ref_lab: str, est_lab: str) -> dict:
    """Score one detected .lab against one reference .lab.

    Returns a dict of {metric: weighted_recall} for REPORTED_METRICS, plus
    `duration` (the scored span in seconds) so callers can duration-weight an
    aggregate across songs. Reference and estimate are trimmed/padded to their
    common time span and gaps filled with 'N' by mir_eval, so partial coverage
    is handled gracefully.
    """
    import mir_eval

    ref_intervals, ref_labels = load_lab(ref_lab)
    est_intervals, est_labels = load_lab(est_lab)

    # Align both annotations to a common [t_min, t_max] span, filling any gaps
    # with "N" (no-chord) so the two sequences are directly comparable.
    t_min = float(min(ref_intervals.min(), est_intervals.min()))
    t_max = float(max(ref_intervals.max(), est_intervals.max()))

    ref_intervals, ref_labels = mir_eval.util.adjust_intervals(
        ref_intervals, ref_labels, t_min, t_max,
        mir_eval.chord.NO_CHORD, mir_eval.chord.NO_CHORD,
    )
    est_intervals, est_labels = mir_eval.util.adjust_intervals(
        est_intervals, est_labels, t_min, t_max,
        mir_eval.chord.NO_CHORD, mir_eval.chord.NO_CHORD,
    )

    full = mir_eval.chord.evaluate(
        ref_intervals, ref_labels, est_intervals, est_labels,
    )
    out = {m: round(float(full[m]), 4) for m in REPORTED_METRICS if m in full}
    out["duration"] = round(t_max - t_min, 3)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="MIREX-style weighted chord recall (mir_eval).")
    p.add_argument("ref", help="Ground-truth .lab")
    p.add_argument("est", help="Detected .lab (e.g. from chord_chart_render --lab-out)")
    p.add_argument("--json", action="store_true", help="Emit one JSON line instead of a table")
    args = p.parse_args()

    for f in (args.ref, args.est):
        if not os.path.isfile(f):
            sys.exit(f"File not found: {f}")

    metrics = score_pair(args.ref, args.est)

    if args.json:
        sys.stdout.write(json.dumps(metrics) + "\n")
        return

    print(f"\n  ref: {args.ref}\n  est: {args.est}\n")
    for m in REPORTED_METRICS:
        if m in metrics:
            print(f"    {m:10}  {metrics[m]:.3f}")
    print(f"    {'duration':10}  {metrics['duration']:.1f}s\n")


if __name__ == "__main__":
    main()

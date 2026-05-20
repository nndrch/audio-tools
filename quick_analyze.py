#!/usr/bin/env python3
"""
quick_analyze.py — Fast metadata detection for the upload preview.

Goal: pre-fill the web UI's Song info panel within a few seconds of the user
dropping a file, so they can confirm or override before the slow full
pipeline runs. We deliberately keep this lightweight — no madmom, no
downbeat tracker, no crema. Just librosa for beat tracking + autocorrelation
for meter + chroma-based Krumhansl-Schmuckler for key.

Output: one JSON line on stdout:

    {
      "bpm":              92,                 # int rounded; null on failure
      "key":              "f:minor",          # LilyPond-style or null
      "time_sig":         "4/4",              # e.g. "4/4", "3/4", "6/8"; null
      "duration_seconds": 187.4,
    }

Runs in the system Python (the one with the beat-stabilizer deps), since
all three detectors only need numpy / librosa / soundfile.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings

# Suppress librosa's deprecation chatter so the JSON line stays the only
# thing on stdout.
warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from chord_sheet import load_audio_mono, detect_time_signature
from chord_chart_render import detect_key_candidates, _use_sharps


# Dropdown values use specific enharmonic spellings — pick whichever name the
# Song info <select> includes so the dropdown actually picks up the detected
# key instead of falling back to "auto".
#
# Major: prefer flats for ♭2, ♭3, ♭6, ♭7 (Db, Eb, Ab, Bb); F# is the one ♯ pick.
# Minor: prefer sharps for #2, #5, #7 (C#m, F#m, G#m); flats for Ebm, Bbm.
_DROPDOWN_LY_MAJOR = {
    0:  "c",   1:  "des", 2:  "d",   3:  "ees",
    4:  "e",   5:  "f",   6:  "fis", 7:  "g",
    8:  "aes", 9:  "a",   10: "bes", 11: "b",
}
_DROPDOWN_LY_MINOR = {
    0:  "c",   1:  "cis", 2:  "d",   3:  "ees",
    4:  "e",   5:  "f",   6:  "fis", 7:  "g",
    8:  "gis", 9:  "a",   10: "bes", 11: "b",
}


def detect_bpm(y: np.ndarray, sr: int) -> tuple[float | None, np.ndarray]:
    """Return (bpm, beat_times) using librosa's default beat tracker.

    Includes an octave correction: librosa commonly locks onto 8th notes for
    half-time grooves, returning double the quarter-note tempo.  We detect
    this by checking whether the detected beats alternate between strong and
    weak onsets — the hallmark of 8th-note tracking.  If a clear alternating
    pattern is found (one set of every-other beats is ≥ 25 % stronger on
    average), we halve the BPM and keep only the stronger beat set.
    """
    import librosa

    try:
        hop = 512
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)

        tempo, beat_frames = librosa.beat.beat_track(
            onset_envelope=onset_env, sr=sr, units="frames", hop_length=hop
        )
        beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop)
        bpm_val = float(np.atleast_1d(tempo)[0]) if tempo is not None else None

        # Octave correction — only worth checking when BPM is high enough that
        # double-tracking is plausible, and we have enough beats to compare.
        if bpm_val is not None and bpm_val > 120 and len(beat_times) >= 8:
            idxs = np.clip(
                librosa.time_to_frames(beat_times, sr=sr, hop_length=hop),
                0, len(onset_env) - 1,
            ).astype(int)
            strengths  = onset_env[idxs]
            even_mean  = float(np.mean(strengths[::2]))   # beats 0, 2, 4, …
            odd_mean   = float(np.mean(strengths[1::2]))  # beats 1, 3, 5, …
            stronger   = max(even_mean, odd_mean)
            weaker     = min(even_mean, odd_mean)

            # If one set is clearly stronger (≥ 25 % gap), the tracker found
            # 8th notes — the "strong" set is the true quarter-note pulse.
            if weaker > 0 and stronger / weaker >= 1.25:
                bpm_val    = bpm_val / 2.0
                beat_times = beat_times[::2] if even_mean >= odd_mean else beat_times[1::2]

        return bpm_val, np.asarray(beat_times, dtype=float)
    except Exception:
        return None, np.zeros(0, dtype=float)


def detect_key_dropdown(y: np.ndarray, sr: int) -> str | None:
    """Return a LilyPond-style key string matching the web Song-info dropdown."""
    try:
        cands = detect_key_candidates(y, sr, n=1)
    except Exception:
        return None
    if not cands:
        return None
    _score, root, mode = cands[0]
    if mode not in ("major", "minor"):
        return None
    table = _DROPDOWN_LY_MAJOR if mode == "major" else _DROPDOWN_LY_MINOR
    return f"{table[root]}:{mode}"


def detect_meter(y: np.ndarray, sr: int, beat_times: np.ndarray) -> str | None:
    """Return a time-signature string ('4/4', '3/4', '6/8'), or None."""
    if beat_times.size < 4:
        return None
    try:
        bpb = detect_time_signature(y, sr, beat_times)
    except Exception:
        return None
    # detect_time_signature returns 6 as a sentinel for compound duple (6/8).
    if bpb == 6:
        return "6/8"
    if bpb in (2, 3, 4, 5):
        return f"{bpb}/4"
    return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fast metadata detection for the upload preview.")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("--sample-rate", type=int, default=22050,
                   help="Internal sample rate. Lower = faster but slightly less accurate.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not os.path.isfile(args.input):
        sys.exit(f"File not found: {args.input}")

    # load_audio_mono downsamples + mixes to mono in one pass.
    y, sr = load_audio_mono(args.input, sr=args.sample_rate)
    duration = float(len(y) / sr) if len(y) else 0.0

    bpm, beat_times = detect_bpm(y, sr)
    key_str         = detect_key_dropdown(y, sr)
    time_sig        = detect_meter(y, sr, beat_times)

    out = {
        "bpm":              round(bpm) if bpm and bpm > 0 else None,
        "key":              key_str,
        "time_sig":         time_sig,
        "duration_seconds": round(duration, 1),
    }
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()

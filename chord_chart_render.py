#!/usr/bin/env python3
"""
chord_chart_render.py  —  Visual PDF chord chart from an audio file.

Detects chords via crema, aligns them to a beat grid, and renders a
LilyPond lead-sheet PDF with chord symbols above blank staff lines.

If the input file has a companion <file>.bpm sidecar (written by
beat_stabilizer.py), that BPM is used automatically — no need to pass
--bpm manually after running the stabiliser.

Run with the crema venv:
    ./venv_crema/bin/python3.11 chord_chart_render.py -i song.wav
    ./venv_crema/bin/python3.11 chord_chart_render.py -i song.wav --key "f:minor" --title "My Song"
    ./venv_crema/bin/python3.11 chord_chart_render.py -i song.wav --no-bpm --bars-per-line 4
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from collections import Counter

import numpy as np


# ---------------------------------------------------------------------------
# Progress reporting (enabled by --progress-json; no-op otherwise)
# ---------------------------------------------------------------------------

_PROGRESS_JSON = False


def _emit(sub: str, pct: float, msg: str | None = None) -> None:
    """Emit a single PROGRESS JSON line on stdout (local 0.0–1.0)."""
    if not _PROGRESS_JSON:
        return
    payload = {"sub": sub, "pct": float(pct)}
    if msg:
        payload["msg"] = msg
    sys.stdout.write(f"PROGRESS {json.dumps(payload)}\n")
    sys.stdout.flush()

sys.path.insert(0, os.path.dirname(__file__))
from chord_sheet import (
    load_audio_mono,
    detect_chords_crema,
    detect_beats,
    detect_time_signature,
    beat_sync_chords,
    CONFIDENCE_WARN,
)


# ---------------------------------------------------------------------------
# Tunable detection thresholds
#
# Edit these values to change behaviour globally.  Every CLI flag that
# controls a threshold uses the matching constant as its default, so
# changing a constant here is equivalent to always passing that flag.
# ---------------------------------------------------------------------------

# Mid-bar chord splits: minimum crema confidence for a within-bar chord
# change to appear in the chart.  Below this the bar keeps its beat-1 anchor.
MID_BAR_THRESHOLD: float = 0.80

# madmom bar fallback: bars whose *mean* crema confidence falls below this
# value are re-evaluated with madmom's bar-window time-weighted chord vote.
MADMOM_THRESHOLD: float = 0.70

# Key snapping: low-confidence bars whose chord is non-diatonic to the
# detected key are snapped to the nearest diatonic equivalent.
# Set to 0.0 to effectively disable without removing the --key-snap flag.
KEY_SNAP_THRESHOLD: float = 0.65

# Low-confidence marker: segments below this are flagged '?' in the
# terminal summary and counted in the JSON report.
# Mirrors CONFIDENCE_WARN from chord_sheet.py (kept here for quick reference).
CONFIDENCE_WARN_THRESHOLD: float = CONFIDENCE_WARN  # 0.45


# ---------------------------------------------------------------------------
# BPM sidecar
# ---------------------------------------------------------------------------

def read_bpm_sidecar(audio_path: str) -> float | None:
    """Return BPM from <audio_path>.bpm if it exists, else None."""
    sidecar = audio_path + ".bpm"
    if os.path.isfile(sidecar):
        try:
            bpm = float(open(sidecar).read().strip())
            print(f"  BPM sidecar found: {bpm} BPM")
            return bpm
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Crema label → LilyPond conversion
# ---------------------------------------------------------------------------

_ROOT_TO_LY = {
    "C":  "c",   "C#": "cis",  "Db": "des",
    "D":  "d",   "D#": "dis",  "Eb": "ees",
    "E":  "e",   "Fb": "e",
    "F":  "f",   "F#": "fis",  "Gb": "ges",
    "G":  "g",   "G#": "gis",  "Ab": "aes",
    "A":  "a",   "A#": "ais",  "Bb": "bes",
    "B":  "b",   "Cb": "b",
}

_QUALITY_TO_LY = {
    "maj":     "",       "min":     ":m",
    "7":       ":7",     "maj7":    ":maj7",
    "min7":    ":m7",    "dim":     ":dim",
    "dim7":    ":dim7",  "hdim7":   ":m7.5-",
    "aug":     ":aug",   "sus2":    ":sus2",
    "sus4":    ":sus4",  "maj6":    ":6",
    "min6":    ":m6",    "minmaj7": ":m7+",
}

_QUALITY_DISPLAY = {
    "maj":     "",       "min":     "m",
    "7":       "7",      "maj7":    "maj7",
    "min7":    "m7",     "dim":     "dim",
    "dim7":    "dim7",   "hdim7":   "ø7",
    "aug":     "aug",    "sus2":    "sus2",
    "sus4":    "sus4",   "maj6":    "6",
    "min6":    "m6",     "minmaj7": "mM7",
}

# Collapse extended/altered qualities down to plain major or minor
_QUALITY_TO_SIMPLE = {
    "maj": "maj",    "7": "maj",      "maj7": "maj",   "maj6": "maj",
    "aug": "maj",    "sus2": "maj",   "sus4": "maj",
    "min": "min",    "min7": "min",   "dim": "min",    "dim7": "min",
    "hdim7": "min",  "min6": "min",   "minmaj7": "min",
}


def simplify_chord(label: str, add_7th: bool = False) -> str:
    """Reduce any chord to plain major or minor, optionally keeping 7th qualities."""
    if label in ("N", "X", ""):
        return label
    root, quality = label.split(":", 1) if ":" in label else (label, "maj")
    if add_7th and quality in ("maj7", "min7", "7"):
        return label
    base = _QUALITY_TO_SIMPLE.get(quality, "maj")
    return f"{root}:min" if base == "min" else f"{root}:maj"


def crema_to_ly(label: str, use_sharps: bool = False) -> tuple[str, str]:
    if label in ("N", "X", ""):
        return ("s", "")
    root, quality = label.split(":", 1) if ":" in label else (label, "maj")
    # Normalise to the key's accidental policy before looking up the LilyPond name.
    if use_sharps:
        root = _FLAT_TO_SHARP_ROOT.get(root, root)
    else:
        root = _SHARP_TO_FLAT_ROOT.get(root, root)
    return (_ROOT_TO_LY.get(root, root.lower()), _QUALITY_TO_LY.get(quality, f":{quality}"))


def crema_to_display(label: str, use_sharps: bool = False) -> str:
    if label in ("N", "X", ""):
        return ""
    root, quality = label.split(":", 1) if ":" in label else (label, "maj")
    if use_sharps:
        display_root = _FLAT_TO_SHARP_ROOT.get(root, root)
    else:
        display_root = _SHARP_TO_FLAT_ROOT.get(root, root)
    return f"{display_root}{_QUALITY_DISPLAY.get(quality, quality)}"


# ---------------------------------------------------------------------------
# Beat data → bar-level chord structures
# ---------------------------------------------------------------------------

def find_bar_phase(beat_chords: list[dict], beats_per_bar: int) -> int:
    """
    Try all beat offsets (0 … beats_per_bar-1) and return the one that places
    the most chord changes exactly on bar boundaries rather than within bars.
    """
    best_phase, best_score = 0, -1.0
    for phase in range(beats_per_bar):
        chords = [b["chord"] for b in beat_chords[phase:]]
        at_boundary = within_bar = 0
        for i in range(1, len(chords)):
            if chords[i] != chords[i - 1]:
                if i % beats_per_bar == 0:
                    at_boundary += 1
                else:
                    within_bar += 1
        total = at_boundary + within_bar
        score = at_boundary / total if total > 0 else 0.0
        if score > best_score:
            best_score = score
            best_phase = phase
    return best_phase



_BEAT_TO_DUR = {1: "4", 2: "2", 3: "2.", 4: "1"}


def _ly_chord_token(label: str, beats: int, use_sharps: bool = False) -> str:
    root, qual = crema_to_ly(label, use_sharps)
    if root == "s":
        return " ".join("s4" for _ in range(beats))
    return f"{root}{_BEAT_TO_DUR.get(beats, '4')}{qual}"


def hybrid_bar_chords(
    beat_chords: list[dict],
    beats_per_bar: int,
    mid_bar_threshold: float = 0.80,
) -> list[dict]:
    """
    Collapses beat-level chords to bars. Beat 1 is always the anchor chord.
    Additional chord changes within a bar are included only when their confidence
    >= mid_bar_threshold. Each bar contains a 'segments' list of runs.
    """
    bars: list[dict] = []
    # A chord that occupies only the last beat of a bar is an anticipation of the
    # next bar's chord.  Rather than erasing it (old behaviour) or showing it on
    # beat 4 (where it physically lives but doesn't rhythmically belong), we push
    # it forward: it becomes the downbeat chord of the *next* bar.  This reflects
    # the performer's stylistic choice without over-encoding it into the chart.
    pending_anticipation: str | None = None

    for i in range(0, len(beat_chords), beats_per_bar):
        group = beat_chords[i : i + beats_per_bar]
        if not group:
            continue

        # If the previous bar ended with an anticipation, override beat 1 here.
        if pending_anticipation is not None:
            group = [{**group[0], "chord": pending_anticipation}, *group[1:]]
            pending_anticipation = None

        current_chord = group[0]["chord"]
        run_start = 0
        segments = []

        for j in range(1, len(group)):
            b = group[j]
            if b["chord"] != current_chord and b["confidence"] >= mid_bar_threshold:
                run = group[run_start:j]
                segments.append({
                    "chord":      current_chord,
                    "beats":      len(run),
                    "confidence": round(sum(x["confidence"] for x in run) / len(run), 3),
                    "time":       run[0]["time"],
                })
                current_chord = b["chord"]
                run_start = j

        run = group[run_start:]
        segments.append({
            "chord":      current_chord,
            "beats":      len(run),
            "confidence": round(sum(x["confidence"] for x in run) / len(run), 3),
            "time":       run[0]["time"],
        })

        # Detect a 1-beat anticipation at the end of this bar and push it forward.
        if len(segments) > 1 and segments[-1]["beats"] == 1:
            pending_anticipation = segments[-1]["chord"]
            # Extend the preceding segment to fill the bar cleanly.
            segments[-2] = {**segments[-2], "beats": segments[-2]["beats"] + 1}
            segments.pop()

        bars.append({
            "bar":        len(bars) + 1,
            "beat":       group[0]["beat"],
            "time":       group[0]["time"],
            "chord":      group[0]["chord"],
            "confidence": segments[0]["confidence"],
            "segments":   segments,
        })
    return bars


# ---------------------------------------------------------------------------
# madmom bar-level fallback  (optional, --madmom-fallback)
# ---------------------------------------------------------------------------

def madmom_fallback_bars(
    bar_chords: list[dict],
    segments: list[tuple[float, float, str]],
    beats_per_bar: int,
    beat_interval: float,
    confidence_threshold: float = MADMOM_THRESHOLD,
    add_7th: bool = False,
    use_sharps: bool = False,
) -> tuple[list[dict], list[int]]:
    """
    For each bar whose mean crema confidence < confidence_threshold, replace
    its entire content with madmom's time-weighted chord vote over the full
    bar window (always a single chord — no mid-bar splits).

    madmom segment boundaries do not align to the beat grid, so splitting
    within a bar produces inaccurate beat counts.  We let crema handle any
    genuine mid-bar changes; madmom is only used to correct the main chord.

    Returns (new_bar_chords, list_of_substituted_bar_numbers).
    """
    n_bars = len(bar_chords)
    new_bars: list[dict] = []
    substituted: list[int] = []

    for i, bar in enumerate(bar_chords):
        mean_conf = float(np.mean([seg["confidence"] for seg in bar["segments"]]))

        if mean_conf >= confidence_threshold:
            new_bars.append(bar)
            continue

        # Full bar time window
        t_start = bar["time"]
        t_end   = (bar_chords[i + 1]["time"] if i + 1 < n_bars
                   else t_start + beats_per_bar * beat_interval)
        bar_dur = t_end - t_start

        # Time-weighted chord coverage across the whole bar
        coverage: dict[str, float] = {}
        for seg_s, seg_e, label in segments:
            if label == "N":
                continue
            overlap = max(0.0, min(seg_e, t_end) - max(seg_s, t_start))
            if overlap > 0:
                chord = simplify_chord(label, add_7th=add_7th)
                coverage[chord] = coverage.get(chord, 0.0) + overlap

        if not coverage:
            new_bars.append(bar)
            continue

        top_chord, top_time = max(coverage.items(), key=lambda x: x[1])
        conf = round(top_time / bar_dur, 3)
        original = crema_to_display(bar["chord"], use_sharps)

        new_segs = [{"chord": top_chord, "beats": beats_per_bar,
                     "confidence": conf, "time": t_start}]
        new_bar = {**bar, "chord": top_chord, "confidence": conf, "segments": new_segs}

        print(f"    Bar {bar['bar']:>3}  {t_start:>6.1f}s  "
              f"{original:<8} ({mean_conf:.0%}) → {crema_to_display(top_chord, use_sharps)}  [madmom]")
        substituted.append(bar["bar"])
        new_bars.append(new_bar)

    return new_bars, substituted


# ---------------------------------------------------------------------------
# Key-constrained snapping
# ---------------------------------------------------------------------------

# Semitone numbers for all root names used by crema / simplify_chord
_ROOT_TO_SEMITONE: dict[str, int] = {
    "C": 0,  "C#": 1,  "Db": 1,
    "D": 2,  "D#": 3,  "Eb": 3,
    "E": 4,  "Fb": 4,
    "F": 5,  "F#": 6,  "Gb": 6,
    "G": 7,  "G#": 8,  "Ab": 8,
    "A": 9,  "A#": 10, "Bb": 10,
    "B": 11, "Cb": 11,
}

# Canonical root spellings — flat (default) and sharp variants.
# Used when rebuilding chord labels from semitone numbers (e.g. after key snapping).
_SEMITONE_TO_ROOT_FLAT: dict[int, str] = {
    0: "C", 1: "Db", 2: "D", 3: "Eb", 4: "E", 5: "F",
    6: "Gb", 7: "G", 8: "Ab", 9: "A", 10: "Bb", 11: "B",
}
_SEMITONE_TO_ROOT_SHARP: dict[int, str] = {
    0: "C", 1: "C#", 2: "D", 3: "D#", 4: "E", 5: "F",
    6: "F#", 7: "G", 8: "G#", 9: "A", 10: "A#", 11: "B",
}
# Backward-compat alias
_SEMITONE_TO_ROOT = _SEMITONE_TO_ROOT_FLAT

# Enharmonic respelling tables (applied when normalising to key's accidental policy).
_FLAT_TO_SHARP_ROOT: dict[str, str] = {
    "Db": "C#", "Eb": "D#", "Fb": "E", "Gb": "F#",
    "Ab": "G#", "Bb": "A#", "Cb": "B",
}
_SHARP_TO_FLAT_ROOT: dict[str, str] = {
    "C#": "Db", "D#": "Eb", "E#": "F", "F#": "Gb",
    "G#": "Ab", "A#": "Bb", "B#": "C",
}

# Major keys (by tonic semitone) that use sharp accidentals.
# All others use flats.  Enharmonic pairs (C#/Db, F#/Gb) are assigned to sharps;
# the relative-minor rule then gives D#m/Ebm → sharps for D# but Eb → flats.
_SHARP_MAJOR_ROOTS: frozenset[int] = frozenset({0, 2, 4, 6, 7, 9, 11})
# C D E F# G A B  →  sharps
# F Bb Eb Ab Db Gb →  flats  (semitones 5, 10, 3, 8, 1 — everything else)


def _use_sharps(root_semitone: int, mode: str) -> bool:
    """
    Return True if this key conventionally uses sharp accidentals.

    For major keys: C, G, D, A, E, B, F# (and C#) → sharps.
    For minor keys: apply the relative-major rule (+3 semitones).
        Am (rel C) → sharps;  Dm (rel F) → flats;  Bm (rel D) → sharps, etc.
    """
    if mode == "major":
        return root_semitone in _SHARP_MAJOR_ROOTS
    else:
        rel_major = (root_semitone + 3) % 12
        return rel_major in _SHARP_MAJOR_ROOTS


# Diatonic scale degrees: (semitone offset from root, quality)
_MAJOR_INTERVALS = [(0,"maj"),(2,"min"),(4,"min"),(5,"maj"),(7,"maj"),(9,"min"),(11,"min")]
# Natural + harmonic minor union:
#   v (natural)  and V (harmonic) are both accepted — major V is by far the most
#   common chord in tonal minor-key music (e.g. G major in C minor).
#   vii° from harmonic minor is simplified to (11,"min") here because simplify_chord
#   collapses dim → min throughout the pipeline.
_MINOR_INTERVALS = [
    (0,"min"),  # i   — tonic
    (2,"min"),  # ii° — simplified to min
    (3,"maj"),  # III — bIII (aug in harmonic → still maj after simplification)
    (5,"min"),  # iv
    (7,"min"),  # v   — natural minor
    (7,"maj"),  # V   — harmonic minor (major dominant — very common!)
    (8,"maj"),  # VI  — bVI
    (10,"maj"), # VII — bVII
    (11,"min"), # vii°— harmonic minor leading tone (simplified to min)
]


def diatonic_set(root_semitone: int, mode: str) -> set[tuple[int, str]]:
    """Return the set of (semitone % 12, quality) pairs that are diatonic to key."""
    intervals = _MAJOR_INTERVALS if mode == "major" else _MINOR_INTERVALS
    return {((root_semitone + offset) % 12, qual) for offset, qual in intervals}


def _chord_semitone(label: str) -> tuple[int | None, str]:
    """Return (semitone, simple_quality) for a chord label; (None, '') for N/X."""
    if label in ("N", "X", ""):
        return None, ""
    root, quality = label.split(":", 1) if ":" in label else (label, "maj")
    return _ROOT_TO_SEMITONE.get(root), _QUALITY_TO_SIMPLE.get(quality, "maj")


def nearest_diatonic_chord(
    label: str,
    diatonic: set[tuple[int, str]],
    use_sharps: bool = False,
) -> str:
    """
    If label's (semitone, quality) pair is already in the diatonic set, return
    it unchanged.  Otherwise find the closest diatonic chord by root distance
    (circular semitone distance, tie-broken by preferring same quality) and
    return it as a canonical chord label spelled according to use_sharps.
    """
    semitone, qual = _chord_semitone(label)
    if semitone is None or (semitone, qual) in diatonic:
        return label

    def dist(s1: int, s2: int) -> int:
        d = abs(s1 - s2) % 12
        return min(d, 12 - d)

    best_semi, best_qual = min(
        diatonic,
        key=lambda x: (dist(semitone, x[0]), 0 if x[1] == qual else 1),
    )
    root_map = _SEMITONE_TO_ROOT_SHARP if use_sharps else _SEMITONE_TO_ROOT_FLAT
    suffix = ":min" if best_qual == "min" else ":maj"
    return f"{root_map[best_semi]}{suffix}"


def _parse_key_params(key_str: str) -> tuple[int, str]:
    """
    Parse a CLI key string (e.g. 'f#:minor', 'bes:major') into
    (root_semitone, mode) where mode is 'major' or 'minor'.
    """
    parts = (key_str + ":major").split(":")
    root_raw = parts[0].capitalize()
    mode_raw = parts[1].lower()
    # LilyPond-style flat names used in some inputs
    _ly_alias = {
        "Bes": "Bb", "Ees": "Eb", "Aes": "Ab", "Ges": "Gb",
        "Des": "Db", "Fes": "E",  "Ces": "B",
    }
    root = _ly_alias.get(root_raw, root_raw)
    semitone = _ROOT_TO_SEMITONE.get(root, 0)
    mode = "minor" if mode_raw in ("minor", "min", "m") else "major"
    return semitone, mode


def detect_key_candidates(
    y: np.ndarray, sr: int, n: int = 5,
) -> list[tuple[float, int, str]]:
    """
    Compute Krumhansl-Schmuckler correlations for all 24 keys and return
    the top-n candidates as [(score, root_semitone, mode), ...] sorted
    best-first.  Used by detect_key_from_chroma() and, optionally, by the
    chord-frequency tiebreaker.
    """
    import librosa

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_mean = chroma.mean(axis=1)  # shape (12,), index 0 = C

    # Krumhansl-Schmuckler profiles (C-rooted; rotated to transpose)
    major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                               2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                               2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

    candidates: list[tuple[float, int, str]] = []
    for root in range(12):
        rotated = np.roll(chroma_mean, -root)
        for profile, mode in [(major_profile, "major"), (minor_profile, "minor")]:
            score = float(np.corrcoef(rotated, profile)[0, 1])
            candidates.append((score, root, mode))

    candidates.sort(reverse=True)
    return candidates[:n]


def detect_key_from_chroma(y: np.ndarray, sr: int) -> tuple[int, str]:
    """
    Estimate the musical key from the audio chromagram using
    Krumhansl-Schmuckler key profiles.

    Returns (root_semitone, mode) where mode is 'major' or 'minor'.
    For disambiguation between closely related keys, combine with
    refine_key_by_chord_frequency() after chord detection.
    """
    score, root, mode = detect_key_candidates(y, sr, n=1)[0]
    return root, mode


def refine_key_by_chord_frequency(
    candidates: list[tuple[float, int, str]],
    bar_chords: list[dict],
) -> tuple[int, str]:
    """
    Disambiguate between closely related KS candidates using the actual
    detected chords.

    For each candidate key, compute a combined score:
        combined = ks_score * (1 + 0.5 * tonic_fraction)
    where tonic_fraction = (bars whose chord root matches the candidate
    tonic) / total bars.

    This resolves the common confusion between a major key and its
    relative or parallel minor (e.g. Bb major vs C minor) by boosting
    the candidate whose tonic is actually played most often.
    """
    if not bar_chords or not candidates:
        return candidates[0][1], candidates[0][2]

    # Count how many bars are rooted on each semitone
    root_counts: dict[int, int] = {}
    for bar in bar_chords:
        semitone, _ = _chord_semitone(bar["chord"])
        if semitone is not None:
            root_counts[semitone] = root_counts.get(semitone, 0) + 1
    total = len(bar_chords)

    best_combined, best_root, best_mode = -np.inf, 0, "major"
    for ks_score, root, mode in candidates:
        tonic_fraction = root_counts.get(root, 0) / total
        combined = ks_score * (1.0 + 0.5 * tonic_fraction)
        if combined > best_combined:
            best_combined, best_root, best_mode = combined, root, mode

    return best_root, best_mode


def guess_key_params(bar_chords: list[dict]) -> tuple[int, str]:
    """Return (root_semitone, mode) for the most-common chord in bar_chords."""
    roots = [lbl.split(":") for b in bar_chords if ":" in (lbl := b["chord"])]
    if not roots:
        return 0, "major"
    root, qual = Counter(tuple(r) for r in roots).most_common(1)[0][0]
    return _ROOT_TO_SEMITONE.get(root, 0), ("minor" if "min" in qual else "major")


def key_snap_bars(
    bar_chords: list[dict],
    root_semitone: int,
    mode: str,
    threshold: float = KEY_SNAP_THRESHOLD,
    use_sharps: bool = False,
) -> tuple[list[dict], list[int]]:
    """
    For every bar whose mean confidence < threshold, check each segment.
    Segments whose chord is non-diatonic to the key are snapped to the
    nearest diatonic equivalent.

    Returns (new_bar_chords, list_of_snapped_bar_numbers).
    """
    diatonic = diatonic_set(root_semitone, mode)
    new_bars: list[dict] = []
    snapped_bars: list[int] = []

    for bar in bar_chords:
        mean_conf = float(np.mean([seg["confidence"] for seg in bar["segments"]]))
        if mean_conf >= threshold:
            new_bars.append(bar)
            continue

        new_segs = []
        bar_changed = False
        for seg in bar["segments"]:
            label = seg["chord"]
            snapped = nearest_diatonic_chord(label, diatonic, use_sharps)
            if snapped != label:
                print(f"    Bar {bar['bar']:>3}  {seg['time']:>6.1f}s  "
                      f"{crema_to_display(label, use_sharps):<8} → "
                      f"{crema_to_display(snapped, use_sharps):<8}  "
                      f"({seg['confidence']:.0%})  [key snap]")
                seg = {**seg, "chord": snapped}
                bar_changed = True
            new_segs.append(seg)

        if bar_changed:
            snapped_bars.append(bar["bar"])
            new_bars.append({**bar, "chord": new_segs[0]["chord"], "segments": new_segs})
        else:
            new_bars.append(bar)

    return new_bars, snapped_bars


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

def key_params_to_ly_stmt(root_semitone: int, mode: str) -> str:
    """Convert (root_semitone, mode) → LilyPond \\key statement."""
    root_map = _SEMITONE_TO_ROOT_SHARP if _use_sharps(root_semitone, mode) else _SEMITONE_TO_ROOT_FLAT
    root = root_map[root_semitone]
    ly_root = _ROOT_TO_LY.get(root, root.lower())
    return f"\\key {ly_root} \\{mode}"


def key_params_to_display(root_semitone: int, mode: str) -> str:
    """Convert (root_semitone, mode) → human-readable key string e.g. 'Cm', 'F#'."""
    root_map = _SEMITONE_TO_ROOT_SHARP if _use_sharps(root_semitone, mode) else _SEMITONE_TO_ROOT_FLAT
    root = root_map[root_semitone]
    return f"{root}{'m' if mode == 'minor' else ''}"


def _ly_key(key_str: str) -> str:
    root, mode = (key_str + ":major").split(":")[:2]
    ly_root = _ROOT_TO_LY.get(root.capitalize(), root.lower())
    return f"\\key {ly_root} \\{mode}"


def guess_key(beat_chords: list[dict]) -> str:
    roots = [lbl.split(":") for b in beat_chords if ":" in (lbl := b["chord"])]
    if not roots:
        return "\\key c \\major"
    root, qual = Counter(tuple(r) for r in roots).most_common(1)[0][0]
    return f"\\key {_ROOT_TO_LY.get(root, root.lower())} \\{'minor' if 'min' in qual else 'major'}"


def guess_key_display(beat_chords: list[dict]) -> str:
    roots = [lbl.split(":") for b in beat_chords if ":" in (lbl := b["chord"])]
    if not roots:
        return "C"
    root, qual = Counter(tuple(r) for r in roots).most_common(1)[0][0]
    display_root = {"C#": "Db", "D#": "Eb", "F#": "Gb", "G#": "Ab", "A#": "Bb"}.get(root, root)
    return f"{display_root}{'m' if 'min' in qual else ''}"


def key_override_to_display(key_str: str) -> str:
    root, mode = (key_str + ":major").split(":")[:2]
    _LY_TO_DISPLAY = {
        "c": "C", "des": "Db", "d": "D", "ees": "Eb", "e": "E",
        "f": "F", "ges": "Gb", "g": "G", "aes": "Ab", "a": "A",
        "bes": "Bb", "b": "B",
    }
    return f"{_LY_TO_DISPLAY.get(root.lower(), root.capitalize())}{'m' if 'minor' in mode else ''}"


# ---------------------------------------------------------------------------
# Structural segmentation (allin1)
# ---------------------------------------------------------------------------
#
# allin1 runs as a venv_demucs subprocess in pipeline.py and writes a JSON
# sidecar with named labels (Intro / Verse / Chorus / Bridge / Outro …).
# Here we just read that sidecar and snap every boundary to the nearest
# bar start from the madmom-derived beat grid.

def detect_sections(
    sections_json: str,
    bar_chords: list[dict],
) -> list[dict]:
    """
    Load allin1 output from `sections_json` and return section descriptors
    snapped to bar boundaries:

        [{"label": "Chorus", "start_bar": 9, "end_bar": 16,
          "start_time": 16.0, "end_time": 32.1}, ...]

    Returns [] on any error — sections are an enhancement, never a blocker.
    """
    if not bar_chords or not sections_json:
        return []

    import json as _json

    if not os.path.isfile(sections_json):
        print(f"  [sections] JSON not found: {sections_json}")
        return []

    try:
        with open(sections_json) as f:
            raw_segments: list[dict] = _json.load(f)
    except Exception as e:
        print(f"  [sections] failed to read sections JSON: {e}")
        return []

    if not raw_segments:
        return []

    bar_times = [b["time"] for b in bar_chords]
    final_t = bar_times[-1] + (bar_times[-1] - bar_times[-2] if len(bar_times) >= 2 else 0.0)

    def snap_to_bar(t: float) -> int:
        """Return the 0-based bar index whose start time is closest to t."""
        best_idx, best_diff = 0, abs(bar_times[0] - t)
        for i, bt in enumerate(bar_times):
            d = abs(bt - t)
            if d < best_diff:
                best_diff, best_idx = d, i
        return best_idx

    sections: list[dict] = []
    for i, seg in enumerate(raw_segments):
        seg_start_t = float(seg["start"])
        seg_end_t   = float(seg.get("end", final_t))
        label       = str(seg.get("label", f"Part {i + 1}"))

        start_bar_idx = snap_to_bar(seg_start_t)
        if i + 1 < len(raw_segments):
            next_start_bar_idx = snap_to_bar(float(raw_segments[i + 1]["start"]))
            end_bar_idx = max(start_bar_idx, next_start_bar_idx - 1)
        else:
            end_bar_idx = len(bar_chords) - 1

        # Drop single-bar segments — they clutter the chart without adding signal.
        if end_bar_idx - start_bar_idx < 1:
            continue

        # Merge adjacent identical labels.
        if sections and sections[-1]["label"] == label and sections[-1]["end_bar"] == start_bar_idx:
            sections[-1]["end_bar"] = end_bar_idx + 1
            sections[-1]["end_time"] = seg_end_t
            continue

        sections.append({
            "label":      label,
            "start_bar":  start_bar_idx + 1,  # 1-indexed
            "end_bar":    end_bar_idx + 1,
            "start_time": round(seg_start_t, 2),
            "end_time":   round(seg_end_t, 2),
        })

    return sections


# ---------------------------------------------------------------------------
# MusicXML generation  (editable in MuseScore / Sibelius)
# ---------------------------------------------------------------------------
#
# music21's ChordSymbol expects flats spelled as "-" (e.g. "B-m7" for Bbm7) and
# rejects some labels the rest of the codebase emits ("ø7", "mM7" via parens,
# etc.).  _crema_to_m21_figure() translates between the two conventions.

# Quality suffix mapping for music21.harmony.ChordSymbol.figure
_QUALITY_TO_M21 = {
    "maj":     "",       "min":     "m",
    "7":       "7",      "maj7":    "maj7",
    "min7":    "m7",     "dim":     "dim",
    "dim7":    "dim7",   "hdim7":   "m7b5",
    "aug":     "aug",    "sus2":    "sus2",
    "sus4":    "sus4",   "maj6":    "6",
    "min6":    "m6",     "minmaj7": "mM7",
}


def _crema_to_m21_figure(label: str, use_sharps: bool = False) -> str | None:
    """
    Convert a crema-style chord label (e.g. 'Bb:min7', 'C#:maj7') into a
    music21 ChordSymbol figure string ('B-m7', 'C#maj7').  Returns None for
    N/X/empty labels which become rests in the score.
    """
    if label in ("N", "X", ""):
        return None
    root, quality = label.split(":", 1) if ":" in label else (label, "maj")
    # Normalise spelling to the key's accidental policy first.
    root = _FLAT_TO_SHARP_ROOT.get(root, root) if use_sharps else _SHARP_TO_FLAT_ROOT.get(root, root)
    # music21 uses "-" for flat, not "b"
    if root.endswith("b") and len(root) > 1:
        root = root[:-1] + "-"
    return root + _QUALITY_TO_M21.get(quality, "")


def bar_chords_to_musicxml(
    bar_chords: list[dict],
    beats_per_bar: int,
    key_root: int,
    key_mode: str,
    title: str,
    time_sig_str: str,
    use_sharps: bool = False,
    sections: list[dict] | None = None,
):
    """
    Build a music21 Score containing the chord chart as ChordSymbol events.
    Returns the Score; caller is responsible for `score.write('musicxml', path)`.

    Each segment lands at the correct beat offset within its measure, so
    mid-bar chord changes (when our --mid-bar-threshold gate lets them through)
    survive the export.

    Optional `sections` (from detect_sections) adds music21 RehearsalMark
    objects at the right measures so MuseScore / Sibelius show A / B / C
    boxes above the appropriate bars.
    """
    from music21 import stream, harmony, meter, key as m21key, metadata, note, expressions

    score = stream.Score()
    score.metadata = metadata.Metadata(title=title)

    part = stream.Part()

    # Key signature.  music21 wants the root spelled with "-" for flats.
    root_map = _SEMITONE_TO_ROOT_SHARP if use_sharps else _SEMITONE_TO_ROOT_FLAT
    key_root_name = root_map[key_root]
    if key_root_name.endswith("b") and len(key_root_name) > 1:
        key_root_name = key_root_name[:-1] + "-"
    # music21.key.Key uses lowercase for minor, capital for major
    key_tonic = key_root_name if key_mode == "major" else key_root_name.lower()
    part.append(m21key.Key(key_tonic))

    # Time signature (handle "6/8" specially; everything else is N/4)
    part.append(meter.TimeSignature(time_sig_str))

    # Each grid beat in our chord grid = 1.0 quarterLength regardless of meter.
    # In 6/8 this gives a 3.0-ql bar of three quarter-note pulses — chord
    # placement is correct; MuseScore will display the time signature as 6/8
    # and render the compound feel.
    beat_ql = 1.0
    bar_ql  = beats_per_bar * beat_ql

    # Map bar number → section letter for the bar that opens each section.
    section_marks: dict[int, str] = {}
    if sections:
        for sec in sections:
            section_marks[sec["start_bar"]] = sec["label"]

    for bar in bar_chords:
        measure = stream.Measure(number=bar["bar"])
        if bar["bar"] in section_marks:
            measure.insert(0.0, expressions.RehearsalMark(section_marks[bar["bar"]]))
        # Chord symbols at their segment offsets. ChordSymbol renders as a
        # text above the staff (MusicXML <harmony>), so it coexists with the
        # rhythm-slash notes below.
        offset = 0.0
        for seg in bar["segments"]:
            figure = _crema_to_m21_figure(seg["chord"], use_sharps)
            if figure is not None:
                cs = harmony.ChordSymbol(figure)
                cs.duration.quarterLength = seg["beats"] * beat_ql
                measure.insert(offset, cs)
            offset += seg["beats"] * beat_ql
        # Fill the bar with one rhythm slash per grid beat. The pitch is
        # cosmetic — MuseScore/Sibelius position slash noteheads on the
        # middle line regardless.
        for b in range(beats_per_bar):
            slash = note.Note("B4")
            slash.notehead = "slash"
            slash.noteheadFill = True
            slash.duration.quarterLength = beat_ql
            measure.insert(b * beat_ql, slash)
        part.append(measure)

    score.append(part)
    return score


# ---------------------------------------------------------------------------
# LilyPond generation
# ---------------------------------------------------------------------------

def generate_lilypond(
    bar_chords: list[dict],
    title: str,
    beats_per_bar: int,
    key_stmt: str,
    bars_per_line: int,
    low_conf_pct: float,
    subtitle: str = "",
    use_sharps: bool = False,
    time_sig_str: str | None = None,
    sections: list[dict] | None = None,
) -> str:
    # time_sig_str overrides the default "{beats_per_bar}/4" — used for 6/8.
    ts = time_sig_str or f"{beats_per_bar}/4"
    # Per-beat rhythm slash. In 6/8 each grid step is an eighth so the bar
    # fills correctly; everything else is quartered. `c'` is squashed to the
    # middle line by Pitch_squash_engraver below, so the pitch is cosmetic.
    beat_duration = "8" if beats_per_bar == 6 else "4"
    bar_slashes = " ".join(f"c'{beat_duration}" for _ in range(beats_per_bar))

    # Map bar number (1-indexed) → section letter for the bar that opens each section.
    # `\mark` belongs to the Score context, so we attach the rehearsal mark to
    # the matching bar's slash line. Marks render above the staff with a default box.
    section_marks: dict[int, str] = {s["start_bar"]: s["label"] for s in (sections or [])}

    chord_lines, slash_lines = [], []
    for i, bar in enumerate(bar_chords):
        tokens = [_ly_chord_token(seg["chord"], seg["beats"], use_sharps) for seg in bar["segments"]]
        chord_lines.append(" ".join(tokens))
        slashes = bar_slashes
        if bar["bar"] in section_marks:
            label = section_marks[bar["bar"]]
            slashes = f'\\mark \\markup {{ \\box "{label}" }} {bar_slashes}'
        slash_lines.append(slashes)
        if (i + 1) % bars_per_line == 0 and i < len(bar_chords) - 1:
            slash_lines.append("\\break")

    # LilyPond strings need backslashes and double-quotes escaped so that
    # titles containing punctuation don't blow up the parser.
    def _ly_escape(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    # Pre-wrap the title in Python so long under-/hyphen-joined filenames
    # don't overflow the page. `\wordwrap-string` only breaks on whitespace,
    # so titles like `AUDIO_FILES_…` would otherwise run off the margin.
    # Width is tuned for the current title fontsize (#4 → ~2× default).
    title_lines = textwrap.wrap(
        title, width=26,
        break_long_words=True,
        break_on_hyphens=True,
    ) or [title]
    title_block = "\n      ".join(f'\\line {{ "{_ly_escape(line)}" }}' for line in title_lines)

    subtitle_safe = _ly_escape(subtitle) if subtitle else ""
    subtitle_line = (
        f'  subtitle = \\markup {{ \\override #\'(font-name . "Season Sans SemiBold") "{subtitle_safe}" }}'
        if subtitle else ""
    )
    warning = (
        '\\markup {\n  \\vspace #1\n'
        f'  \\italic "⚠  Low confidence ({low_conf_pct:.0f}% of beats) — verify manually."\n}}'
        if low_conf_pct > 30 else ""
    )

    chord_body  = " |\n    ".join(chord_lines)
    slash_body  = " |\n        ".join(slash_lines)

    return f"""\
\\version "2.26.0"

\\header {{
  title = \\markup {{
    \\override #'(font-name . "Season Musiversal Sans")
    \\fontsize #4
    \\center-column {{
      {title_block}
    }}
  }}
{subtitle_line}
  tagline = ""
}}

\\paper {{
  #(set-paper-size "a4")
  top-margin = 20\\mm
  left-margin = 15\\mm
  right-margin = 15\\mm
  markup-system-spacing =
    #'((basic-distance . 14)
       (minimum-distance . 10)
       (padding . 4)
       (stretchability . 10))
  system-system-spacing =
    #'((basic-distance . 16)
       (minimum-distance . 12)
       (padding . 2)
       (stretchability . 20))
}}

theChords = \\chordmode {{
    {chord_body}
}}

\\score {{
  <<
    \\new ChordNames {{
      \\set chordChanges = ##t
      \\override ChordNames.ChordName.font-size = #1
      \\theChords
    }}
    \\new Staff {{
      {key_stmt}
      \\time {ts}
      \\new Voice \\with {{
        \\consists "Pitch_squash_engraver"
      }} {{
        \\improvisationOn
        {slash_body}
      }}
    }}
  >>
  \\layout {{
    \\context {{
      \\Score
      \\override SpacingSpanner.base-shortest-duration = #(ly:make-moment 1/8)
    }}
  }}
}}

{warning}
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate a PDF chord chart from an audio file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ./venv_crema/bin/python3.11 chord_chart_render.py -i song.wav
  ./venv_crema/bin/python3.11 chord_chart_render.py -i song.wav --key "bes:major" --title "My Song"
  ./venv_crema/bin/python3.11 chord_chart_render.py -i song.wav --no-bpm --bars-per-line 4
        """,
    )
    p.add_argument("-i", "--input",       required=True)
    p.add_argument("-o", "--output",      default=None,   help="Output PDF path (default: same dir as input)")
    p.add_argument("--title",             default=None,   help="Chart title (default: filename)")
    p.add_argument("--key",               default="auto", help="e.g. 'f:minor', 'bes:major' (default: auto)")
    p.add_argument("--time-sig",          type=int, default=None, dest="time_sig", help="Beats per bar (default: auto)")
    p.add_argument("--bpm",               type=float, default=None, help="Override BPM (default: sidecar or auto)")
    p.add_argument("--bars-per-line",     type=int, default=4, dest="bars_per_line")
    p.add_argument("--threshold",         type=float, default=CONFIDENCE_WARN)
    p.add_argument("--sample-rate",       type=int, default=44100)
    p.add_argument("--no-bpm",            action="store_true", help="Omit BPM from subtitle")
    p.add_argument("--no-key",            action="store_true", help="Omit key from subtitle")
    p.add_argument("--no-meter",          action="store_true", help="Omit meter from subtitle")
    p.add_argument("--subtitle",          default=None,   help="Override entire subtitle ('' to hide)")
    p.add_argument("--add-7th",           action="store_true", dest="add_7th",
                   help="Keep maj7, m7, and dominant 7 chords (default: simplify to major/minor)")
    p.add_argument("--mid-bar-threshold", type=float, default=MID_BAR_THRESHOLD,
                   dest="mid_bar_threshold",
                   help=f"Confidence required for a mid-bar chord change to appear "
                        f"(default: {MID_BAR_THRESHOLD})")
    p.add_argument("--no-madmom-fallback", action="store_false", dest="madmom_fallback",
                   help="Disable the default madmom fallback for low-confidence bars")
    p.add_argument("--madmom-fallback",    action="store_true",  dest="madmom_fallback",
                   help="Use madmom to re-evaluate bars where crema confidence < --madmom-threshold "
                        "(on by default)")
    p.set_defaults(madmom_fallback=True)
    p.add_argument("--madmom-threshold",  type=float, default=MADMOM_THRESHOLD,
                   dest="madmom_threshold",
                   help=f"Bar mean-confidence below which madmom fallback triggers "
                        f"(default: {MADMOM_THRESHOLD})")
    p.add_argument("--key-tiebreak",      action="store_true", dest="key_tiebreak",
                   help="After chromagram key detection, refine the key by weighting candidates "
                        "toward the root that appears most often in the detected bar chords "
                        "(resolves major/minor ambiguity, e.g. Bb major vs C minor)")
    p.add_argument("--key-snap",          action="store_true", dest="key_snap",
                   help="Snap non-diatonic low-confidence chords to the nearest diatonic equivalent")
    p.add_argument("--key-snap-threshold", type=float, default=KEY_SNAP_THRESHOLD,
                   dest="key_snap_threshold",
                   help=f"Bars below this mean confidence are eligible for key snapping "
                        f"(default: {KEY_SNAP_THRESHOLD})")
    p.add_argument("--half-time",          action="store_true", dest="half_time",
                   help="Keep every other detected beat (fixes half-time grooves where the "
                        "beat tracker locks onto 8th notes instead of quarter notes). "
                        "Triggered automatically when --bpm is set and the detected rate "
                        "is ~2× the specified BPM.")
    p.add_argument("--compound",           action="store_true", dest="compound",
                   help="Force 6/8 notation when the time signature would otherwise be 3/4 "
                        "(auto-detected for most 6/8 songs; use this flag as a manual override).")
    p.add_argument("--open",              action="store_true", help="Open PDF when done")
    p.add_argument("--keep-ly",           action="store_true", help="Keep the .ly source file")
    p.add_argument("--sections-json",     default=None, dest="sections_json",
                   help="Path to allin1 sections JSON written by pipeline.py. "
                        "Omit to produce a chart with no rehearsal marks.")
    p.add_argument("--sections-json-wait-s", type=int, default=0, dest="sections_json_wait_s",
                   help="Seconds to poll for --sections-json to appear (for parallel execution). "
                        "0 = don't wait (default).")
    # Library knobs
    lib = p.add_argument_group("Library knobs")
    lib.add_argument("--no-bar-phase",     action="store_false", dest="bar_phase",
                     help="Disable chord-grid phase alignment to bar downbeats")
    lib.set_defaults(bar_phase=True)
    lib.add_argument("--ts-window-factor",   type=float, default=0.15, dest="ts_window_factor",
                     help="Time-signature autocorrelation window factor (default: 0.15)")
    lib.add_argument("--librosa-start-bpm",  type=float, default=120.0, dest="librosa_start_bpm",
                     help="Initial tempo guess for librosa beat tracker (default: 120)")
    lib.add_argument("--librosa-tightness",  type=float, default=100.0, dest="librosa_tightness",
                     help="librosa beat-tracker onset-strength weighting (default: 100)")
    lib.add_argument("--librosa-hop-length", type=int,   default=512,   dest="librosa_hop_length",
                     help="librosa STFT hop length in samples (default: 512)")
    # ── HPSS preprocessing for crema input ──
    # Operates on the (time-aligned) audio already loaded for chord detection.
    # Key detection and beat detection always use the raw `y`; only crema sees
    # the cleaned signal so the harmonic-percussive split can't bias the
    # chromagram-based key or the onset-strength-based beat tracker.
    lib.add_argument("--hpss-mode",        default="hpss",
                     choices=("off", "hpss", "hpss-no-drums"), dest="hpss_mode",
                     help="HPSS preprocessing before crema chord detection "
                          "(off | hpss = harmonic-only | hpss-no-drums = subtract drums stem "
                          "then HPSS, requires --drums-wav). Default: hpss")
    lib.add_argument("--drums-wav",        default=None, dest="drums_wav",
                     help="Path to drums stem WAV (required when --hpss-mode=hpss-no-drums).")
    lib.add_argument("--hpss-margin",      type=float, default=3.0, dest="hpss_margin",
                     help="librosa.effects.hpss margin (higher = more aggressive harmonic/"
                          "percussive separation; default: 3.0)")

    p.add_argument("--progress-json",     action="store_true", dest="progress_json",
                   help="Emit machine-readable PROGRESS JSON lines on stdout")
    return p.parse_args()


# ---------------------------------------------------------------------------
# HPSS preprocessing for chord detection
# ---------------------------------------------------------------------------

def _apply_hpss_preprocessing(
    y: np.ndarray,
    sr: int,
    mode: str,
    drums_wav: str | None,
    margin: float,
    sample_rate: int,
) -> np.ndarray:
    """Return the audio array to feed into crema, based on hpss_mode.

    Operates on the *already loaded* time-aligned signal `y` — the caller is
    expected to have run load_audio_mono on the stabilised WAV.  Key detection
    and beat tracking continue to use the raw `y`; only crema sees the result.

    mode='off'           → return y unchanged.
    mode='hpss'          → librosa.effects.hpss(y, margin=margin); return harmonic.
    mode='hpss-no-drums' → load drums.wav (also time-aligned), subtract from y
                           (length-clipped to min), then HPSS the residual.
                           Falls back to plain 'hpss' if drums_wav is missing.
    """
    if mode == "off":
        return y
    import librosa
    y_input = y
    if mode == "hpss-no-drums":
        if drums_wav and os.path.isfile(drums_wav):
            print(f"  [HPSS] Subtracting drums stem: {os.path.basename(drums_wav)}")
            drums_y, _ = load_audio_mono(drums_wav, sample_rate)
            n = min(len(y), len(drums_y))
            y_input = (y[:n] - drums_y[:n]).astype(np.float32)
        else:
            print(f"  [HPSS] drums stem not found at {drums_wav!r} — "
                  "falling back to plain HPSS")
    y_harmonic, _ = librosa.effects.hpss(y_input, margin=margin)
    print(f"  [HPSS] Harmonic separation applied (margin={margin})")
    return y_harmonic.astype(np.float32)


def main() -> None:
    global _PROGRESS_JSON
    args = parse_args()
    _PROGRESS_JSON = args.progress_json

    if not os.path.isfile(args.input):
        sys.exit(f"File not found: {args.input}")

    title     = args.title or os.path.splitext(os.path.basename(args.input))[0]
    base      = args.output or os.path.splitext(args.input)[0]
    pdf_path  = base + ".pdf"
    ly_path   = base + ".ly"
    json_path = base + ".json"
    xml_path  = base + ".musicxml"

    # 1. Load
    _emit("load", 0.02, "loading audio")
    print(f"\n[1/5] Loading {args.input} …")
    y, sr = load_audio_mono(args.input, args.sample_rate)
    print(f"  {len(y)/sr:.1f}s  |  {sr} Hz  |  mono")

    # Detect key early from chromagram so it's available for key-snap and subtitle.
    # A manual --key flag always takes precedence.
    # When --key-tiebreak is set the candidates are kept and the key is
    # refined after bar_chords are available (see step 5 below).
    if args.key != "auto":
        key_root, key_mode = _parse_key_params(args.key)
        key_stmt       = _ly_key(args.key)
        key_display    = key_override_to_display(args.key)
        _key_candidates = None          # tiebreaker not applicable
    else:
        _key_candidates = detect_key_candidates(y, sr, n=5)
        key_root, key_mode = _key_candidates[0][1], _key_candidates[0][2]
        key_stmt    = key_params_to_ly_stmt(key_root, key_mode)
        key_display = key_params_to_display(key_root, key_mode)
        print(f"  Key (chromagram): {key_display}")

    # Accidental policy: sharp keys (G D A E B F# …) → use sharps everywhere;
    # flat keys (F Bb Eb Ab Db …) → use flats.  Computed once from the key and
    # threaded into every display and LilyPond function.
    use_sharps = _use_sharps(key_root, key_mode)

    # 2. Detect chords
    _emit("crema", 0.10, "crema chord detection")
    print("\n[2/5] Detecting chords (crema) …")
    if args.hpss_mode != "off":
        print(f"  [HPSS] mode={args.hpss_mode}")
    y_chords = _apply_hpss_preprocessing(
        y, sr, args.hpss_mode, args.drums_wav, args.hpss_margin, args.sample_rate,
    )
    times, confidence, labels = detect_chords_crema(y_chords, sr)
    _emit("crema", 0.45, f"{len(times)} frames")
    hop = int(round((times[1] - times[0]) * sr)) if len(times) > 1 else 4096
    print(f"  {len(times)} frames  |  mean confidence: {confidence.mean():.1%}")

    # 3. Detect beats — prefer explicit flag, then sidecar, then auto
    _emit("beats", 0.50, "beat detection")
    print("\n[3/5] Detecting beats …")
    beat_times = detect_beats(
        y, sr,
        start_bpm=args.librosa_start_bpm,
        tightness=args.librosa_tightness,
        hop_length=args.librosa_hop_length,
    )
    _emit("beats", 0.60, f"{len(beat_times)} beats")
    sidecar_bpm = read_bpm_sidecar(args.input) if args.bpm is None else None
    detected_bpm = 60.0 / float(np.median(np.diff(beat_times)))
    bpm = args.bpm or sidecar_bpm or detected_bpm
    print(f"  {len(beat_times)} beats  |  {detected_bpm:.1f} BPM (detected)"
          + (f"  |  override: {bpm:.0f} BPM" if (args.bpm or sidecar_bpm) else "")
          + (" (from sidecar)" if sidecar_bpm else ""))

    # Half-time correction: if the beat tracker locked onto 8th notes (detected ≈ 2× target),
    # keep every other beat to recover the quarter-note grid.
    # Triggered explicitly by --half-time, or automatically when --bpm is given and
    # the detected rate is roughly twice the specified BPM (ratio 1.7–2.3).
    _auto_half = (
        args.bpm is not None
        and 1.7 < detected_bpm / args.bpm < 2.3
    )
    if args.half_time or _auto_half:
        beat_times = beat_times[::2]
        bpm_after  = 60.0 / float(np.median(np.diff(beat_times)))
        trigger    = "--half-time flag" if args.half_time else f"auto (detected {detected_bpm:.0f} ≈ 2× {args.bpm:.0f})"
        print(f"  Half-time correction ({trigger}): "
              f"{len(beat_times)} beats  |  {bpm_after:.1f} BPM")
        bpm = args.bpm or bpm_after   # honour the override in the subtitle

    # 4. Detect time signature
    if args.time_sig:
        beats_per_bar = args.time_sig
        _ts_source = "manual"
    else:
        beats_per_bar = detect_time_signature(y, sr, beat_times, window_factor=args.ts_window_factor)
        _ts_source = "auto-detected"

    # detect_time_signature returns 6 to signal compound duple (6/8).
    # --compound forces the same treatment for any 3-beat result.
    _compound = (beats_per_bar == 6) or (args.compound and beats_per_bar == 3)
    if _compound:
        beats_per_bar = 3   # chord grid uses 3 beats per bar
        time_sig_str  = "6/8"
    else:
        time_sig_str  = f"{beats_per_bar}/4"
    print(f"\n[4/5] Time signature: {time_sig_str} ({_ts_source})")

    # 5. Align, simplify, collapse to bars
    _emit("align", 0.65, "aligning chords to bars")
    print(f"\n[5/5] Aligning chords to beat grid …")
    beat_chords = beat_sync_chords(times, confidence, labels, beat_times, sr, hop)

    beat_chords = [{**b, "chord": simplify_chord(b["chord"], add_7th=args.add_7th)}
                   for b in beat_chords]

    # Align the beat grid to real bar downbeats (skip with --no-bar-phase).
    if args.bar_phase:
        bar_phase = find_bar_phase(beat_chords, beats_per_bar)
        if bar_phase > 0:
            print(f"  Bar phase: offset {bar_phase} beat(s) to align chord changes to bar boundaries")
            beat_chords = beat_chords[bar_phase:]

    bar_chords = hybrid_bar_chords(beat_chords, beats_per_bar, args.mid_bar_threshold)

    # Optional: chord-frequency tiebreaker to disambiguate closely related keys
    if args.key_tiebreak and _key_candidates is not None:
        refined_root, refined_mode = refine_key_by_chord_frequency(_key_candidates, bar_chords)
        refined_display = key_params_to_display(refined_root, refined_mode)
        if (refined_root, refined_mode) != (key_root, key_mode):
            print(f"  Key (tiebreaker): {key_display} → {refined_display}")
        else:
            print(f"  Key (tiebreaker): {refined_display} (unchanged)")
        key_root, key_mode = refined_root, refined_mode
        key_stmt    = key_params_to_ly_stmt(key_root, key_mode)
        key_display = refined_display
        use_sharps  = _use_sharps(key_root, key_mode)

    # Optional: madmom bar-level fallback for low-confidence bars
    madmom_substituted: list[int] = []
    if args.madmom_fallback:
        script_dir    = os.path.dirname(os.path.abspath(__file__))
        madmom_python = os.path.join(script_dir, "venv_madmom", "bin", "python3.11")
        madmom_script = os.path.join(script_dir, "madmom_chord_detect.py")
        if not os.path.isfile(madmom_python):
            print("  [madmom] venv_madmom not found — skipping fallback")
        else:
            _emit("madmom", 0.75, "madmom fallback")
            print(f"  madmom fallback (bar mean confidence < {args.madmom_threshold:.0%}) …")
            result = subprocess.run(
                [madmom_python, madmom_script, "--dump-segments", "-i", args.input],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"  [madmom] detection failed — skipping fallback\n{result.stderr[-500:]}")
            else:
                import json as _json
                raw = _json.loads(result.stdout.strip())
                segments = [(s, e, l) for s, e, l in raw]
                beat_interval = float(np.median(np.diff(beat_times)))
                bar_chords, madmom_substituted = madmom_fallback_bars(
                    bar_chords, segments,
                    beats_per_bar        = beats_per_bar,
                    beat_interval        = beat_interval,
                    confidence_threshold = args.madmom_threshold,
                    add_7th              = args.add_7th,
                    use_sharps           = use_sharps,
                )
                print(f"  → {len(madmom_substituted)} bar(s) updated from madmom")

    # Optional: key-constrained snapping for low-confidence non-diatonic chords
    key_snapped: list[int] = []
    if args.key_snap:
        print(f"  key snap ({key_mode}, root semitone {key_root}, "
              f"threshold {args.key_snap_threshold:.0%}) …")
        bar_chords, key_snapped = key_snap_bars(
            bar_chords, key_root, key_mode, args.key_snap_threshold,
            use_sharps=use_sharps,
        )
        print(f"  → {len(key_snapped)} bar(s) snapped to diatonic chord")

    all_segs = [seg for bar in bar_chords for seg in bar["segments"]]
    low_conf = sum(1 for s in all_segs if s["confidence"] < args.threshold)
    low_pct  = 100 * low_conf / max(len(all_segs), 1)
    print(f"  Low-confidence segments: {low_conf}/{len(all_segs)} ({low_pct:.0f}%)")

    # Structural segmentation — read allin1 JSON written by pipeline.py.
    # When pipeline.py runs allin1 in parallel, the file may not exist yet;
    # --sections-json-wait-s tells us to poll for it up to N seconds.
    if args.sections_json and not os.path.isfile(args.sections_json) and args.sections_json_wait_s > 0:
        import time as _time
        deadline = _time.monotonic() + args.sections_json_wait_s
        print(f"  [sections] Waiting up to {args.sections_json_wait_s}s for allin1 to finish …", flush=True)
        while _time.monotonic() < deadline:
            if os.path.isfile(args.sections_json):
                break
            _time.sleep(2)
        else:
            print("  [sections] Timed out — continuing without section marks")

    sections: list[dict] = []
    if args.sections_json:
        _emit("sections", 0.85, "loading sections")
        print("\n  Loading sections (allin1) …")
        sections = detect_sections(args.sections_json, bar_chords)
        if sections:
            print(f"  → {len(sections)} section(s): " + ", ".join(
                f"{s['label']}(bars {s['start_bar']}-{s['end_bar']})" for s in sections
            ))
        else:
            print("  → no sections detected (continuing without rehearsal marks)")

    madmom_bar_set  = set(madmom_substituted)
    key_snap_bar_set = set(key_snapped)
    print("\n  Chord summary (changes only):")
    prev = None
    for bar in bar_chords:
        beat_pos = 1
        tags = []
        if bar["bar"] in madmom_bar_set:   tags.append("madmom")
        if bar["bar"] in key_snap_bar_set: tags.append("key snap")
        tag = f"  [{', '.join(tags)}]" if tags else ""
        for seg in bar["segments"]:
            if seg["chord"] != prev:
                flag   = " ?" if seg["confidence"] < args.threshold else ""
                prefix = f"Bar {bar['bar']:>3}" if beat_pos == 1 else f"      beat {beat_pos}"
                print(f"    {prefix}  {seg['time']:>6.1f}s  {crema_to_display(seg['chord'], use_sharps):<8}  ({seg['confidence']:.0%}{flag}){tag if beat_pos == 1 else ''}")
                prev = seg["chord"]
            beat_pos += seg["beats"]

    # Build subtitle  (key_stmt and key_display already computed from chromagram above)
    if args.subtitle is not None:
        subtitle = args.subtitle
    else:
        parts = []
        if not args.no_meter: parts.append(f"Meter: {time_sig_str}")
        if not args.no_key:   parts.append(f"Key: {key_display}")
        if not args.no_bpm:   parts.append(f"BPM: {round(bpm)}")
        subtitle = "  ·  ".join(parts)

    _emit("render", 0.90, "rendering PDF")
    print(f"\nRendering PDF …  ({subtitle or 'no subtitle'})")
    ly_src = generate_lilypond(
        bar_chords, title=title, beats_per_bar=beats_per_bar,
        key_stmt=key_stmt, bars_per_line=args.bars_per_line,
        low_conf_pct=low_pct, subtitle=subtitle,
        use_sharps=use_sharps, time_sig_str=time_sig_str,
        sections=sections,
    )

    with open(ly_path, "w") as f:
        f.write(ly_src)

    result = subprocess.run(
        ["lilypond", "--output", os.path.splitext(pdf_path)[0], ly_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("LilyPond error:\n", result.stderr[-2000:])
        sys.exit(1)

    if not args.keep_ly:
        os.unlink(ly_path)

    print(f"\n  PDF saved: {pdf_path}")

    # Render a PNG preview of page 1 — embedded PDF viewers are flaky across
    # browsers/sandboxes, but a flat PNG always shows up in <img>.
    try:
        import fitz  # type: ignore[import-not-found]  # PyMuPDF
        _doc = fitz.open(pdf_path)
        if _doc.page_count > 0:
            _pix = _doc[0].get_pixmap(dpi=120)
            _preview_path = os.path.splitext(pdf_path)[0] + "_preview.png"
            _pix.save(_preview_path)
            print(f"  Preview   : {_preview_path}")
        _doc.close()
    except Exception as _e:
        print(f"  [preview] PNG render failed (non-fatal): {_e}")

    # Write MusicXML (editable in MuseScore / Sibelius).
    # Failure here must not abort the run — the PDF is the primary artifact.
    musicxml_written: str | None = None
    try:
        score = bar_chords_to_musicxml(
            bar_chords,
            beats_per_bar = beats_per_bar,
            key_root      = key_root,
            key_mode      = key_mode,
            title         = title,
            time_sig_str  = time_sig_str,
            use_sharps    = use_sharps,
            sections      = sections,
        )
        score.write("musicxml", fp=xml_path)
        musicxml_written = xml_path
        print(f"  MusicXML  : {xml_path}")
    except Exception as e:
        print(f"  [musicxml] export failed (non-fatal): {e}")

    # Write analysis JSON
    beat_intervals = np.diff(beat_times)
    all_confs      = [seg["confidence"] for seg in all_segs]
    chord_changes  = sum(1 for k in range(1, len(all_segs))
                         if all_segs[k]["chord"] != all_segs[k-1]["chord"])
    analysis = {
        "input":          args.input,
        "title":          title,
        "time_signature": time_sig_str,
        "key":            key_display,
        "bars":           len(bar_chords),
        "chord_identification": {
            "mean_confidence":    round(float(np.mean(all_confs)), 3),
            "median_confidence":  round(float(np.median(all_confs)), 3),
            "low_confidence_pct": round(low_pct, 1),
            "chord_changes":      chord_changes,
        },
        "alignment": {
            "detected_bpm":     round(float(bpm), 2),
            "bpm_source":       "sidecar" if sidecar_bpm else "auto",
            "beat_count":       len(beat_times),
            "beat_interval_cv": round(float(np.std(beat_intervals) / np.mean(beat_intervals)), 4),
            "bar_phase_offset": bar_phase,
        },
        "madmom_fallback": {
            "enabled":                 args.madmom_fallback,
            "threshold":               args.madmom_threshold if args.madmom_fallback else None,
            "bars_substituted":        len(madmom_substituted),
            "substituted_bar_numbers": madmom_substituted,
        },
        "key_snap": {
            "enabled":              args.key_snap,
            "threshold":            args.key_snap_threshold if args.key_snap else None,
            "bars_snapped":         len(key_snapped),
            "snapped_bar_numbers":  key_snapped,
        },
        "musicxml": musicxml_written,
        "sections": [
            {
                "label":      s["label"],
                "start_bar":  s["start_bar"],
                "end_bar":    s["end_bar"],
                "start_time": s["start_time"],
                "end_time":   s["end_time"],
            }
            for s in sections
        ],
    }
    with open(json_path, "w") as f:
        json.dump(analysis, f, indent=2)
    print(f"  Analysis  : {json_path}")
    _emit("render", 1.0, "done")

    if args.open:
        subprocess.run(["open", pdf_path])


if __name__ == "__main__":
    main()

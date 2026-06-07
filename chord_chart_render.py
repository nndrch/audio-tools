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


def _bass_pc_to_name(bass_pc: int, use_sharps: bool) -> str:
    """Return the display name for a bass pitch class (slash-chord suffix)."""
    table = _SEMITONE_TO_ROOT_SHARP if use_sharps else _SEMITONE_TO_ROOT_FLAT
    return table.get(int(bass_pc) % 12, "")


def crema_to_ly(label: str, use_sharps: bool = False, bass_pc: int | None = None) -> tuple[str, str]:
    if label in ("N", "X", ""):
        return ("s", "")
    root, quality = label.split(":", 1) if ":" in label else (label, "maj")
    # Normalise to the key's accidental policy before looking up the LilyPond name.
    if use_sharps:
        root = _FLAT_TO_SHARP_ROOT.get(root, root)
    else:
        root = _SHARP_TO_FLAT_ROOT.get(root, root)
    ly_root = _ROOT_TO_LY.get(root, root.lower())
    ly_qual = _QUALITY_TO_LY.get(quality, f":{quality}")
    # Append LilyPond slash-bass syntax when an inversion tag is present.
    # LilyPond chord syntax: <root><qual>/<bass>  e.g. c:5/e  →  C/E
    if bass_pc is not None:
        bass_name = _bass_pc_to_name(bass_pc, use_sharps)
        if bass_name:
            bass_ly = _ROOT_TO_LY.get(bass_name, bass_name.lower())
            ly_qual = f"{ly_qual}/{bass_ly}"
    return (ly_root, ly_qual)


def crema_to_display(label: str, use_sharps: bool = False, bass_pc: int | None = None) -> str:
    if label in ("N", "X", ""):
        return ""
    root, quality = label.split(":", 1) if ":" in label else (label, "maj")
    if use_sharps:
        display_root = _FLAT_TO_SHARP_ROOT.get(root, root)
    else:
        display_root = _SHARP_TO_FLAT_ROOT.get(root, root)
    base = f"{display_root}{_QUALITY_DISPLAY.get(quality, quality)}"
    if bass_pc is not None:
        bass_name = _bass_pc_to_name(bass_pc, use_sharps)
        if bass_name:
            base = f"{base}/{bass_name}"
    return base


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


def _ly_chord_token(label: str, beats: int, use_sharps: bool = False, bass_pc: int | None = None) -> str:
    root, qual = crema_to_ly(label, use_sharps, bass_pc=bass_pc)
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
# music21's ChordSymbol is built from an explicit (root, kind, bass) rather than
# a figure string: figure-string parsing silently mis-reads several qualities we
# emit (e.g. "Bm7b5" decodes to minor-seventh, not half-diminished; an unknown
# suffix collapses to a bare major triad).  Building from the kind-value enum
# guarantees the correct MusicXML <kind>, and chordKindStr sets the printed
# symbol so charts read consistently across renderers (Berklee §6).

# crema quality  →  MusicXML kind-value enum (MusicXML 4.0 §5).
_QUALITY_TO_M21_KIND = {
    "maj":     "major",             "min":     "minor",
    "7":       "dominant",          "maj7":    "major-seventh",
    "min7":    "minor-seventh",     "dim":     "diminished",
    "dim7":    "diminished-seventh", "hdim7":  "half-diminished",
    "aug":     "augmented",         "sus2":    "suspended-second",
    "sus4":    "suspended-fourth",  "maj6":    "major-sixth",
    "min6":    "minor-sixth",       "minmaj7": "major-minor",
}


def _crema_to_m21_parts(
    label: str, use_sharps: bool = False, bass_pc: int | None = None,
) -> tuple[str, str, str | None, str] | None:
    """
    Translate a crema-style chord label ('Bb:min7', 'C#:maj7', 'G:7') into the
    pieces music21's ChordSymbol needs: (root, kind, bass, display_text).

    Returns None for N/X/empty labels (no <harmony> is emitted for those).

    - root / bass are spelled to the key's accidental policy, with flats as
      music21's "-" (e.g. "B-" for Bb).
    - kind is a MusicXML kind-value enum string; an unknown quality falls back to
      the *nearest* base (major/minor via _QUALITY_TO_SIMPLE), never silently to
      major (MusicXML 4.0 §7).
    - display_text is the printed chord suffix (Berklee spelling, e.g. "ø7",
      "maj7", "m") — the same string the PDF/terminal use, so all three agree.
    """
    if label in ("N", "X", ""):
        return None
    root, quality = label.split(":", 1) if ":" in label else (label, "maj")
    root = _FLAT_TO_SHARP_ROOT.get(root, root) if use_sharps else _SHARP_TO_FLAT_ROOT.get(root, root)
    if root.endswith("b") and len(root) > 1:
        root = root[:-1] + "-"
    kind = _QUALITY_TO_M21_KIND.get(quality)
    if kind is None:
        kind = "minor" if _QUALITY_TO_SIMPLE.get(quality, "maj") == "min" else "major"
    text = _QUALITY_DISPLAY.get(quality, "")
    bass = None
    if bass_pc is not None:
        bass_name = _bass_pc_to_name(bass_pc, use_sharps)
        if bass_name:
            bass = (bass_name[:-1] + "-") if bass_name.endswith("b") and len(bass_name) > 1 else bass_name
    return (root, kind, bass, text)


def bar_chords_to_musicxml(
    bar_chords: list[dict],
    beats_per_bar: int,
    key_root: int,
    key_mode: str,
    title: str,
    time_sig_str: str,
    use_sharps: bool = False,
    sections: list[dict] | None = None,
    bpm: float | None = None,
    bars_per_line: int = 4,
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
    from music21 import (
        stream, harmony, meter, key as m21key, metadata, note, expressions,
        tempo, clef, bar as m21bar, layout, duration as m21duration,
    )

    score = stream.Score()
    score.metadata = metadata.Metadata(title=title)
    # music21 stamps "Music21" as the composer when none is set, and renderers
    # print it on the chart.  Blank it out (MusicXML 4.0 §4.2).
    score.metadata.composer = ""

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

    n_bars = len(bar_chords)
    for idx, bar in enumerate(bar_chords):
        measure = stream.Measure(number=bar["bar"])
        if idx == 0:
            # Treble clef (decorative on a slash chart but conventional, §4.6) and
            # the initial tempo (§4.9 — music21 emits both <metronome> and
            # <sound tempo>).  Quarter referent matches the 1.0-ql-per-grid-beat
            # model (including the 6/8 simplification), so the printed metronome
            # mark and playback tempo agree.
            measure.clef = clef.TrebleClef()
            if bpm and bpm > 0:
                measure.insert(0.0, tempo.MetronomeMark(
                    number=round(float(bpm)), referent=m21duration.Duration("quarter")))
        elif bars_per_line > 0 and idx % bars_per_line == 0:
            # Force ~bars_per_line bars per system so the MusicXML matches the
            # PDF's line breaks instead of MuseScore auto-packing by density (§4.12).
            measure.insert(0.0, layout.SystemLayout(isNew=True))
        if bar["bar"] in section_marks:
            measure.insert(0.0, expressions.RehearsalMark(section_marks[bar["bar"]]))
        # Chord symbols at their segment offsets. ChordSymbol renders as a
        # text above the staff (MusicXML <harmony>), so it coexists with the
        # rhythm-slash notes below.
        offset = 0.0
        for seg in bar["segments"]:
            parts = _crema_to_m21_parts(seg["chord"], use_sharps, seg.get("bass_pc"))
            if parts is not None:
                root_m21, kind, bass_m21, text = parts
                if bass_m21:
                    cs = harmony.ChordSymbol(root=root_m21, bass=bass_m21, kind=kind)
                else:
                    cs = harmony.ChordSymbol(root=root_m21, kind=kind)
                # Printed symbol (Berklee §6); the <kind> enum carries the meaning.
                cs.chordKindStr = text
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
        if idx == n_bars - 1:
            measure.rightBarline = m21bar.Barline("final")  # §4.11
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
        tokens = [_ly_chord_token(seg["chord"], seg["beats"], use_sharps, seg.get("bass_pc"))
                  for seg in bar["segments"]]
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
    p.add_argument("--lab-out",           default=None, dest="lab_out",
                   help="Also write the detected chord sequence as a Harte-style "
                        ".lab file (tab-separated 'start  end  label' in seconds). "
                        "Used by the eval harness; off by default.")
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
    # ── Bass-stem-anchored root correction ──
    # Requires stems to be available before chord detection.  pipeline.py
    # arranges this (stems_first) and points us at the bass WAV via --bass-wav.
    lib.add_argument("--bass-anchor",       action="store_true", dest="bass_anchor",
                     help="Override chord roots using the bass stem's dominant pitch "
                          "(requires --bass-wav). Corrects relative-minor / inversion "
                          "confusions which crema often gets wrong.")
    lib.add_argument("--bass-wav",          default=None, dest="bass_wav",
                     help="Path to bass stem WAV (used when --bass-anchor is set).")
    lib.add_argument("--bass-anchor-margin", type=float, default=0.55, dest="bass_anchor_margin",
                     help="Minimum chroma confidence (top-1 / (top-1+top-2)) for the bass "
                          "to override the chord root (default: 0.55)")
    # ── Section-aware chord consistency ──
    # Pure post-processing: groups bars by section label (--sections-json must
    # be set) and forces same-named sections to share the highest-confidence
    # chord progression at each position.  No effect when sections are absent
    # or each label appears only once.
    lib.add_argument("--section-consistency", action="store_true", dest="section_consistency",
                     help="Force same-named sections (e.g. Chorus 1 and Chorus 2) to share "
                          "their chord progression by voting per bar position. Requires "
                          "section detection (--sections-json).")
    # ── Slash chord labelling (inversions) ──
    # Reuses the bass stem loaded by bass-anchor.  When bass and chord agree on
    # root family but the bass note is a chord tone other than the root (3rd
    # or 5th), tag the segment for "C/E", "G/B", … slash notation.
    lib.add_argument("--slash-chords", action="store_true", dest="slash_chords",
                     help="Detect chord inversions (C/E, G/B, Am/C) using the bass stem. "
                          "Requires --bass-wav. Tags segments with bass_pc; renderers emit "
                          "slash notation in the PDF / MusicXML / JSON output.")
    # ── Key-conditioned Viterbi smoothing ──
    # Replaces the greedy per-bar chord pick with a music-theory-aware sequence
    # decode.  Bars with explicit mid-bar splits are skipped (they encode
    # fine-grained detection that Viterbi shouldn't flatten).
    lib.add_argument("--viterbi-smoothing", action="store_true", dest="viterbi_smoothing",
                     help="Smooth chord sequence with a key-conditioned Viterbi pass. "
                          "Catches one-off misdetections (e.g. a stray Bdim between two Cs "
                          "in C major) that no other guard catches. Works at bar level on "
                          "single-segment bars; mid-bar splits are left untouched.")
    lib.add_argument("--viterbi-stay-prob", type=float, default=0.35, dest="viterbi_stay_prob",
                     help="Viterbi same-chord self-transition prior (default: 0.35). "
                          "Higher = stickier (more reluctant to switch chords).")
    lib.add_argument("--viterbi-cadence-boost", type=float, default=4.0, dest="viterbi_cadence_boost",
                     help="Multiplier on classical cadence transitions (V→I, IV→I, ii→V, "
                          "v→i, iv→i) in the Viterbi prior (default: 4.0).")

    p.add_argument("--progress-json",     action="store_true", dest="progress_json",
                   help="Emit machine-readable PROGRESS JSON lines on stdout")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Slash chord (inversion) labelling
# ---------------------------------------------------------------------------
#
# Chord-detection models output a (root, quality) pair: "C:maj", "A:min7", …
# They have no notion of inversions — "C with E in the bass" still comes back
# as "C:maj" because the chord identity is the same.  Musically, though, the
# inversion matters: descending bass lines like `C – G/B – Am – F` read
# completely differently from `C – G – Am – F`.
#
# This pass runs after bass-anchor has corrected any wrong-root cases.  For
# every segment where bass and chord *agree on root family but the bass note
# is a chord tone other than the root* (the 3rd or 5th), we tag the segment
# with `bass_pc` so the renderers can emit slash notation.  When bass is
# *not* a chord tone, this is the bass-anchor case and we leave it alone
# (slash chords don't apply to non-diatonic bass).

# Pitch-class offsets from the root for each crema quality.  Conservative
# defaults — extended/altered qualities fall back to the major-triad set.
_CHORD_TONES_BY_QUALITY: dict[str, frozenset[int]] = {
    "maj":      frozenset({0, 4, 7}),
    "min":      frozenset({0, 3, 7}),
    "dim":      frozenset({0, 3, 6}),
    "aug":      frozenset({0, 4, 8}),
    "sus2":     frozenset({0, 2, 7}),
    "sus4":     frozenset({0, 5, 7}),
    "maj7":     frozenset({0, 4, 7, 11}),
    "min7":     frozenset({0, 3, 7, 10}),
    "7":        frozenset({0, 4, 7, 10}),
    "dim7":     frozenset({0, 3, 6, 9}),
    "hdim7":    frozenset({0, 3, 6, 10}),  # half-diminished (m7b5)
    "minmaj7":  frozenset({0, 3, 7, 11}),
    "maj6":     frozenset({0, 4, 7, 9}),
    "min6":     frozenset({0, 3, 7, 9}),
    "9":        frozenset({0, 2, 4, 7, 10}),
    "maj9":     frozenset({0, 2, 4, 7, 11}),
    "min9":     frozenset({0, 2, 3, 7, 10}),
}

def _chord_tones_for_quality(quality: str) -> frozenset[int]:
    """Return chord-tone pitch-class offsets, falling back to major triad."""
    return _CHORD_TONES_BY_QUALITY.get(quality, frozenset({0, 4, 7}))


def _apply_slash_chords(
    bar_chords: list[dict],
    bass_wav: str | None,
    sample_rate: int,
    margin_threshold: float = 0.55,
) -> tuple[list[dict], list[int]]:
    """
    Tag segments with `bass_pc` when the bass plays a chord tone other than
    the root (first or second inversion).  Renderers consult this field to
    emit slash notation ("C/E", "G/B", "Am/C").

    No-op when bass_wav is missing.  Leaves bass-as-non-chord-tone cases
    alone (those are bass-anchor's domain).
    """
    if not bar_chords or not bass_wav or not os.path.isfile(bass_wav):
        return bar_chords, []

    import librosa
    print(f"  [slash-chords] loading {os.path.basename(bass_wav)} …")
    y_bass, sr_bass = load_audio_mono(bass_wav, sample_rate)
    chroma = librosa.feature.chroma_cqt(y=y_bass, sr=sr_bass)
    frame_times = librosa.frames_to_time(np.arange(chroma.shape[1]), sr=sr_bass)

    if len(bar_chords) >= 2:
        bar_duration = bar_chords[1]["time"] - bar_chords[0]["time"]
    else:
        bar_duration = 2.0

    inverted_bars: set[int] = set()
    for i, bar in enumerate(bar_chords):
        bar_start = bar["time"]
        bar_end   = (bar_chords[i + 1]["time"]
                     if i + 1 < len(bar_chords) else bar_start + bar_duration)
        total_beats = sum(s["beats"] for s in bar["segments"])
        if total_beats == 0:
            continue
        beat_dur = (bar_end - bar_start) / total_beats

        cur_t = bar_start
        for seg in bar["segments"]:
            seg_start = cur_t
            seg_end   = cur_t + seg["beats"] * beat_dur
            cur_t     = seg_end

            mask = (frame_times >= seg_start) & (frame_times < seg_end)
            if not mask.any():
                continue

            seg_chroma = chroma[:, mask].mean(axis=1)
            sorted_c   = np.sort(seg_chroma)[::-1]
            margin = (sorted_c[0] / (sorted_c[0] + sorted_c[1])
                      if sorted_c[0] + sorted_c[1] > 1e-6 else 0.0)
            if margin < margin_threshold:
                continue

            bass_pc = int(np.argmax(seg_chroma))
            chord_label = seg["chord"]
            if ":" not in chord_label:
                continue
            root_str, quality = chord_label.split(":", 1)
            chord_root_pc = _ROOT_TO_SEMITONE.get(root_str)
            if chord_root_pc is None or chord_root_pc == bass_pc:
                # Bass = root, no inversion
                continue

            interval = (bass_pc - chord_root_pc) % 12
            tones = _chord_tones_for_quality(quality)
            if interval in tones:
                # Bass is a chord tone other than root → inversion → tag for slash
                seg["bass_pc"] = bass_pc
                inverted_bars.add(bar["bar"])
            # else: bass is NOT a chord tone — that's the bass-anchor case,
            # which (if enabled) already ran above. We leave it alone here.

    print(f"  [slash-chords] {len(inverted_bars)} bar(s) tagged with an inversion")
    return bar_chords, sorted(inverted_bars)


# ---------------------------------------------------------------------------
# Key-conditioned Viterbi smoothing
# ---------------------------------------------------------------------------
#
# crema picks the best chord per beat independently.  Result: a single
# misdetected `Bdim7` between two `C`s in an obviously tonal context.  Music
# theory says that's almost never a real chord change — V→I and I→IV
# cadences are common; I→vii° and similar transitions are vanishingly rare.
#
# Viterbi decoding finds the chord *sequence* that maximises
#   sum_i [ log P(crema_obs | chord_i) + log P(chord_i | chord_i-1, key) ]
# rather than the chord *per beat* that maximises P(chord | crema_obs).  A
# transition prior conditioned on the detected key replaces independent
# argmax with a music-theoretically-aware sequence decode.
#
# We marginalise crema's 170-dim posterior to 24-dim (12 roots × {major,
# minor}) so the transition matrix is small enough to hand-author and the
# music theory stays interpretable.  After Viterbi picks (root, mode) per
# bar, we recover the chord quality by taking the highest-posterior chord
# in the original 170 distribution whose root/mode matches Viterbi's pick.

# Reduced decoding vocabularies (Phase 1.3). marginalize_to_reduced_vocab() sums
# crema's 170-class posterior mass into one column per (root, reduced-quality),
# so a decode can argmax over the SUMMED family mass instead of crema's argmax
# over fine classes (which scatters a chord's mass across maj/maj7/7/… and lets a
# concentrated rival win the root).
REDUCED_MAJMIN = ("maj", "min")
REDUCED_7TH    = ("maj", "min", "maj7", "min7", "7", "dim", "sus")

# crema fine quality → reduced quality for the 7th vocabulary (the maj/min
# vocabulary reuses _QUALITY_TO_SIMPLE). hdim7 routes to dim (its triad is
# diminished); aug/maj6 → maj; min6/minmaj7 → min. Unmapped → maj.
_QUALITY_TO_REDUCED7 = {
    "maj": "maj",   "maj6": "maj",  "aug": "maj",
    "min": "min",   "min6": "min",  "minmaj7": "min",
    "maj7": "maj7",
    "min7": "min7",
    "7": "7",
    "dim": "dim",   "dim7": "dim",  "hdim7": "dim",
    "sus2": "sus",  "sus4": "sus",
}


def marginalize_to_reduced_vocab(
    probs: np.ndarray,
    vocab: list[str],
    add_7th: bool = False,
    keep_n: bool = True,
) -> tuple[np.ndarray, list[str]]:
    """Sum crema posteriors into a reduced (root, quality) vocabulary (Phase 1.3).

    Generalises _marginalize_crema_to_root_mode: instead of collapsing to 24
    (root × {maj,min}) and dropping N, it produces summed probability mass over a
    chosen reduced vocabulary and (by default) keeps an N column.

    This is the lever for the root-swap fix: if crema spreads a chord's mass
    across its family (C:maj 0.30 / C:maj7 0.30 / C:7 0.25 = 0.85) but argmaxes to
    a concentrated rival (A:min 0.35), summing the family beats the rival and the
    decode (M4) picks C. With add_7th=False the whole family collapses to one
    column per root (the strongest root-swap fix); with add_7th=True the 7th
    qualities occupy separate columns (finer labels, but the family no longer
    merges for the *root* decision — a tradeoff the eval harness arbitrates).

    Apply this PER FRAME, before beat aggregation (beat_sync_posteriors): summing
    columns is linear, so it commutes with the default `mean` aggregator, and any
    transient-resistant aggregator then operates on whole chord families rather
    than competing fine classes.

    Parameters
    ----------
    probs   : (T, n_classes) crema posteriors (per-frame or per-beat).
    vocab   : list of n_classes crema labels (column index → label, e.g. 'A#:7').
    add_7th : False → {maj, min}; True → {maj,min,maj7,min7,7,dim,sus}. Output
              width is 12*len(qualities) (+1 for N if keep_n): 25 / 85 by default,
              24 / 84 with keep_n=False.
    keep_n  : append an 'N' (no-chord) column. It collects crema's N mass AND its
              'X' (unknown-chord) mass — both render as no-chord (the file-wide
              X→N convention). Unknown roots/qualities are dropped (not misrouted),
              so with keep_n=False rows sum to ≤ the input mass.

    Returns
    -------
    (out, labels):
      out    : (T, K) summed mass, K = 12*len(qualities) (+1 if keep_n). NOT
               renormalised — it is faithful summed mass (the quantity M4
               argmaxes); rows sum to ~the retained input mass.
      labels : list[str] length K mapping column index → reduced label
               ('C:maj', 'A:min', …, 'N'). Roots are spelled with sharps;
               enharmonics score identically in mir_eval and are respelled to the
               key's accidental policy by the display/MusicXML helpers downstream.

    Downstream contract (preconditions for M4/M5):
      - Confidence: this output is summed mass (can exceed 1), NOT a [0,1]
        posterior. The decode (M4) must derive any beat 'confidence' for
        hybrid_bar_chords / madmom / key-snap as a normalised share
        (winning_column / row_sum) — the scale those 0.70–0.80 gates were tuned
        for — never the raw mass.
      - add_7th decode: prefer two-stage — win root+mode on the {maj,min} view
        (full family mass, so C beats Am), then refine the quality within the
        winning family — over a flat argmax across 84 columns (which splits the
        family and can regress the root). The eval harness arbitrates.
      - M5 must NOT re-run simplify_chord() on these labels (it would collapse
        dim→min and sus→maj); the reduced decode already IS the simplification.
    """
    probs = np.asarray(probs)
    if probs.ndim != 2 or probs.shape[1] != len(vocab):
        raise ValueError(
            f"probs shape {getattr(probs, 'shape', None)} inconsistent with "
            f"len(vocab)={len(vocab)}")

    qualities = REDUCED_7TH if add_7th else REDUCED_MAJMIN
    qmap      = _QUALITY_TO_REDUCED7 if add_7th else _QUALITY_TO_SIMPLE
    Q       = len(qualities)
    q_index = {q: k for k, q in enumerate(qualities)}

    T = probs.shape[0]
    K = 12 * Q + (1 if keep_n else 0)
    out = np.zeros((T, K), dtype=np.float64)
    n_col = K - 1 if keep_n else None   # N/X sink column index (None guards misuse)

    for j, lbl in enumerate(vocab):
        if ":" not in lbl:
            # crema has TWO no-colon classes: 'N' (no chord) and 'X' (unknown
            # chord). Both render as no-chord on a chart, so route both to the N
            # column — the file-wide X→N convention (cf. the --lab-out writer).
            # Dropped entirely when keep_n is False.
            if keep_n and lbl in ("N", "X"):
                out[:, n_col] += probs[:, j]
            continue
        root_str, quality = lbl.split(":", 1)
        root = _ROOT_TO_SEMITONE.get(root_str)
        rq   = qmap.get(quality)
        if root is None or rq is None:
            continue  # unknown root/quality: drop its mass rather than misroute it
        out[:, root * Q + q_index[rq]] += probs[:, j]

    labels = [f"{_SEMITONE_TO_ROOT_SHARP[r]}:{q}" for r in range(12) for q in qualities]
    if keep_n:
        labels.append("N")
    return out, labels


# Reduced quality → {maj,min} family, for the two-stage decode (M4): the decode
# wins root+mode on the family view (full family mass), then refines the quality
# within it. Covers both reduced vocabularies (maj/min maps through unchanged).
_REDUCED_QUALITY_TO_FAMILY = {
    "maj": "maj", "maj7": "maj", "7": "maj", "sus": "maj",
    "min": "min", "min7": "min", "dim": "min",
}


def reduced_vocab_decode(
    mass: np.ndarray,
    labels: list[str],
    beat_times: np.ndarray,
) -> list[dict]:
    """Decode per-beat reduced-vocab mass into beat_chords (Phase 1.3 / M4).

    Consumes the summed mass from marginalize_to_reduced_vocab (aggregated to
    beats via beat_sync_posteriors) and emits the beat_chords list that
    hybrid_bar_chords / find_bar_phase take: one dict per beat with keys 'beat'
    (1-based index), 'time', 'chord' ('root:quality' or 'N') and 'confidence'
    (a [0,1] share) — matching beat_sync_chords().

    Two-stage decode (works for both reduced vocabularies):
      1. Sum columns into the {maj,min} family view (root × mode) and pick the
         winner there — so a root whose mass is spread across its family
         (maj/maj7/7/sus) beats a concentrated rival, the root-swap fix. The
         N column competes as its own candidate and wins ties (no spurious
         chord on a no-chord beat).
      2. Refine the quality within the winning root+family by argmax over that
         family's columns. For the maj/min vocabulary stage 2 is a no-op (one
         column per family); for the 7th vocabulary it recovers maj7/min7/7/dim/sus.

    Confidence is the winning family bin's share of the row mass
    (winning_family_mass / row_sum) — a normalised [0,1] value on the scale the
    madmom / key-snap / mid-bar gates were tuned for, never the raw summed mass.
    Does NOT call simplify_chord(): the reduced labels already ARE the
    simplification (the M5 contract).
    """
    mass = np.asarray(mass, dtype=np.float64)
    n_beats = len(beat_times)
    if mass.ndim != 2 or mass.shape[0] != n_beats:
        raise ValueError(
            f"mass shape {getattr(mass, 'shape', None)} inconsistent with "
            f"{n_beats} beats")
    if mass.shape[1] != len(labels):
        raise ValueError(
            f"mass has {mass.shape[1]} columns but labels has {len(labels)}")

    # Per column: its (root, family) bin key, or None for the N (no-chord) sink.
    n_col: int | None = None
    bins: dict[tuple[int, str], list[int]] = {}
    for j, lbl in enumerate(labels):
        if ":" not in lbl:
            n_col = j                                  # 'N' (or any no-colon sink)
            continue
        root_str, quality = lbl.split(":", 1)
        root   = _ROOT_TO_SEMITONE.get(root_str)
        family = _REDUCED_QUALITY_TO_FAMILY.get(quality)
        if root is None or family is None:
            continue                                   # unexpected label: ignore
        bins.setdefault((root, family), []).append(j)

    beat_chords: list[dict] = []
    for i in range(n_beats):
        row     = mass[i]
        row_sum = float(row.sum())

        # Stage 1 — strongest {maj,min} family bin.
        best_cols, best_bin_mass = None, -1.0
        for cols in bins.values():
            m = float(row[cols].sum())
            if m > best_bin_mass:
                best_cols, best_bin_mass = cols, m
        n_mass = float(row[n_col]) if n_col is not None else 0.0

        if row_sum <= 1e-12 or best_cols is None or n_mass >= best_bin_mass:
            chord = "N"
            conf  = (n_mass / row_sum) if row_sum > 1e-12 else 0.0
        else:
            # Stage 2 — strongest quality column within the winning family.
            win   = best_cols[int(np.argmax(row[best_cols]))]
            chord = labels[win]
            conf  = best_bin_mass / row_sum

        beat_chords.append({
            "beat":       i + 1,
            "time":       float(beat_times[i]),
            "chord":      chord,
            "confidence": round(conf, 3),
        })

    return beat_chords


def _marginalize_crema_to_root_mode(probs: np.ndarray, vocab: list[str]) -> np.ndarray:
    """Collapse (T, 170) crema posteriors to (T, 24): 12 roots × {maj, min}.

    Major qualities (maj, maj7, 7, aug, sus2, sus4, …) sum into the major bin
    for that root.  Minor qualities (min, min7, dim, dim7, hdim7, minmaj7)
    sum into the minor bin.  The "N" (no chord) class is dropped.
    """
    T = probs.shape[0]
    out = np.zeros((T, 24), dtype=np.float64)
    for j, lbl in enumerate(vocab):
        if ":" not in lbl:
            continue
        root_str, quality = lbl.split(":", 1)
        root = _ROOT_TO_SEMITONE.get(root_str)
        if root is None:
            continue
        # Minor family
        is_minor = (
            quality.startswith("min")
            or quality.startswith("dim")
            or quality in ("hdim7",)
        )
        col = root * 2 + (1 if is_minor else 0)  # 0..23
        out[:, col] += probs[:, j]
    # Normalize each row so it's a proper distribution over the 24 classes
    row_sums = out.sum(axis=1, keepdims=True)
    row_sums[row_sums < 1e-12] = 1.0
    return out / row_sums


def _build_key_transition_matrix(
    key_root: int,
    key_mode: str,
    stay_prob: float = 0.35,
    in_key_base: float = 0.06,
    cadence_boost: float = 4.0,
    out_of_key_base: float = 0.005,
) -> np.ndarray:
    """Build a 24×24 transition matrix log P(chord_t | chord_{t-1}) in key.

    Each row corresponds to a (root, mode) source; each column to a (root,
    mode) destination.  The matrix is NOT row-normalised — Viterbi only uses
    relative log-probs, so leaving the absolute scale alone keeps things
    interpretable.  All numbers tuned by ear, not learned from data.
    """
    # Diatonic chord families (scale degree → expected mode):
    #   major key:  I (M), ii (m), iii (m), IV (M), V (M), vi (m), vii° (m proxy)
    #   minor key:  i (m), ii° (m), III (M), iv (m), v (m), VI (M), VII (M)
    if key_mode == "major":
        diatonic_major = {0, 5, 7}        # I, IV, V
        diatonic_minor = {2, 4, 9, 11}    # ii, iii, vi, vii° (modelled as m)
    else:
        diatonic_major = {3, 8, 10}       # III, VI, VII
        diatonic_minor = {0, 2, 5, 7}     # i, ii°, iv, v

    M = np.full((24, 24), out_of_key_base, dtype=np.float64)
    for src in range(24):
        for dst in range(24):
            src_root, src_mode = src // 2, "minor" if src % 2 else "major"
            dst_root, dst_mode = dst // 2, "minor" if dst % 2 else "major"
            dst_deg = (dst_root - key_root) % 12

            # Diatonic membership
            if dst_mode == "major" and dst_deg in diatonic_major:
                M[src, dst] = in_key_base
            elif dst_mode == "minor" and dst_deg in diatonic_minor:
                M[src, dst] = in_key_base

            # Cadence boosts (in major-key idiom; minor-key uses i/iv/v versions)
            src_deg = (src_root - key_root) % 12
            if key_mode == "major":
                if src_deg == 7 and dst_deg == 0 and dst_mode == "major":   # V → I
                    M[src, dst] *= cadence_boost
                elif src_deg == 5 and dst_deg == 0 and dst_mode == "major": # IV → I
                    M[src, dst] *= cadence_boost * 0.75
                elif src_deg == 2 and src_mode == "minor" and dst_deg == 7 and dst_mode == "major":  # ii → V
                    M[src, dst] *= cadence_boost * 0.75
                elif src_deg == 9 and src_mode == "minor" and dst_deg == 5 and dst_mode == "major":  # vi → IV
                    M[src, dst] *= 2.0
            else:
                if src_deg == 7 and dst_deg == 0 and dst_mode == "minor":   # v → i
                    M[src, dst] *= cadence_boost
                elif src_deg == 5 and src_mode == "minor" and dst_deg == 0 and dst_mode == "minor":  # iv → i
                    M[src, dst] *= cadence_boost * 0.75
                elif src_deg == 8 and src_mode == "major" and dst_deg == 3 and dst_mode == "major":  # VI → III
                    M[src, dst] *= 2.0

            # Self-loop (chord stays the same across bar boundary)
            if src == dst:
                M[src, dst] = stay_prob

    return np.log(M + 1e-12)


def _viterbi_decode(log_emit: np.ndarray, log_trans: np.ndarray) -> np.ndarray:
    """Standard Viterbi decode.
    log_emit : (T, K) log emission probabilities.
    log_trans: (K, K) log transition probabilities.
    Returns  : (T,)   sequence of int states.
    """
    T, K = log_emit.shape
    delta  = np.empty((T, K), dtype=np.float64)
    psi    = np.empty((T, K), dtype=np.int64)
    delta[0] = log_emit[0]
    psi[0]   = 0
    for t in range(1, T):
        # delta[t, j] = max_i (delta[t-1, i] + log_trans[i, j]) + log_emit[t, j]
        scores = delta[t - 1, :, None] + log_trans  # (K, K) broadcast
        psi[t]   = np.argmax(scores, axis=0)
        delta[t] = scores[psi[t], np.arange(K)] + log_emit[t]
    path = np.empty(T, dtype=np.int64)
    path[-1] = int(np.argmax(delta[-1]))
    for t in range(T - 2, -1, -1):
        path[t] = psi[t + 1, path[t + 1]]
    return path


def _bar_posteriors(
    bar_chords: list[dict],
    times: np.ndarray,
    chord_probs: np.ndarray,
) -> np.ndarray:
    """Sum crema's per-frame posteriors over each bar's time window.

    Returns (n_bars, n_classes).  Empty bars (no frames within window) get a
    uniform distribution so Viterbi falls back to transition prior alone.
    """
    n_bars = len(bar_chords)
    n_cls  = chord_probs.shape[1]
    out = np.zeros((n_bars, n_cls), dtype=np.float64)

    if n_bars >= 2:
        bar_duration = bar_chords[1]["time"] - bar_chords[0]["time"]
    else:
        bar_duration = 2.0

    for i, bar in enumerate(bar_chords):
        t_start = bar["time"]
        t_end   = (bar_chords[i + 1]["time"]
                   if i + 1 < n_bars else t_start + bar_duration)
        mask = (times >= t_start) & (times < t_end)
        if mask.any():
            out[i] = chord_probs[mask].mean(axis=0)
        else:
            out[i] = 1.0 / n_cls
    # Avoid log(0) downstream
    out = np.clip(out, 1e-12, 1.0)
    return out


def _apply_viterbi_smoothing(
    bar_chords: list[dict],
    times: np.ndarray,
    chord_probs: np.ndarray,
    vocab: list[str],
    key_root: int,
    key_mode: str,
    stay_prob: float = 0.35,
    cadence_boost: float = 4.0,
) -> tuple[list[dict], list[int]]:
    """
    Replace bar chord labels with the music-theory-aware Viterbi sequence.

    Steps:
      1. Per-bar crema posterior (n_bars, 170) ← sum over bar window.
      2. Marginalise to (n_bars, 24) on (root, mode).
      3. 24×24 transition matrix conditioned on (key_root, key_mode).
      4. Viterbi decode → per-bar (root, mode).
      5. For each bar where Viterbi disagrees with the current label's
         (root, mode), keep the original CHORD QUALITY by picking the
         highest-posterior 170-class chord whose root/mode matches Viterbi's
         pick (so "C:maj7" can become "G:maj7" but not "G:maj" if the
         original quality was 7th).  Mid-bar splits skipped — multi-segment
         bars indicate explicit fine-grained detection that we don't want
         Viterbi to flatten.

    Returns (bar_chords, list_of_bar_numbers_changed).
    """
    if not bar_chords or chord_probs.size == 0:
        return bar_chords, []

    # 1. Per-bar posteriors and marginalised root/mode posteriors
    bar_p     = _bar_posteriors(bar_chords, times, chord_probs)  # (n_bars, 170)
    rm_p      = _marginalize_crema_to_root_mode(bar_p, vocab)    # (n_bars, 24)
    log_emit  = np.log(rm_p + 1e-12)
    log_trans = _build_key_transition_matrix(
        key_root, key_mode, stay_prob=stay_prob, cadence_boost=cadence_boost,
    )

    # 2. Viterbi
    path = _viterbi_decode(log_emit, log_trans)   # (n_bars,) ints in [0..24)

    # 3. Apply — for single-segment bars where Viterbi disagrees, swap label
    changed: list[int] = []
    # Pre-index vocab by (root, mode) for fast best-quality lookup
    vocab_by_rm: dict[int, list[int]] = {}
    for j, lbl in enumerate(vocab):
        if ":" not in lbl:
            continue
        rs, q = lbl.split(":", 1)
        r = _ROOT_TO_SEMITONE.get(rs)
        if r is None:
            continue
        is_minor = q.startswith("min") or q.startswith("dim") or q == "hdim7"
        rm_idx = r * 2 + (1 if is_minor else 0)
        vocab_by_rm.setdefault(rm_idx, []).append(j)

    for i, bar in enumerate(bar_chords):
        if len(bar["segments"]) != 1:
            continue  # don't smooth bars with explicit mid-bar splits
        seg = bar["segments"][0]
        cur_label = seg["chord"]
        if ":" not in cur_label:
            continue
        cur_root_str, cur_q = cur_label.split(":", 1)
        cur_root = _ROOT_TO_SEMITONE.get(cur_root_str)
        if cur_root is None:
            continue
        cur_is_minor = cur_q.startswith("min") or cur_q.startswith("dim") or cur_q == "hdim7"
        cur_rm = cur_root * 2 + (1 if cur_is_minor else 0)

        new_rm = int(path[i])
        if new_rm == cur_rm:
            continue

        # Viterbi disagrees — pick best-posterior label with new (root, mode)
        candidates = vocab_by_rm.get(new_rm, [])
        if not candidates:
            continue
        best_j   = max(candidates, key=lambda j: bar_p[i, j])
        new_label = vocab[best_j]
        seg["chord"] = new_label
        # Posterior confidence for the new chord
        seg["confidence"] = float(bar_p[i, best_j])
        changed.append(bar["bar"])

    if changed:
        print(f"  [viterbi] {len(changed)} bar(s) relabelled by key-conditioned smoothing "
              f"(key: root={key_root}, mode={key_mode})")
    else:
        print("  [viterbi] no changes (crema already consistent with key prior)")
    return bar_chords, changed


# ---------------------------------------------------------------------------
# Bass-stem-anchored root correction
# ---------------------------------------------------------------------------
#
# Chord-detection errors split roughly into (a) wrong root and (b) wrong
# quality.  Root errors hurt the most musically and are the easy ones to fix:
# the bass note pins the chord root ~85 % of the time in pop / rock / folk.
#
# When stems are produced before chord detection (always true with this option
# enabled — pipeline.py forces stems_first and adds 'bass' to the stem filter),
# we recompute a per-segment dominant pitch class from the isolated bass WAV
# and override crema's root when:
#
#   1. the bass chroma is unambiguous (top-1 / (top-1 + top-2) ≥ margin), and
#   2. the bass pitch class differs from the chord root crema picked.
#
# Quality is preserved.  E.g. crema "Dm:7" + bass detects F → relabel "Fm:7"
# only if the user wants that; we instead keep quality unchanged: "F:m7".  In
# practice this corrects relative-minor / first-inversion confusions which
# crema gets wrong most often.

def _apply_bass_anchor(
    bar_chords: list[dict],
    bass_wav: str | None,
    sample_rate: int,
    margin_threshold: float = 0.55,
) -> tuple[list[dict], list[int]]:
    """
    Override chord roots in `bar_chords` using the bass stem's dominant pitch.

    Operates per segment (so a bar with a mid-bar split gets two independent
    bass-anchor checks).  Returns (updated_bar_chords, list_of_changed_bars).

    No-op if bass_wav is missing, the file doesn't exist, or chroma is too
    ambiguous (no clear single dominant pitch class).
    """
    if not bar_chords or not bass_wav or not os.path.isfile(bass_wav):
        return bar_chords, []

    import librosa
    print(f"  [bass-anchor] loading {os.path.basename(bass_wav)} …")
    y_bass, sr_bass = load_audio_mono(bass_wav, sample_rate)
    # Bass is typically <300 Hz; chroma_cqt with default fmin (~32.7 Hz) covers it
    chroma = librosa.feature.chroma_cqt(y=y_bass, sr=sr_bass)
    frame_times = librosa.frames_to_time(np.arange(chroma.shape[1]), sr=sr_bass)

    # Estimate bar duration to bound the last bar's window
    if len(bar_chords) >= 2:
        bar_duration = bar_chords[1]["time"] - bar_chords[0]["time"]
    else:
        bar_duration = 2.0

    changed_bars: set[int] = set()
    for i, bar in enumerate(bar_chords):
        bar_start = bar["time"]
        bar_end   = (bar_chords[i + 1]["time"]
                     if i + 1 < len(bar_chords) else bar_start + bar_duration)
        bar_dur   = bar_end - bar_start
        total_beats = sum(s["beats"] for s in bar["segments"])
        if total_beats == 0:
            continue
        beat_dur = bar_dur / total_beats

        cur_t = bar_start
        new_segments = []
        for seg in bar["segments"]:
            seg_start = cur_t
            seg_end   = cur_t + seg["beats"] * beat_dur
            cur_t     = seg_end

            mask = (frame_times >= seg_start) & (frame_times < seg_end)
            if not mask.any():
                new_segments.append(seg)
                continue

            seg_chroma = chroma[:, mask].mean(axis=1)
            sorted_c   = np.sort(seg_chroma)[::-1]
            margin = (sorted_c[0] / (sorted_c[0] + sorted_c[1])
                      if sorted_c[0] + sorted_c[1] > 1e-6 else 0.0)
            if margin < margin_threshold:
                new_segments.append(seg)
                continue

            bass_pc = int(np.argmax(seg_chroma))
            chord_label = seg["chord"]
            if ":" not in chord_label:
                new_segments.append(seg)
                continue
            root_str, quality = chord_label.split(":", 1)
            chord_root_pc = _ROOT_TO_SEMITONE.get(root_str)
            if chord_root_pc is None or chord_root_pc == bass_pc:
                new_segments.append(seg)
                continue

            new_root = _SEMITONE_TO_ROOT_FLAT[bass_pc]
            new_segments.append({**seg, "chord": f"{new_root}:{quality}"})
            changed_bars.add(bar["bar"])

        bar["segments"] = new_segments

    print(f"  [bass-anchor] {len(changed_bars)} bar(s) had their root re-anchored to the bass stem")
    return bar_chords, sorted(changed_bars)


# ---------------------------------------------------------------------------
# Section-aware chord consistency
# ---------------------------------------------------------------------------
#
# allin1 gives us Intro / Verse / Chorus / Bridge / Outro boundaries.  In most
# pop / rock songs the same section name has the same chord progression every
# time.  When crema disagrees with itself between Verse 1 and Verse 2 the user
# spots it instantly on the chart, even if either pass alone looks fine.
#
# Algorithm: group bars by section label.  For each label with ≥ 2 instances
# of identical length, walk position-by-position and pick the chord set from
# the instance with the highest mean confidence at that position; copy its
# chord labels to all other instances (timing and segment structure preserved
# from each target bar).
#
# Skips a label when instances have varying bar counts — re-aligning a 6-bar
# chorus with an 8-bar chorus is too risky to do silently.

def _apply_section_consistency(
    bar_chords: list[dict],
    sections: list[dict],
) -> tuple[list[dict], list[int]]:
    """
    Force same-named sections to share their chord progressions, picking the
    highest-confidence bar at each position as the winner.

    No-op when:
      - sections is empty
      - a label appears only once
      - instances of a label have different bar counts (logged then skipped)
      - segment structures differ between winner and loser (avoid alignment
        across mid-bar-split mismatches)

    Returns (bar_chords, list_of_changed_bars).
    """
    if not bar_chords or not sections:
        return bar_chords, []

    by_label: dict[str, list[dict]] = {}
    for sec in sections:
        by_label.setdefault(sec["label"], []).append(sec)

    bar_by_num = {b["bar"]: b for b in bar_chords}
    changed_bars: set[int] = set()

    for label, instances in by_label.items():
        if len(instances) < 2:
            continue
        lengths = [sec["end_bar"] - sec["start_bar"] + 1 for sec in instances]
        if len(set(lengths)) > 1:
            print(f"  [section-consistency] '{label}' instances have varying lengths "
                  f"{lengths} — skipping")
            continue
        length = lengths[0]

        for offset in range(length):
            bars_at_position: list[tuple[dict, float]] = []
            for sec in instances:
                bar_num = sec["start_bar"] + offset
                if bar_num not in bar_by_num:
                    continue
                bar = bar_by_num[bar_num]
                if not bar["segments"]:
                    continue
                mean_conf = sum(s["confidence"] for s in bar["segments"]) / len(bar["segments"])
                bars_at_position.append((bar, mean_conf))

            if len(bars_at_position) < 2:
                continue

            winner_bar, _winner_conf = max(bars_at_position, key=lambda c: c[1])
            winner_segments = winner_bar["segments"]
            winner_chords   = [s["chord"] for s in winner_segments]

            for bar, _ in bars_at_position:
                if bar is winner_bar:
                    continue
                # Skip if segment structure differs — aligning a 1-segment bar to a
                # 2-segment (mid-bar-split) winner would distort timing.
                if len(bar["segments"]) != len(winner_segments):
                    continue
                # Compare (chord, bass_pc) pairs so an inversion change is
                # also treated as a difference worth correcting.
                cur_pairs = [(s["chord"], s.get("bass_pc")) for s in bar["segments"]]
                win_pairs = [(s["chord"], s.get("bass_pc")) for s in winner_segments]
                if cur_pairs == win_pairs:
                    continue
                bar["segments"] = [
                    {**orig, "chord": win["chord"], "bass_pc": win.get("bass_pc")}
                    for orig, win in zip(bar["segments"], winner_segments)
                ]
                changed_bars.add(bar["bar"])

    if changed_bars:
        print(f"  [section-consistency] {len(changed_bars)} bar(s) updated to match "
              "best-confidence instance within their section")
    else:
        print("  [section-consistency] no changes (sections already consistent or "
              "labels appear only once)")
    return bar_chords, sorted(changed_bars)


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
    # crema_probs is the full per-frame posterior matrix (n_frames, n_classes);
    # crema_vocab maps column index → label. Needed by --viterbi-smoothing.
    times, confidence, labels, crema_probs, crema_vocab = detect_chords_crema(y_chords, sr)
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

    # ── Viterbi smoothing ──
    # Runs BEFORE bass-anchor / slash so the corrected sequence flows into
    # the bass-driven passes.  Bars whose Viterbi pick disagrees with crema
    # get their chord label replaced with the best-posterior chord of the
    # Viterbi-picked (root, mode).
    viterbi_relabeled: list[int] = []
    if args.viterbi_smoothing:
        bar_chords, viterbi_relabeled = _apply_viterbi_smoothing(
            bar_chords, times, crema_probs, crema_vocab,
            key_root=key_root, key_mode=key_mode,
            stay_prob=args.viterbi_stay_prob,
            cadence_boost=args.viterbi_cadence_boost,
        )

    # ── Bass-anchored root correction ──
    # Runs after madmom-fallback, key-snap, and Viterbi (so they all get a
    # chance to fix the chord first) but BEFORE slash-chord tagging (which
    # operates on the *corrected* root) and section-consistency.
    bass_anchored: list[int] = []
    if args.bass_anchor:
        if not args.bass_wav:
            print("  [bass-anchor] enabled but --bass-wav not provided — skipping")
        else:
            bar_chords, bass_anchored = _apply_bass_anchor(
                bar_chords, args.bass_wav, args.sample_rate,
                margin_threshold=args.bass_anchor_margin,
            )

    # ── Slash chord (inversion) labelling ──
    # Reuses the bass stem.  Tags seg["bass_pc"] when bass is a chord tone
    # other than the root; renderers consult this for slash notation.
    slash_chord_bars: list[int] = []
    if args.slash_chords:
        if not args.bass_wav:
            print("  [slash-chords] enabled but --bass-wav not provided — skipping")
        else:
            bar_chords, slash_chord_bars = _apply_slash_chords(
                bar_chords, args.bass_wav, args.sample_rate,
                margin_threshold=args.bass_anchor_margin,
            )

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

    # ── Section-aware chord consistency ──
    # Run AFTER sections are loaded; needs both bar_chords and sections.
    # Idempotent and bounded — never invents data, only copies winning chords
    # across same-named instances.  Recompute low_pct since chords may change.
    section_consistent: list[int] = []
    if args.section_consistency:
        if not sections:
            print("  [section-consistency] enabled but no sections detected — skipping")
        else:
            bar_chords, section_consistent = _apply_section_consistency(bar_chords, sections)
            if section_consistent:
                # Recount low-confidence segments — winning chord may have higher conf
                all_segs = [seg for bar in bar_chords for seg in bar["segments"]]
                low_conf = sum(1 for s in all_segs if s["confidence"] < args.threshold)
                low_pct  = 100 * low_conf / max(len(all_segs), 1)

    # Optional: dump the detected chord sequence as a Harte-style .lab
    # (start  end  label, in seconds) for the eval harness.  Written here —
    # after every chord-correction pass but before LilyPond rendering — so the
    # labels are captured even if PDF rendering later fails.  Labels are the
    # crema "root:quality" strings the codebase already uses (Harte-compatible,
    # so mir_eval parses them directly); bass/inversion is omitted.
    if args.lab_out:
        _interval = float(np.median(np.diff(beat_times))) if len(beat_times) > 1 else 0.5
        with open(args.lab_out, "w") as _lab:
            for _bar in bar_chords:
                for _seg in _bar["segments"]:
                    _start = float(_seg["time"])
                    _end   = _start + _seg["beats"] * _interval
                    _lbl   = _seg["chord"] if _seg["chord"] not in ("", "X") else "N"
                    _lab.write(f"{_start:.4f}\t{_end:.4f}\t{_lbl}\n")
        print(f"  Chord .lab: {args.lab_out}")

    madmom_bar_set      = set(madmom_substituted)
    key_snap_bar_set    = set(key_snapped)
    bass_anchor_set     = set(bass_anchored)
    section_consist_set = set(section_consistent)
    slash_chord_set     = set(slash_chord_bars)
    viterbi_set         = set(viterbi_relabeled)
    print("\n  Chord summary (changes only):")
    prev = None
    for bar in bar_chords:
        beat_pos = 1
        tags = []
        if bar["bar"] in madmom_bar_set:      tags.append("madmom")
        if bar["bar"] in key_snap_bar_set:    tags.append("key snap")
        if bar["bar"] in bass_anchor_set:     tags.append("bass-anchor")
        if bar["bar"] in section_consist_set: tags.append("section-consistency")
        if bar["bar"] in slash_chord_set:     tags.append("slash")
        if bar["bar"] in viterbi_set:         tags.append("viterbi")
        tag = f"  [{', '.join(tags)}]" if tags else ""
        for seg in bar["segments"]:
            # Pair chord identity with its bass annotation so a change of bass
            # (same chord, different inversion) also gets printed.
            seg_id = (seg["chord"], seg.get("bass_pc"))
            if seg_id != prev:
                flag   = " ?" if seg["confidence"] < args.threshold else ""
                prefix = f"Bar {bar['bar']:>3}" if beat_pos == 1 else f"      beat {beat_pos}"
                display = crema_to_display(seg["chord"], use_sharps, seg.get("bass_pc"))
                print(f"    {prefix}  {seg['time']:>6.1f}s  {display:<10}  ({seg['confidence']:.0%}{flag}){tag if beat_pos == 1 else ''}")
                prev = seg_id
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
            bpm           = bpm,
            bars_per_line = args.bars_per_line,
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

#!/usr/bin/env python3
"""
beat_stabilizer.py - Stabilize audio to a consistent BPM grid.

Detects beats in an audio file (or accepts a manual BPM), then warps
the audio so every beat lands on an even musical grid — like Ableton's
"warp to grid" feature but from the command line.

Beat detection uses madmom's RNN + DBN tracker (via venv_madmom) when
available — significantly more accurate than librosa, especially for
half-time grooves and irregular rhythms.  Librosa is the fallback.

After saving, writes a <output>.bpm sidecar file so downstream tools
(chord_chart_render.py, pipeline.py) can pick up the exact target BPM
without re-detecting it.

Half-time detection
-------------------
When --bpm is supplied, the detected beat count is compared against the
expected count (target_bpm / 60 × duration).  If the ratio is ≈ 2, the
tracker locked onto 8th notes instead of quarter notes.  All detected
beats are kept as warp anchors (2× correction density) but mapped to
the 8th-note grid, so the output plays at the correct quarter-note tempo
without stretching the file to double length.

Intro trim (on by default)
--------------------------
The output starts exactly one bar before the first detected beat.  This
makes it trivial to drop the file into a DAW: set the project tempo, put
the clip at bar 1 beat 1, and the music lines up immediately.  If the
first beat is within one bar of the file start, silence is prepended
instead of trimming.  Disable with --no-trim-intro.

Usage:
    python3 beat_stabilizer.py -i input.mp3 -o output.wav
    python3 beat_stabilizer.py -i input.wav -o output.wav --bpm 120
    python3 beat_stabilizer.py -i input.aiff -o output.wav --strength 0.75
    python3 beat_stabilizer.py -i song.m4a --detect-only
    python3 beat_stabilizer.py -i song.wav -o out.wav --no-trim-intro
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time

import numpy as np
import soundfile as sf
import librosa
import pyrubberband as pyrb


# ---------------------------------------------------------------------------
# Progress reporting (enabled by --progress-json; no-op otherwise)
# ---------------------------------------------------------------------------

_PROGRESS_JSON = False


def _emit(sub: str, pct: float, msg: str | None = None) -> None:
    """Emit a single PROGRESS JSON line on stdout.

    `pct` is a local 0.0–1.0 value. pipeline.py remaps to the global range.
    """
    if not _PROGRESS_JSON:
        return
    payload = {"sub": sub, "pct": float(pct)}
    if msg:
        payload["msg"] = msg
    sys.stdout.write(f"PROGRESS {json.dumps(payload)}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

SUPPORTED_INPUT = {".mp3", ".wav", ".aiff", ".aif", ".m4a", ".flac", ".ogg"}


def load_audio(path: str, sr: int = 44100) -> tuple[np.ndarray, int]:
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_INPUT:
        sys.exit(f"Unsupported format: {ext}. Supported: {', '.join(SUPPORTED_INPUT)}")

    if ext in {".mp3", ".m4a", ".aiff", ".aif"}:
        try:
            from pydub import AudioSegment
        except ImportError:
            sys.exit("pydub is required for mp3/m4a/aiff: pip install pydub")
        seg = AudioSegment.from_file(path)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            seg.export(tmp_path, format="wav")
            y, sr_orig = sf.read(tmp_path, always_2d=False)
        finally:
            os.unlink(tmp_path)
    else:
        y, sr_orig = sf.read(path, always_2d=False)

    if sr_orig != sr:
        print(f"  Resampling {sr_orig} Hz → {sr} Hz …")
        if y.ndim == 2:
            y = librosa.resample(y.T, orig_sr=sr_orig, target_sr=sr).T
        else:
            y = librosa.resample(y, orig_sr=sr_orig, target_sr=sr)

    return y, sr


def save_audio(path: str, y: np.ndarray, sr: int) -> str:
    """Save audio and return the actual path written."""
    ext = os.path.splitext(path)[1].lower()
    if ext in {".mp3", ".m4a"}:
        print(f"  Note: saving as WAV (lossless).")
        path = os.path.splitext(path)[0] + ".wav"
    sf.write(path, y, sr)
    print(f"  Wrote: {path}  ({len(y)/sr:.2f}s)")
    return path


def write_bpm_sidecar(audio_path: str, bpm: float) -> None:
    """Write <audio_path>.bpm so downstream tools know the exact target BPM."""
    sidecar = audio_path + ".bpm"
    with open(sidecar, "w") as f:
        f.write(f"{bpm}\n")
    print(f"  BPM sidecar: {sidecar}")


# ---------------------------------------------------------------------------
# Beat detection
# ---------------------------------------------------------------------------

def detect_beats_madmom(
    y_mono: np.ndarray,
    sr: int,
    bpb_options: list[int] | None = None,
    fps: int = 100,
    timeout_s: int = 240,
) -> tuple[np.ndarray, np.ndarray | None, int | None]:
    """
    Run madmom's RNN + DBN tracker, preferring the downbeat-aware version.

    Tries (in order):
      1. `DBNDownBeatTrackingProcessor` with the candidate `bpb_options`
         (defaults to [3, 4]) — gives us beat times, downbeat indices, and a
         detected meter in a single pass.
      2. `DBNBeatTrackingProcessor` if (1) errors — beats only, no downbeats.

    Tries `venv_madmom` via subprocess first (preferred — avoids import
    conflicts with the host Python). Falls back to a direct import only if
    madmom happens to be installed in the active environment.

    Returns
    -------
    beat_times       : (n_beats,)  seconds
    downbeat_indices : (n_downbeats,) int — indices into beat_times where
                        beat 1 falls; None if the downbeat tracker errored.
    meter            : int beats-per-bar; None if not detected.
    """
    import json, subprocess

    if bpb_options is None:
        bpb_options = [3, 4]

    script_dir    = os.path.dirname(os.path.abspath(__file__))
    madmom_python = os.path.join(script_dir, "venv_madmom", "bin", "python3.11")

    if os.path.isfile(madmom_python):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            sf.write(tmp_path, y_mono.astype(np.float32), sr)
            # The child script tries the downbeat tracker first and falls
            # back to beats-only on failure. Output is JSON.
            code = f"""
# madmom pins are old enough that it imports MutableSequence/etc. directly
# from `collections`, which Python 3.10+ moved to `collections.abc`. Patch
# the names back onto `collections` before anything from madmom is imported.
import collections, collections.abc
for _name in ('MutableSequence', 'Mapping', 'MutableMapping', 'Iterable',
              'Hashable', 'Callable', 'Sequence', 'Set', 'MutableSet'):
    if not hasattr(collections, _name):
        setattr(collections, _name, getattr(collections.abc, _name))
import numpy as _np
_np.int = int; _np.float = float; _np.complex = complex
_np.bool = bool; _np.object = object; _np.str = str
import sys, json
import numpy as np
import itertools as it

audio = sys.argv[1]
bpb = {bpb_options!r}
FPS = {fps!r}

# Monkey-patch DBNDownBeatTrackingProcessor.process(): the upstream
# implementation does `np.asarray(results)[:, 1]` where results is a list of
# (path_array, log_prob_float). NumPy >=1.20 refuses to coerce inhomogeneous
# nested sequences into a uniform array, raising ValueError. We replace the
# argmax with an explicit log-prob extraction and leave the rest of the
# method byte-identical.
from madmom.features.downbeats import DBNDownBeatTrackingProcessor, _process_dbn

def _patched_process(self, activations, **kwargs):
    first = 0
    if self.threshold:
        idx = np.nonzero(activations >= self.threshold)[0]
        if idx.any():
            first = max(first, int(np.min(idx)))
            last = min(len(activations), int(np.max(idx)) + 1)
        else:
            last = first
        activations = activations[first:last]
    if not activations.any():
        return np.empty((0, 2))
    results = list(self.map(_process_dbn, zip(self.hmms, it.repeat(activations))))
    log_probs = [r[1] for r in results]
    best = int(np.argmax(log_probs))
    path, _ = results[best]
    st = self.hmms[best].transition_model.state_space
    om = self.hmms[best].observation_model
    positions = st.state_positions[path]
    beat_numbers = positions.astype(int) + 1
    if self.correct:
        beats = np.empty(0, dtype=int)
        beat_range = om.pointers[path] >= 1
        idx = np.nonzero(np.diff(beat_range.astype(int)))[0] + 1
        if beat_range[0]:
            idx = np.r_[0, idx]
        if beat_range[-1]:
            idx = np.r_[idx, beat_range.size]
        if idx.any():
            for left, right in idx.reshape((-1, 2)):
                peak = int(np.argmax(activations[left:right])) // 2 + left
                beats = np.hstack((beats, peak))
    else:
        beats = np.nonzero(np.diff(beat_numbers))[0] + 1
    return np.vstack(((beats + first) / float(self.fps),
                      beat_numbers[beats])).T

DBNDownBeatTrackingProcessor.process = _patched_process

try:
    from madmom.features.downbeats import RNNDownBeatProcessor
    act = RNNDownBeatProcessor()(audio)
    out = DBNDownBeatTrackingProcessor(beats_per_bar=bpb, fps=FPS)(act)
    # out shape: (n, 2) — (time_seconds, position_in_bar_1_indexed)
    arr = np.asarray(out)
    beats = arr[:, 0].tolist()
    positions = arr[:, 1].astype(int).tolist()
    downbeat_idx = [i for i, p in enumerate(positions) if p == 1]
    meter = max(positions) if positions else None
    print(json.dumps({{
        "beats": beats,
        "downbeat_indices": downbeat_idx,
        "meter": meter,
        "tracker": "downbeat",
    }}))
except Exception as e:
    # Fall back to the beats-only tracker.
    from madmom.features.beats import RNNBeatProcessor, DBNBeatTrackingProcessor
    act = RNNBeatProcessor()(audio)
    beats = DBNBeatTrackingProcessor(fps=FPS)(act)
    print(json.dumps({{
        "beats": np.asarray(beats).tolist(),
        "downbeat_indices": None,
        "meter": None,
        "tracker": "beats_only",
        "downbeat_error": repr(e)[:200],
    }}), file=sys.stderr)
    # Re-emit as JSON on stdout so the parent can read it.
    print(json.dumps({{
        "beats": np.asarray(beats).tolist(),
        "downbeat_indices": None,
        "meter": None,
        "tracker": "beats_only",
    }}))
"""
            result = subprocess.run(
                [madmom_python, "-c", code, tmp_path],
                capture_output=True, text=True, timeout=timeout_s,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr[-400:])
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            beats = np.array(payload["beats"], dtype=float)
            dbi   = (np.array(payload["downbeat_indices"], dtype=int)
                     if payload["downbeat_indices"] is not None else None)
            meter = payload.get("meter")
            return beats, dbi, meter
        finally:
            os.unlink(tmp_path)

    # Direct import fallback (only works when madmom is in the active env).
    # Downbeat tracker is too heavy to attempt here without venv isolation.
    from madmom.features.beats import RNNBeatProcessor, DBNBeatTrackingProcessor
    sig = y_mono.astype(np.float32)
    act = RNNBeatProcessor()(sig)
    return np.asarray(DBNBeatTrackingProcessor(fps=fps)(act), dtype=float), None, None


def detect_beats_librosa(
    y_mono: np.ndarray,
    sr: int,
    start_bpm: float = 120.0,
    tightness: float = 100.0,
    hop_length: int = 512,
) -> tuple[np.ndarray, float]:
    tempo, beat_frames = librosa.beat.beat_track(
        y=y_mono, sr=sr, units="frames",
        start_bpm=start_bpm, tightness=tightness, hop_length=hop_length,
    )
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop_length)

    if len(beat_times) < 2:
        print("  [librosa] beat_track found <2 beats, switching to onset detection …")
        beat_times = librosa.onset.onset_detect(y=y_mono, sr=sr, units="time", hop_length=hop_length)
        tempo = _bpm_from_times(beat_times) if len(beat_times) >= 2 else 120.0

    return np.asarray(beat_times, dtype=float), float(np.atleast_1d(tempo)[0])


def _madmom_heartbeat(stop: threading.Event, duration_s: float) -> None:
    """Emit synthetic progress ticks while madmom subprocess is blocking.

    Interpolates pct from 0.16 → 0.48 over an estimated runtime so the
    frontend bar visibly moves instead of freezing for 30–90 s.
    """
    expected_s = max(30.0, duration_s * 0.6)
    start = time.monotonic()
    while not stop.is_set():
        time.sleep(4.0)
        if stop.is_set():
            break
        elapsed = time.monotonic() - start
        frac = min(elapsed / expected_s, 0.90)
        _emit("detect_beats", 0.16 + frac * (0.48 - 0.16))


def detect_beats(
    y: np.ndarray,
    sr: int,
    bpb_options: list[int] | None = None,
    backend: str = "auto",
    madmom_fps: int = 100,
    madmom_timeout_s: int = 240,
    librosa_start_bpm: float = 120.0,
    librosa_tightness: float = 100.0,
    librosa_hop_length: int = 512,
) -> tuple[np.ndarray, float, np.ndarray | None, int | None]:
    """
    Beat (and downbeat, when available) detection.

    `backend` is one of:
      - "auto"    : try madmom, fall back to librosa
      - "madmom"  : madmom only (errors propagate)
      - "librosa" : librosa only (no downbeat info)

    Returns (beat_times, bpm, downbeat_indices, meter).
    `downbeat_indices` and `meter` are None when the downbeat tracker
    couldn't run or the librosa backend was used.
    """
    y_mono = y.mean(axis=1) if y.ndim == 2 else y
    duration_s = (len(y) if y.ndim == 1 else y.shape[0]) / sr

    if backend == "librosa":
        beat_times, bpm = detect_beats_librosa(
            y_mono, sr,
            start_bpm=librosa_start_bpm,
            tightness=librosa_tightness,
            hop_length=librosa_hop_length,
        )
        print(f"  [librosa] {len(beat_times)} beats  |  {bpm:.2f} BPM")
        return beat_times, bpm, None, None

    stop_event = threading.Event()
    if _PROGRESS_JSON:
        t = threading.Thread(target=_madmom_heartbeat, args=(stop_event, duration_s), daemon=True)
        t.start()
    else:
        t = None

    try:
        beat_times, downbeat_indices, meter = detect_beats_madmom(
            y_mono, sr,
            bpb_options=bpb_options,
            fps=madmom_fps,
            timeout_s=madmom_timeout_s,
        )
        bpm = _bpm_from_times(beat_times)
        if downbeat_indices is not None and meter is not None:
            print(f"  [madmom] {len(beat_times)} beats, {len(downbeat_indices)} downbeats  "
                  f"|  {bpm:.2f} BPM  |  meter ≈ {meter}/4")
        else:
            print(f"  [madmom] {len(beat_times)} beats  |  {bpm:.2f} BPM  (downbeats unavailable)")
        return beat_times, bpm, downbeat_indices, meter
    except Exception as e:
        if backend == "madmom":
            raise
        print(f"  [madmom] not available ({e}), falling back to librosa …")
    finally:
        stop_event.set()
        if t is not None:
            t.join(timeout=1)

    beat_times, bpm = detect_beats_librosa(
        y_mono, sr,
        start_bpm=librosa_start_bpm,
        tightness=librosa_tightness,
        hop_length=librosa_hop_length,
    )
    print(f"  [librosa] {len(beat_times)} beats  |  {bpm:.2f} BPM")
    return beat_times, bpm, None, None


def _bpm_from_times(beat_times: np.ndarray) -> float:
    if len(beat_times) < 2:
        return 120.0
    return float(60.0 / np.median(np.diff(beat_times)))


# ---------------------------------------------------------------------------
# Tempo-change detection (arrangement-level, not jitter)
# ---------------------------------------------------------------------------

def normalize_beat_octaves(
    beat_times: np.ndarray,
    fold_tol: float = 0.18,
    ema_alpha: float = 0.3,
) -> tuple[np.ndarray, dict]:
    """
    Collapse mid-song beat-tracker octave flips into a single quarter-note grid.

    Beat trackers commonly relock between quarter-note and eighth-note
    spacing within a single song (especially around section boundaries with
    different rhythmic density).  Once the lock flips, the downstream warper
    stretches the doubled-density section to 2× its original duration —
    correct for what the tracker LABELLED but musically catastrophic.

    Algorithm: walk the beat list while maintaining a running estimate `T` of
    the true quarter-note interval (initialised from the first 4 intervals).
    For each next beat:
      - gap ≈ T / 2  → 8th-note lock; drop the beat (don't add to output).
      - gap ≈ 2 T    → half-note lock; insert one midpoint, add the beat.
      - gap ≈ T      → normal; add the beat and EMA-update T.
      - anything else → add the beat without touching T (avoids letting noise
                        drift the estimate).

    Returns (normalised_beat_times, info_dict) where info_dict reports the
    number of skip / insert events and the final T estimate so the caller
    can log a structured summary.
    """
    info = {"dropped": 0, "inserted": 0, "final_T": 0.0}
    if len(beat_times) < 6:
        info["final_T"] = float(np.median(np.diff(beat_times))) if len(beat_times) >= 2 else 0.0
        return beat_times, info

    intervals = np.diff(beat_times)
    T = float(np.median(intervals[:min(4, len(intervals))]))
    if T <= 0:
        info["final_T"] = T
        return beat_times, info

    result: list[float] = [float(beat_times[0])]
    i = 1
    while i < len(beat_times):
        gap = float(beat_times[i]) - result[-1]
        ratio = gap / T

        if abs(ratio - 0.5) < 0.5 * fold_tol:
            # 8th-note lock — skip the doubled beat
            info["dropped"] += 1
            i += 1
        elif abs(ratio - 2.0) < 2.0 * fold_tol:
            # Half-note lock — insert one midpoint to restore quarter-note grid
            mid = result[-1] + T
            result.append(mid)
            result.append(float(beat_times[i]))
            info["inserted"] += 1
            # Update T from the inserted half (mid → beat[i]) so we adapt to
            # any subtle drift inside the half-locked region
            T = (1 - ema_alpha) * T + ema_alpha * (float(beat_times[i]) - mid)
            i += 1
        elif abs(ratio - 1.0) < fold_tol:
            # Normal beat — keep and EMA-update T
            result.append(float(beat_times[i]))
            T = (1 - ema_alpha) * T + ema_alpha * gap
            i += 1
        else:
            # Ambiguous (rubato, dropped/added beat, etc.) — keep, don't update T
            result.append(float(beat_times[i]))
            i += 1

    info["final_T"] = T
    return np.array(result, dtype=float), info


def _octave_fold_bpm(bpm: float, reference: float, fold_tol: float = 0.15) -> float:
    """Fold `bpm` by ×2 or ×0.5 when doing so brings it close to `reference`.

    Beat trackers regularly produce octave errors: a 100-BPM song reads as
    either 100 or 200 depending on whether the tracker locked onto quarter
    notes or eighth notes.  When the lock flips mid-song we get a "phantom
    tempo change" that's really just one tempo viewed at two metrical levels.

    Returns the folded value only when it lands within `fold_tol` (relative)
    of the reference; otherwise returns `bpm` unchanged.  This preserves true
    1.5× / 1.3× tempo changes (which should still trip the guard) while
    silently collapsing clean 2.0× / 0.5× octave flips.
    """
    if bpm <= 0 or reference <= 0:
        return bpm
    candidates = (bpm, bpm * 0.5, bpm * 2.0)
    best = min(candidates, key=lambda x: abs(x - reference))
    if best is bpm:
        return bpm
    if abs(best - reference) / reference <= fold_tol:
        return best
    return bpm


def detect_tempo_change(
    beat_times: np.ndarray,
    downbeat_indices: np.ndarray | None,
    window_bars: int = 8,
    persist_bars: int = 4,
    threshold_pct: float = 0.06,
    threshold_floor_bpm: float = 6.0,
    octave_fold_tol: float = 0.15,
) -> dict | None:
    """
    Detect a sustained arrangement-level tempo change.

    The current pipeline assumes a single global BPM and warps everything to
    it. That's fine for AI jitter or human rubato (high-frequency wobble
    around a stable mean) but wrong for songs with real section-level tempo
    shifts (e.g. ballad section at 70 BPM, chorus at 90 BPM). For the latter
    the warp would produce audibly broken output, so we'd rather stop and
    tell the user.

    Algorithm:
      1. Compute per-bar median beat interval → per-bar BPM.
         If downbeats are unavailable, treat groups of 4 beats as a bar
         (acceptable fallback — the smoothing window absorbs the error).
      2. Rolling-median over `window_bars` to flatten jitter/rubato.
      3. Scan for sustained step changes: |Δbpm| ≥ max(6, 6%) of the base
         BPM, that persists for at least `persist_bars` more windows
         without returning to the prior level.

    Returns
    -------
    None if no change is detected, else
    {"from_bpm": float, "to_bpm": float, "at_bar": int, "at_time_s": float}.
    """
    if len(beat_times) < (window_bars + persist_bars) * 4:
        # Too short to reason about — let it through.
        return None

    intervals = np.diff(beat_times)
    if intervals.size == 0:
        return None

    # Group beats into bars.
    if downbeat_indices is not None and len(downbeat_indices) >= 4:
        # Use real bar boundaries.
        bar_bpms: list[float] = []
        bar_starts_t: list[float] = []
        for i in range(len(downbeat_indices) - 1):
            lo = int(downbeat_indices[i])
            hi = int(downbeat_indices[i + 1])
            if hi <= lo + 1:
                continue
            bar_intervals = intervals[lo:hi - 1] if hi - 1 <= len(intervals) else intervals[lo:]
            if len(bar_intervals) == 0:
                continue
            median_interval = float(np.median(bar_intervals))
            bar_bpms.append(60.0 / median_interval)
            bar_starts_t.append(float(beat_times[lo]))
    else:
        # No downbeats → assume 4 beats per bar.
        beats_per_bar = 4
        n_bars = len(intervals) // beats_per_bar
        if n_bars < window_bars + persist_bars:
            return None
        bar_bpms = []
        bar_starts_t = []
        for b in range(n_bars):
            lo = b * beats_per_bar
            hi = lo + beats_per_bar
            median_interval = float(np.median(intervals[lo:hi]))
            bar_bpms.append(60.0 / median_interval)
            bar_starts_t.append(float(beat_times[lo]))

    bpms = np.array(bar_bpms, dtype=float)
    if len(bpms) < window_bars + persist_bars + 1:
        return None

    # Rolling median over window_bars (centred).
    half = window_bars // 2
    smoothed = np.empty_like(bpms)
    for i in range(len(bpms)):
        lo = max(0, i - half)
        hi = min(len(bpms), i + half + 1)
        smoothed[i] = float(np.median(bpms[lo:hi]))

    # Scan for step changes.
    # We look for: smoothed[i] differs from smoothed[i-1] by ≥ threshold,
    # AND smoothed[i+1..i+persist_bars] also stays on the new level (within
    # half the threshold of smoothed[i]).
    #
    # Before each comparison, octave-fold the candidate BPM against the
    # reference: a 105 → 207 flip becomes 105 → 103.5 (the second half is
    # the same tempo, just heard in eighth notes by the tracker).  See
    # _octave_fold_bpm for the rationale and tolerance.
    for i in range(window_bars, len(smoothed) - persist_bars):
        prev_bpm = float(smoothed[i - 1])
        curr_bpm = _octave_fold_bpm(float(smoothed[i]), prev_bpm, octave_fold_tol)
        delta = abs(curr_bpm - prev_bpm)
        threshold = max(threshold_floor_bpm, threshold_pct * prev_bpm)
        if delta < threshold:
            continue
        # Check persistence: every smoothed value in [i, i+persist_bars]
        # (also octave-folded against prev_bpm) must stay within threshold/2
        # of curr_bpm.
        persistence_window = np.array([
            _octave_fold_bpm(float(b), prev_bpm, octave_fold_tol)
            for b in smoothed[i: i + persist_bars + 1]
        ])
        if np.all(np.abs(persistence_window - curr_bpm) < threshold / 2):
            return {
                "from_bpm": round(prev_bpm, 2),
                "to_bpm":   round(curr_bpm, 2),
                "at_bar":   i + 1,  # 1-indexed bar number
                "at_time_s": round(bar_starts_t[i], 2),
            }
    return None


# ---------------------------------------------------------------------------
# Beat stabilisation (warping)
# ---------------------------------------------------------------------------

def build_timemap(beat_samples: np.ndarray, target_samples: np.ndarray, n_total: int) -> np.ndarray:
    pairs: list[tuple[int, int]] = []

    if beat_samples[0] > 0:
        pairs.append((0, 0))

    for s, t in zip(beat_samples.tolist(), target_samples.tolist()):
        pairs.append((int(s), int(t)))

    last_src, last_tgt = pairs[-1]
    pairs.append((int(n_total), int(last_tgt + (n_total - last_src))))

    clean: list[tuple[int, int]] = [pairs[0]]
    for s, t in pairs[1:]:
        if s > clean[-1][0] and t > clean[-1][1]:
            clean.append((s, t))

    return np.array(clean, dtype=np.int32)


def stabilize(
    y: np.ndarray,
    sr: int,
    beat_times: np.ndarray,
    target_bpm: float,
    strength: float = 1.0,
    crispness: int | None = None,
) -> np.ndarray:
    beat_interval_samples = sr * 60.0 / target_bpm
    beat_samples = np.round(beat_times * sr).astype(np.int64)

    first = int(beat_samples[0])
    ideal_samples = np.array(
        [first + round(i * beat_interval_samples) for i in range(len(beat_samples))],
        dtype=np.int64,
    )
    target_samples = np.round(
        (1.0 - strength) * beat_samples + strength * ideal_samples
    ).astype(np.int64)

    n_total = len(y) if y.ndim == 1 else y.shape[0]
    timemap = build_timemap(beat_samples, target_samples, n_total)

    print(f"  Warping {len(timemap)-2} beat anchors …")
    # pyrubberband exposes rubberband's --crispness flag via rbargs. 0 = smooth,
    # 6 = sharp transients. None = library default.
    rbargs: dict | None = None
    if crispness is not None:
        rbargs = {"--crispness": str(int(crispness))}
    return pyrb.timemap_stretch(y, sr, timemap, rbargs=rbargs) if rbargs else pyrb.timemap_stretch(y, sr, timemap)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Stabilise audio to a consistent BPM grid.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 beat_stabilizer.py -i live_drums.wav -o stable_drums.wav
  python3 beat_stabilizer.py -i guitar.mp3 -o guitar_stable.wav --bpm 98 --strength 0.8
  python3 beat_stabilizer.py -i song.m4a --detect-only
        """,
    )
    p.add_argument("-i", "--input",       required=True)
    p.add_argument("-o", "--output",      default=None,  help="Output WAV file")
    p.add_argument("--bpm",               type=float,    default=None, help="Target BPM (auto-detected if omitted)")
    p.add_argument("--strength",          type=float,    default=1.0,  metavar="0-1", help="Quantisation strength (default 1.0)")
    p.add_argument("--sample-rate",       type=int,      default=44100)
    p.add_argument("--detect-only",       action="store_true",
                   help="Print detected BPM and exit without writing")
    # Intro trim is ON by default: the output starts one bar before the first
    # detected beat so the file drops straight into a DAW without manual offsetting.
    # Use --no-trim-intro to get the raw stabilised audio with no padding changes.
    p.add_argument("--no-trim-intro",     action="store_false", dest="trim_intro",
                   help="Disable the default intro trim (output starts at sample 0)")
    p.add_argument("--trim-intro",        action="store_true",  dest="trim_intro",
                   help="Trim output to start one bar before the first detected beat "
                        "(on by default; silence-padded when the beat is near the file start)")
    p.set_defaults(trim_intro=True)
    p.add_argument("--beats-per-bar",     type=int,      default=None, dest="beats_per_bar",
                   help="Beats per bar for the intro trim length "
                        "(default: auto-detected from downbeats, falling back to 4)")
    p.add_argument("--allow-tempo-change", action="store_true", dest="allow_tempo_change",
                   help="Proceed even if a sustained tempo change is detected. "
                        "Default: stop and emit EARLY_STOP so the caller can warn the user. "
                        "Enable only if you accept that the warp will be musically wrong "
                        "across the tempo boundary.")
    p.add_argument("--intro-trim-bars",   type=int, default=1, dest="intro_trim_bars",
                   help="How many bars to include before the first detected beat when "
                        "--trim-intro is on (default: 1)")
    # Detector backend & library-level knobs.
    det = p.add_argument_group("Beat detector (library knobs)")
    det.add_argument("--detector-backend", default="auto",
                     choices=("auto", "madmom", "librosa"), dest="detector_backend",
                     help="Beat detector to use (default: auto — madmom then librosa)")
    det.add_argument("--madmom-bpb-options", default="3,4", dest="madmom_bpb_options",
                     help="Comma-separated candidate beats-per-bar for the madmom "
                          "downbeat tracker (default: 3,4)")
    det.add_argument("--madmom-fps",       type=int,   default=100, dest="madmom_fps",
                     help="madmom RNN/DBN frame rate in Hz (default: 100)")
    det.add_argument("--madmom-timeout-s", type=int,   default=240, dest="madmom_timeout_s",
                     help="Seconds before the madmom subprocess is aborted (default: 240)")
    det.add_argument("--librosa-start-bpm", type=float, default=120.0, dest="librosa_start_bpm",
                     help="Initial tempo guess for librosa.beat.beat_track (default: 120)")
    det.add_argument("--librosa-tightness", type=float, default=100.0, dest="librosa_tightness",
                     help="librosa beat-tracker onset-strength weighting (default: 100)")
    det.add_argument("--librosa-hop-length", type=int, default=512, dest="librosa_hop_length",
                     help="librosa STFT hop length in samples (default: 512)")
    # Tempo-change guard knobs.
    tc = p.add_argument_group("Tempo-change guard")
    tc.add_argument("--tempo-change-window-bars",  type=int, default=8, dest="tempo_change_window_bars",
                    help="Rolling-median window for the tempo-change scanner (default: 8 bars)")
    tc.add_argument("--tempo-change-persist-bars", type=int, default=4, dest="tempo_change_persist_bars",
                    help="Bars the new tempo must persist before the guard fires (default: 4)")
    tc.add_argument("--tempo-change-threshold-pct", type=float, default=0.06, dest="tempo_change_threshold_pct",
                    help="Percentage tempo step that counts as a change (default: 0.06 = 6%%)")
    tc.add_argument("--tempo-change-threshold-floor", type=float, default=6.0, dest="tempo_change_threshold_floor",
                    help="Minimum absolute BPM step that counts as a change (default: 6)")
    # Beat-octave normalisation (runs after detection, before warping).
    p.add_argument("--no-beat-octave-normalize", action="store_false",
                   dest="beat_octave_normalize",
                   help="Disable mid-song beat-tracker octave-flip correction. "
                        "By default we thin doubled beats and insert midpoints for "
                        "halved beats so the warper sees a single quarter-note grid. "
                        "Pass this if you suspect normalization is misfiring on a "
                        "genuinely variable-tempo song.")
    p.set_defaults(beat_octave_normalize=True)
    # Warp engine knobs.
    p.add_argument("--pyrb-crispness", type=int, default=None, dest="pyrb_crispness",
                   help="rubberband --crispness (0=smoothest, 6=sharpest transients). "
                        "Default: library default.")
    p.add_argument("--progress-json",     action="store_true", dest="progress_json",
                   help="Emit machine-readable PROGRESS JSON lines on stdout")
    return p.parse_args()


def _emit_early_stop(reason: str, **details: object) -> None:
    """Emit a structured EARLY_STOP line on stdout. Always emitted (not gated
    by --progress-json) so the caller / web layer can parse it even when
    PROGRESS lines are off."""
    payload = {"reason": reason, **details}
    sys.stdout.write(f"EARLY_STOP {json.dumps(payload)}\n")
    sys.stdout.flush()


def main() -> None:
    global _PROGRESS_JSON
    args = parse_args()
    _PROGRESS_JSON = args.progress_json

    if not os.path.isfile(args.input):
        sys.exit(f"File not found: {args.input}")
    if not args.detect_only and args.output is None:
        sys.exit("Specify -o / --output (or use --detect-only).")
    if not 0.0 <= args.strength <= 1.0:
        sys.exit("--strength must be between 0.0 and 1.0")

    _emit("load", 0.02, "loading audio")
    print(f"\n[1/4] Loading  {args.input} …")
    y, sr = load_audio(args.input, args.sample_rate)
    duration = (len(y) if y.ndim == 1 else y.shape[0]) / sr
    channels = 1 if y.ndim == 1 else y.shape[1]
    print(f"  {duration:.2f}s  |  {sr} Hz  |  {channels}ch")

    _emit("detect_beats", 0.15, "detecting beats")
    print("\n[2/4] Detecting beats …")
    bpb_options = [int(x) for x in args.madmom_bpb_options.split(",") if x.strip()]
    beat_times, detected_bpm, downbeat_indices, detected_meter = detect_beats(
        y, sr,
        bpb_options=bpb_options,
        backend=args.detector_backend,
        madmom_fps=args.madmom_fps,
        madmom_timeout_s=args.madmom_timeout_s,
        librosa_start_bpm=args.librosa_start_bpm,
        librosa_tightness=args.librosa_tightness,
        librosa_hop_length=args.librosa_hop_length,
    )
    _emit("detect_beats", 0.55, f"{len(beat_times)} beats")

    # Beat-octave normalisation — collapses mid-song quarter↔eighth-note
    # tracker flips into a single grid so (a) the tempo-change guard sees a
    # stable BPM and (b) the warper doesn't stretch the doubled-density
    # section to 2× its true length.  Disable with --no-beat-octave-normalize.
    if args.beat_octave_normalize and len(beat_times) >= 6:
        beat_times_before = beat_times
        beat_times, norm_info = normalize_beat_octaves(beat_times)
        if norm_info["dropped"] or norm_info["inserted"]:
            print(f"  [octave-normalize] dropped {norm_info['dropped']} 8th-note beats, "
                  f"inserted {norm_info['inserted']} half-note midpoints "
                  f"({len(beat_times_before)} → {len(beat_times)} beats)")
            # If we modified the sequence, downbeat indices from the detector
            # no longer match — drop them so detect_tempo_change falls back to
            # the 4-beat-bar assumption rather than dereferencing stale indices.
            if downbeat_indices is not None:
                downbeat_indices = None

    if args.detect_only:
        print(f"\nDetected BPM   : {detected_bpm:.3f}")
        print(f"Beat count     : {len(beat_times)}")
        if detected_meter:
            print(f"Detected meter : {detected_meter}/4")
        if downbeat_indices is not None:
            print(f"Downbeats      : {len(downbeat_indices)}")
        return

    # Tempo-change guard.
    # Catches songs with arrangement-level tempo shifts (ballad → chorus
    # bump, etc.) that the single-BPM warp would corrupt. AI-floating-BPM
    # jitter and human rubato are explicitly *not* flagged — that's what
    # the warp is for. See detect_tempo_change() docstring.
    tempo_event = detect_tempo_change(
        beat_times, downbeat_indices,
        window_bars=args.tempo_change_window_bars,
        persist_bars=args.tempo_change_persist_bars,
        threshold_pct=args.tempo_change_threshold_pct,
        threshold_floor_bpm=args.tempo_change_threshold_floor,
    )
    if tempo_event is not None:
        if args.allow_tempo_change:
            print(f"\n  ⚠  Tempo change detected at bar {tempo_event['at_bar']} "
                  f"({tempo_event['from_bpm']} → {tempo_event['to_bpm']} BPM); "
                  f"continuing anyway because --allow-tempo-change was set.")
        else:
            print(f"\n  ✗  Tempo change detected at bar {tempo_event['at_bar']} "
                  f"({tempo_event['from_bpm']} → {tempo_event['to_bpm']} BPM, "
                  f"≈{tempo_event['at_time_s']}s).")
            print(f"     Beat stabilization assumes a single tempo and would warp this incorrectly.")
            print(f"     Split the song at the section boundary, or pass --allow-tempo-change.")
            _emit_early_stop("tempo_change", **tempo_event)
            sys.exit(2)

    # If the user didn't pass --beats-per-bar, use the detected meter (when
    # available) as the default for the intro trim length. Fall back to 4.
    if args.beats_per_bar is None:
        args.beats_per_bar = detected_meter if detected_meter else 4

    if args.bpm is not None:
        target_bpm = args.bpm
        print(f"\n[3/4] Using manual BPM: {target_bpm:.2f}")
        # ── Duration-fit sanity check ──────────────────────────────────────
        # Expected beats at target BPM vs actually detected.  A ratio near 2
        # means the tracker locked onto 8th notes in a half-time groove —
        # thin to every other beat so we don't stretch the file to 2× length.
        expected_beats = target_bpm / 60.0 * duration
        ratio = len(beat_times) / expected_beats
        if 1.7 < ratio < 2.3:
            # Half-time groove: the tracker locked onto 8th notes.
            # Keep ALL detected beats as warp anchors (2× correction density)
            # but map them to the 8th-note grid at target_bpm so the output
            # audio plays at the correct quarter-note tempo.
            warp_bpm = target_bpm * 2          # 8th-note grid spacing
            print(f"  ⚠  Beat count ({len(beat_times)}) ≈ 2× expected "
                  f"({expected_beats:.0f}) at {target_bpm} BPM  →  half-time groove.")
            print(f"     Using all {len(beat_times)} sub-beat anchors at "
                  f"{warp_bpm:.0f} BPM grid (2× correction density).")
        elif 0.43 < ratio < 0.57:
            warp_bpm = target_bpm
            print(f"  ⚠  Beat count ({len(beat_times)}) ≈ ½ expected "
                  f"({expected_beats:.0f}) at {target_bpm} BPM  →  double-time "
                  f"detected; consider a higher --bpm value.")
        else:
            warp_bpm = target_bpm
    else:
        if detected_bpm <= 0 or len(beat_times) < 2:
            sys.exit("Could not detect a valid BPM. Try --bpm <value>.")
        target_bpm = round(detected_bpm)
        warp_bpm   = target_bpm
        print(f"\n[3/4] Auto BPM: {detected_bpm:.2f} → rounded to {target_bpm}")

    _emit("warp", 0.60, "warping")
    print(f"\n[4/4] Stabilising (strength={args.strength}) …")
    y_out = stabilize(
        y, sr, beat_times, warp_bpm,
        strength=args.strength,
        crispness=args.pyrb_crispness,
    )
    _emit("warp", 0.90, "warped")

    # ── Intro trim ────────────────────────────────────────────────────────────
    # After warping, the first beat is still at beat_times[0] seconds (stabilize
    # keeps the first anchor in place).  We want the output to begin exactly one
    # bar before that beat, using the final target_bpm (post any half-time
    # thinning) so the bar length is musically consistent.
    if args.trim_intro:
        bar_duration   = args.beats_per_bar * 60.0 / target_bpm
        intro_duration = max(0, int(args.intro_trim_bars)) * bar_duration
        first_beat_t   = float(beat_times[0])
        trim_start_t   = first_beat_t - intro_duration

        print(f"\n  Intro trim:")
        print(f"    First beat     : {first_beat_t:.3f}s")
        print(f"    Bar duration   : {bar_duration:.3f}s  "
              f"({args.beats_per_bar} beats @ {target_bpm} BPM)")
        print(f"    Intro duration : {intro_duration:.3f}s  ({args.intro_trim_bars} bars)")
        print(f"    Target start   : {trim_start_t:.3f}s", end="")

        if trim_start_t >= 0:
            # There is enough audio before the first beat — just trim.
            trim_sample = round(trim_start_t * sr)
            y_out = y_out[trim_sample:] if y_out.ndim == 1 else y_out[trim_sample:, :]
            print(f"  →  trimmed {trim_start_t:.3f}s from the front")
        else:
            # Not enough audio — prepend silence.
            silence_dur    = abs(trim_start_t)
            silence_frames = round(silence_dur * sr)
            if y_out.ndim == 1:
                silence = np.zeros(silence_frames, dtype=y_out.dtype)
            else:
                silence = np.zeros((silence_frames, y_out.shape[1]), dtype=y_out.dtype)
            y_out = np.concatenate([silence, y_out], axis=0)
            print(f"  →  prepended {silence_dur:.3f}s of silence")

    print(f"\nSaving …")
    saved_path = save_audio(args.output, y_out, sr)
    write_bpm_sidecar(saved_path, target_bpm)
    _emit("save", 1.0, "done")
    print("\nDone.")


if __name__ == "__main__":
    main()

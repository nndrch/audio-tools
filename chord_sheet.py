#!/usr/bin/env python3
"""
chord_sheet.py - Detect chords in an audio file and output a chord sheet.

Uses crema (602-class chord vocabulary) for detection and librosa for
beat-synchronous alignment. Each chord is reported with a confidence score;
low-confidence detections are flagged with a warning.

Usage:
    python3.13 chord_sheet.py -i song.m4a
    python3.13 chord_sheet.py -i song.wav --bpm 120 --threshold 0.4
    python3.13 chord_sheet.py -i song.mp3 --format txt
"""

import argparse
import os
import sys
import tempfile

import numpy as np


CONFIDENCE_WARN = 0.45   # flag chords below this
LOW_CONFIDENCE_MSG = (
    "⚠  Low overall confidence — results may be unreliable. "
    "Consider checking manually."
)


# ---------------------------------------------------------------------------
# Audio loading (reuse pydub/soundfile pattern from beat_stabilizer)
# ---------------------------------------------------------------------------

def load_audio_mono(path: str, sr: int = 44100) -> tuple[np.ndarray, int]:
    """Load audio as mono float32, converting formats via pydub if needed."""
    import soundfile as sf
    import librosa

    ext = os.path.splitext(path)[1].lower()
    if ext in {".mp3", ".m4a", ".aiff", ".aif"}:
        try:
            from pydub import AudioSegment
        except ImportError:
            sys.exit("pydub required for mp3/m4a/aiff: pip install pydub")
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

    # Mix to mono
    if y.ndim == 2:
        y = y.mean(axis=1)

    if sr_orig != sr:
        # Use numpy linear interpolation to avoid scipy.fft / resampy deadlocks
        # on macOS (scipy 1.15+ hangs on import of scipy.fft in subprocesses).
        # Linear interpolation is sufficient for beat/key/timesig detection.
        n_out = int(round(len(y) * sr / sr_orig))
        y = np.interp(
            np.linspace(0, len(y) - 1, n_out),
            np.arange(len(y)),
            y,
        ).astype(np.float32)

    return y.astype(np.float32), sr


# ---------------------------------------------------------------------------
# Chord detection via crema
# ---------------------------------------------------------------------------

def detect_chords_crema(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """
    Run crema chord estimator.

    Returns
    -------
    times      : (n_frames,) frame centre times in seconds
    confidence : (n_frames,) max probability per frame [0–1]
    labels     : (n_frames,) chord label strings e.g. 'A:min7', 'C:maj', 'N'
    """
    import crema

    model = crema.models.chord.ChordModel()
    output = model.outputs(y=y, sr=sr)

    # crema's actual output key is 'chord_tag' (170 classes: 14 roots × 12 qualities + N)
    chord_probs = output["chord_tag"]          # (n_frames, 170)
    chord_idx   = np.argmax(chord_probs, axis=1)
    confidence  = chord_probs[np.arange(len(chord_idx)), chord_idx]

    # Decode using the encoder's fitted classes
    pump  = model.pump
    task  = pump["chord_tag"]
    vocab = list(task.encoder.classes_)        # e.g. ['A#:7', 'A#:aug', ..., 'N']
    labels = [vocab[i] for i in chord_idx]

    # Frame hop from the task (same sr used by crema internally)
    hop   = task.hop_length
    times = librosa_frames_to_time(hop, sr, len(chord_idx))

    return times, confidence, labels


def librosa_frames_to_time(hop: int, sr: int, n_frames: int) -> np.ndarray:
    import librosa
    return librosa.frames_to_time(np.arange(n_frames), sr=sr, hop_length=hop)


# ---------------------------------------------------------------------------
# Beat-synchronous alignment
# ---------------------------------------------------------------------------

def beat_sync_chords(
    times: np.ndarray,
    confidence: np.ndarray,
    labels: list[str],
    beat_times: np.ndarray,
    sr: int,
    hop: int,
) -> list[dict]:
    """
    Collapse frame-level chord detections into beat-length segments.

    For each beat window, picks the chord with the highest *total* probability
    mass and reports the mean confidence of that chord within the window.

    Returns a list of dicts:
        { beat: int, time: float, chord: str, confidence: float }
    """
    import librosa

    frame_times = times
    beat_results = []

    for i, beat_t in enumerate(beat_times):
        # Window: from this beat to the next (or +1 beat interval for the last)
        if i < len(beat_times) - 1:
            end_t = beat_times[i + 1]
        else:
            end_t = beat_t + np.median(np.diff(beat_times))

        # Frames inside this beat window
        mask = (frame_times >= beat_t) & (frame_times < end_t)
        if not mask.any():
            # Nearest single frame
            idx = np.argmin(np.abs(frame_times - beat_t))
            mask[idx] = True

        window_labels = [labels[j] for j in np.where(mask)[0]]
        window_conf   = confidence[mask]

        # Most common chord weighted by confidence
        chord_scores: dict[str, float] = {}
        for lbl, conf in zip(window_labels, window_conf):
            chord_scores[lbl] = chord_scores.get(lbl, 0.0) + conf

        best_chord = max(chord_scores, key=chord_scores.__getitem__)
        # Mean confidence of the winning chord's frames
        winning_mask = np.array([l == best_chord for l in window_labels])
        mean_conf = float(window_conf[winning_mask].mean())

        beat_results.append({
            "beat":       i + 1,
            "time":       float(beat_t),
            "chord":      best_chord,
            "confidence": round(mean_conf, 3),
        })

    return beat_results


# ---------------------------------------------------------------------------
# Detect beats (lifted from beat_stabilizer logic)
# ---------------------------------------------------------------------------

def detect_beats(
    y: np.ndarray,
    sr: int,
    manual_bpm: float | None = None,
    start_bpm: float = 120.0,
    tightness: float = 100.0,
    hop_length: int = 512,
) -> np.ndarray:
    import librosa

    tempo, beat_frames = librosa.beat.beat_track(
        y=y, sr=sr, units="frames",
        start_bpm=start_bpm, tightness=tightness, hop_length=hop_length,
    )
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop_length)

    if len(beat_times) < 2:
        beat_times = librosa.onset.onset_detect(y=y, sr=sr, units="time", hop_length=hop_length)

    return beat_times.astype(float)


def detect_time_signature(
    y: np.ndarray,
    sr: int,
    beat_times: np.ndarray,
    window_factor: float = 0.15,
) -> int:
    """
    Estimate beats-per-bar (2, 3, or 4) by scoring how well the onset strength
    envelope repeats at each candidate bar length via autocorrelation.

    Returns the most likely beats-per-bar integer.
    """
    import librosa

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    beat_frames = librosa.time_to_frames(beat_times, sr=sr)
    avg_beat_frames = float(np.median(np.diff(beat_frames)))

    # Normalised autocorrelation
    ac = np.correlate(onset_env, onset_env, mode="full")
    ac = ac[len(ac) // 2:]  # keep non-negative lags only
    ac /= ac[0] + 1e-8

    scores: dict[int, float] = {}
    for bpb in (2, 3, 4):
        lag = int(round(avg_beat_frames * bpb))
        if lag < len(ac):
            # Average the autocorrelation over a small window around the lag
            window = max(1, int(avg_beat_frames * window_factor))
            scores[bpb] = float(ac[max(0, lag - window): lag + window + 1].mean())

    best = max(scores, key=scores.get)

    # Tiebreak: if 4/4 and 2/4 are very close, prefer 4/4 (more common in pop)
    if best == 2 and scores.get(4, 0) > scores[2] * 0.85:
        best = 4

    # Compound-duple check: 3/4 vs 6/8.
    # In 6/8 the dotted-quarter beat (= 1.5 quarter notes) creates a strong
    # periodic accent.  The autocorrelation at that lag should therefore be
    # almost as large as the bar-period lag.  In simple 3/4 the 1.5-beat
    # position lands between quarter-note beats and is acoustically weak.
    # We encode 6/8 as the return value 6; chord_chart_render.py handles it.
    if best == 3:
        half_lag = int(round(avg_beat_frames * 1.5))
        if half_lag < len(ac):
            window = max(1, int(avg_beat_frames * window_factor))
            half_score = float(
                ac[max(0, half_lag - window): half_lag + window + 1].mean()
            )
            if half_score >= scores[3] * 0.80:
                best = 6  # compound duple → caller renders as 6/8

    return best


# ---------------------------------------------------------------------------
# Chord sheet formatting
# ---------------------------------------------------------------------------

def _conf_flag(conf: float) -> str:
    if conf < CONFIDENCE_WARN:
        return " ?"
    return ""


def format_txt(beats: list[dict], source: str, bpm: float) -> str:
    lines = [
        f"Chord Sheet — {os.path.basename(source)}",
        f"Detected BPM: {bpm:.1f}",
        f"Confidence threshold for '?': < {CONFIDENCE_WARN}",
        "",
        f"{'Beat':>5}  {'Time':>7}  {'Chord':<12}  {'Conf':>5}",
        "-" * 40,
    ]

    low_conf_count = 0
    prev_chord = None
    for b in beats:
        chord = b["chord"]
        conf  = b["confidence"]
        flag  = _conf_flag(conf)
        if flag:
            low_conf_count += 1
        # Only print when chord changes (cleaner sheet)
        marker = "│" if chord == prev_chord else "►"
        lines.append(
            f"{b['beat']:>5}  {b['time']:>6.2f}s  {chord:<12}  {conf:>5.0%}{flag}"
        )
        prev_chord = chord

    lines.append("")
    pct_low = 100 * low_conf_count / max(len(beats), 1)
    lines.append(f"Low-confidence beats: {low_conf_count}/{len(beats)} ({pct_low:.0f}%)")
    if pct_low > 30:
        lines.append(LOW_CONFIDENCE_MSG)

    return "\n".join(lines)


def format_compact(beats: list[dict]) -> str:
    """Condensed view: only show chord when it changes."""
    lines = []
    prev = None
    bar = 1
    bar_buf = []

    for b in beats:
        chord = b["chord"]
        flag  = _conf_flag(b["confidence"])
        label = f"{chord}{flag}"
        bar_buf.append((b["beat"], label, chord == prev))
        prev = chord

        if b["beat"] % 4 == 0:
            # Print one bar
            unique = []
            for (_, lbl, same) in bar_buf:
                if not same or not unique:
                    unique.append(lbl)
                else:
                    unique.append("—")
            lines.append(f"  Bar {bar:>3}:  " + "  ".join(f"{x:<10}" for x in unique))
            bar += 1
            bar_buf = []

    if bar_buf:
        unique = []
        prev2 = None
        for _, lbl, _ in bar_buf:
            chord_only = lbl.rstrip(" ?")
            if chord_only != prev2:
                unique.append(lbl)
            else:
                unique.append("—")
            prev2 = chord_only
        lines.append(f"  Bar {bar:>3}:  " + "  ".join(f"{x:<10}" for x in unique))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Detect chords and generate a beat-aligned chord sheet.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3.13 chord_sheet.py -i song.m4a
  python3.13 chord_sheet.py -i song.wav --bpm 120
  python3.13 chord_sheet.py -i song.mp3 --threshold 0.4 --format compact
        """,
    )
    p.add_argument("-i", "--input",  required=True, help="Input audio file")
    p.add_argument("-o", "--output", default=None,  help="Save chord sheet to .txt file")
    p.add_argument("--bpm",       type=float, default=None, help="Override BPM")
    p.add_argument("--threshold", type=float, default=CONFIDENCE_WARN,
                   help=f"Confidence below which a chord is flagged (default {CONFIDENCE_WARN})")
    p.add_argument("--format", choices=["full", "compact"], default="full",
                   help="Output format: full (every beat) or compact (bar-by-bar, default: full)")
    p.add_argument("--sample-rate", type=int, default=44100)
    # Library knobs (rarely changed)
    p.add_argument("--ts-window-factor", type=float, default=0.15, dest="ts_window_factor",
                   help="Time-signature autocorrelation window as a fraction of the median "
                        "beat-interval (default: 0.15)")
    p.add_argument("--librosa-start-bpm", type=float, default=120.0, dest="librosa_start_bpm",
                   help="Initial tempo guess for librosa.beat.beat_track (default: 120)")
    p.add_argument("--librosa-tightness", type=float, default=100.0, dest="librosa_tightness",
                   help="librosa beat-tracker onset-strength weighting (default: 100)")
    p.add_argument("--librosa-hop-length", type=int, default=512, dest="librosa_hop_length",
                   help="librosa STFT hop length in samples (default: 512)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    global CONFIDENCE_WARN
    CONFIDENCE_WARN = args.threshold

    if not os.path.isfile(args.input):
        sys.exit(f"File not found: {args.input}")

    # 1. Load
    print(f"\n[1/4] Loading {args.input} …")
    y, sr = load_audio_mono(args.input, args.sample_rate)
    print(f"  {len(y)/sr:.1f}s  |  {sr} Hz  |  mono")

    # 2. Detect chords
    print("\n[2/4] Detecting chords (crema) …")
    times, confidence, labels = detect_chords_crema(y, sr)
    print(f"  {len(times)} frames analysed")
    print(f"  Mean confidence: {confidence.mean():.1%}  |  Min: {confidence.min():.1%}  |  Max: {confidence.max():.1%}")

    # Infer hop from frame times
    hop = int(round((times[1] - times[0]) * sr)) if len(times) > 1 else 4096

    # 3. Detect beats
    print("\n[3/4] Detecting beats …")
    beat_times = detect_beats(
        y, sr, args.bpm,
        start_bpm=args.librosa_start_bpm,
        tightness=args.librosa_tightness,
        hop_length=args.librosa_hop_length,
    )
    interval   = np.median(np.diff(beat_times))
    bpm        = 60.0 / interval
    if args.bpm:
        bpm = args.bpm
    print(f"  {len(beat_times)} beats  |  {bpm:.1f} BPM")

    # 4. Align chords to beats
    print("\n[4/4] Aligning chords to beat grid …")
    beat_chords = beat_sync_chords(times, confidence, labels, beat_times, sr, hop)

    # 5. Output
    print()
    if args.format == "compact":
        sheet = format_compact(beat_chords)
    else:
        sheet = format_txt(beat_chords, args.input, bpm)

    print(sheet)

    if args.output:
        with open(args.output, "w") as f:
            f.write(sheet)
        print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()

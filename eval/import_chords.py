#!/usr/bin/env python3
"""
eval/import_chords.py — turn a musician's chord sheet into a scorer-ready .lab.

The annotation guide (eval/ANNOTATION-GUIDE.md) asks musicians to fill in a
simple two-column sheet: the time each chord STARTS and the chord name. That
format is DAW-agnostic on purpose — it doesn't lean on any program's export
feature (Logic has no clean marker export; Pro Tools' is timecode-specific), so
a musician can produce it in Logic, Pro Tools, a spreadsheet, anywhere.

This script is the front door to the dataset: it converts that human sheet into
the Harte `.lab` the eval harness scores against (eval/score.py / eval/run.py),
tab-separated `start  end  label`, where each chord runs until the next one
begins and the last chord runs to the end of the song (read from the audio).

Sheet format (CSV or TSV, .csv/.txt), one row per chord change:

    start,chord
    0:00,C:maj
    0:08.5,A:min
    0:17,F:maj
    0:25,G:7

- `start` accepts `M:SS`, `M:SS.mmm`, `H:MM:SS`, or plain seconds (`8.5`).
- `chord` is a Harte label (see the cheat sheet in ANNOTATION-GUIDE.md): a root
  (A-G, optional # or b) + ':' + quality (maj/min/7/maj7/min7/dim/aug/
  sus2/sus4/maj6/min6), or `N` for no chord. Labels are validated with mir_eval;
  anything it can't parse is reported (and still written, so you can fix it).
- A header row naming the columns is auto-detected and skipped; blank lines and
  lines starting with '#' are ignored.

Usage (run in venv_crema so mir_eval is importable):
    # one sheet -> eval/dataset/<name>.lab  (audio auto-found as a sibling for duration)
    ./venv_crema/bin/python3.11 eval/import_chords.py path/to/song-01.csv

    # explicit audio and/or output dir
    ./venv_crema/bin/python3.11 eval/import_chords.py song-01.csv --audio song-01.wav -o eval/dataset

    # batch: every sheet in a folder
    ./venv_crema/bin/python3.11 eval/import_chords.py incoming/ -o eval/dataset
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import sys

AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".flac", ".aiff", ".aif", ".ogg")
SHEET_EXTS = (".csv", ".tsv", ".txt")
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset")


def parse_time(text: str) -> float:
    """Parse a start time into seconds.

    Accepts plain seconds ('8.5'), 'M:SS[.mmm]', or 'H:MM:SS[.mmm]'. Raises
    ValueError on anything else so a bad cell is reported rather than silently
    mis-imported.
    """
    text = text.strip()
    if not text:
        raise ValueError("empty time")
    if ":" not in text:
        return float(text)
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(f"too many ':' in time {text!r}")
    secs = 0.0
    for p in parts:                       # most-significant first
        secs = secs * 60.0 + float(p)
    return secs


def _looks_like_header(row: list[str]) -> bool:
    """A first row whose time cell isn't a parseable time is a header."""
    if not row:
        return True
    try:
        parse_time(row[0])
        return False
    except ValueError:
        return True


def read_sheet(path: str) -> list[tuple[float, str]]:
    """Read a chord sheet -> [(start_seconds, label)] sorted by start.

    Tolerates comma- or tab-separated files, a header row, blank lines, and
    '#'-comment lines.
    """
    with open(path, newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        delim = "\t" if (sample.count("\t") > sample.count(",")) else ","
        rows: list[tuple[float, str]] = []
        first = True
        for raw in csv.reader(f, delimiter=delim):
            cells = [c.strip() for c in raw]
            if not cells or all(c == "" for c in cells):
                continue
            if cells[0].startswith("#"):
                continue
            if first:
                first = False
                if _looks_like_header(cells):
                    continue
            if len(cells) < 2:
                print(f"    [warn] {os.path.basename(path)}: skipping malformed row {cells!r}",
                      file=sys.stderr)
                continue
            try:
                t = parse_time(cells[0])
            except ValueError as e:
                print(f"    [warn] {os.path.basename(path)}: bad time {cells[0]!r} ({e}) — skipped",
                      file=sys.stderr)
                continue
            rows.append((t, cells[1]))
    rows.sort(key=lambda r: r[0])
    return rows


def validate_label(label: str) -> bool:
    """True if mir_eval can parse the Harte label (or it's the no-chord 'N')."""
    if label in ("N", "X"):
        return True
    try:
        import mir_eval
        mir_eval.chord.encode(label)
        return True
    except Exception:
        return False


def audio_duration(audio: str) -> float | None:
    """Total seconds of an audio file, without decoding the whole thing."""
    try:
        import soundfile as sf
        return float(sf.info(audio).duration)
    except Exception:
        pass
    try:
        import librosa
        return float(librosa.get_duration(path=audio))
    except Exception:
        return None


def find_sibling_audio(sheet: str, explicit: str | None) -> str | None:
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    stem = os.path.splitext(sheet)[0]
    for ext in AUDIO_EXTS:
        if os.path.isfile(stem + ext):
            return stem + ext
    return None


def sheet_to_lab(sheet: str, out_dir: str, explicit_audio: str | None) -> str | None:
    """Convert one chord sheet to a .lab in out_dir. Returns the path or None."""
    rows = read_sheet(sheet)
    if not rows:
        print(f"  [skip] {os.path.basename(sheet)}: no chord rows found", file=sys.stderr)
        return None

    name = os.path.splitext(os.path.basename(sheet))[0]
    audio = find_sibling_audio(sheet, explicit_audio)
    dur = audio_duration(audio) if audio else None

    # End of each chord = start of the next; the final chord ends at the song's
    # end. Without the audio we fall back to the median gap and warn — the tail
    # chord's length is then a guess.
    starts = [t for t, _ in rows]
    if dur is None:
        gaps = [b - a for a, b in zip(starts, starts[1:]) if b > a]
        med = (sorted(gaps)[len(gaps) // 2] if gaps else 2.0)
        dur = starts[-1] + med
        print(f"  [warn] {name}: no audio found for duration — last chord length guessed "
              f"({med:.2f}s). Pass --audio for an exact end.", file=sys.stderr)
    elif dur < starts[-1]:
        print(f"  [warn] {name}: last chord starts ({starts[-1]:.2f}s) after the audio ends "
              f"({dur:.2f}s) — check the times.", file=sys.stderr)

    ends = starts[1:] + [dur]

    # Build the segments, collecting any unrecognised labels. A single bad label
    # makes the whole song unscoreable (mir_eval raises), so we refuse to admit
    # it into the dataset: if anything is invalid we write a `.lab.rejected`
    # sidecar for inspection instead of the real .lab, and the caller exits
    # nonzero. Fix the flagged cell in the sheet and re-import.
    segments: list[tuple[float, float, str]] = []
    bad: list[str] = []
    if starts[0] > 0.05:
        # Cover any silence before the first marked chord so the reference spans
        # the whole song from 0.0 (the scorer compares over the full timeline).
        segments.append((0.0, starts[0], "N"))
    for (start, label), end in zip(rows, ends):
        if end <= start:
            print(f"  [warn] {name}: zero/negative span at {start:.2f}s "
                  f"({label!r}) — skipped", file=sys.stderr)
            continue
        if not validate_label(label):
            bad.append(f"{start:.2f}s {label!r}")
        segments.append((start, end, label))

    os.makedirs(out_dir, exist_ok=True)
    if bad:
        out_path = os.path.join(out_dir, name + ".lab.rejected")
        with open(out_path, "w") as f:
            for s, e, lbl in segments:
                f.write(f"{s:.4f}\t{e:.4f}\t{lbl}\n")
        print(f"  [REJECTED] {os.path.basename(sheet)}: {len(bad)} unrecognised chord(s) — "
              f"NOT added to the dataset.", file=sys.stderr)
        for b in bad:
            print(f"             • {b}  (see the cheat sheet in ANNOTATION-GUIDE.md)", file=sys.stderr)
        print(f"             wrote {out_path} for inspection; fix the sheet and re-import.",
              file=sys.stderr)
        return None

    out_path = os.path.join(out_dir, name + ".lab")
    with open(out_path, "w") as f:
        for s, e, lbl in segments:
            f.write(f"{s:.4f}\t{e:.4f}\t{lbl}\n")
    print(f"  [ok] {os.path.basename(sheet)} -> {out_path}  ({len(segments)} segments)")
    return out_path


def collect_sheets(inputs: list[str]) -> list[str]:
    """Expand files/dirs into a sheet list (dirs are scanned for sheet exts)."""
    sheets: list[str] = []
    for item in inputs:
        if os.path.isdir(item):
            for ext in SHEET_EXTS:
                sheets += sorted(glob.glob(os.path.join(item, f"*{ext}")))
        elif os.path.isfile(item):
            sheets.append(item)
        else:
            print(f"  [skip] not found: {item}", file=sys.stderr)
    # Don't import the blank template itself.
    return [s for s in sheets if os.path.basename(s) != "annotation-template.csv"]


def main() -> None:
    p = argparse.ArgumentParser(
        description="Convert musician chord sheets into scorer-ready .lab files.")
    p.add_argument("inputs", nargs="+", help="Chord sheet file(s) or a folder of them")
    p.add_argument("-o", "--out", default=DEFAULT_OUT,
                   help=f"Output dir for the .lab files (default: {DEFAULT_OUT})")
    p.add_argument("--audio", default=None,
                   help="Audio file for the song's duration (only valid with a single sheet; "
                        "otherwise each sheet's sibling audio is used)")
    args = p.parse_args()

    sheets = collect_sheets(args.inputs)
    if not sheets:
        sys.exit("No chord sheets found. Point me at a .csv/.tsv file or a folder of them.")
    if args.audio and len(sheets) > 1:
        sys.exit("--audio only applies to a single sheet; for a batch, name each audio like its sheet.")

    print(f"  importing {len(sheets)} sheet(s) -> {args.out}")
    ok = 0
    for sheet in sheets:
        if sheet_to_lab(sheet, args.out, args.audio) is not None:
            ok += 1
    rejected = len(sheets) - ok
    print(f"\n  done: {ok}/{len(sheets)} sheet(s) written to {args.out}"
          + (f"  ({rejected} rejected — fix and re-import)" if rejected else ""))
    if rejected:
        sys.exit(1)


if __name__ == "__main__":
    main()

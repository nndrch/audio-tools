#!/usr/bin/env python3
"""
eval/run.py — run the chord step over a labeled dataset and score it.

The Phase-0 A/B harness from docs/chord-detection-implementation-plan.md. For
each song it runs `chord_chart_render.py` with a flag profile, captures the
detected chords via `--lab-out`, scores them against the ground-truth `.lab`
with eval/score.py, and prints a per-song + aggregate table. Give it two or
more profiles and it prints the delta — this is how every accuracy change in
Phases 1-3 gets quantified instead of eyeballed.

Dataset layout (flat):
    eval/dataset/<name>.wav        (or .mp3/.m4a/.flac/.aiff/.ogg)
    eval/dataset/<name>.lab        ground-truth Harte annotation
Songs without a sibling .lab are skipped.

Usage (run with the crema venv so chord_chart_render + mir_eval are importable):
    ./venv_crema/bin/python3.11 eval/run.py --profile default
    ./venv_crema/bin/python3.11 eval/run.py --compare default viterbi
    ./venv_crema/bin/python3.11 eval/run.py --profile accuracy --prepare-aux
    ./venv_crema/bin/python3.11 eval/run.py --flags "--key-snap --viterbi-smoothing"

`--prepare-aux` pre-generates the bass stem (venv_demucs) and section JSON
(venv_allin1) a profile needs; without it, stem/section-dependent flags are
dropped with a warning so the run still completes.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for score.py

from score import score_pair, REPORTED_METRICS  # noqa: E402

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".aiff", ".aif", ".ogg"}

# Built-in flag profiles. Names map to chord_chart_render.py flag lists.
# The first group needs no external artifacts (runnable immediately); the
# "accuracy" profile needs the bass stem + sections (see --prepare-aux).
PROFILES: dict[str, list[str]] = {
    "default":    [],                                    # current production defaults
    "no-madmom":  ["--no-madmom-fallback"],
    "hpss-off":   ["--hpss-mode", "off"],
    "viterbi":    ["--viterbi-smoothing"],
    "keysnap":    ["--key-snap"],
    "accuracy":   ["--key-snap", "--viterbi-smoothing", "--section-consistency",
                   "--bass-anchor", "--slash-chords"],
}

# Flags that require auxiliary artifacts before the chord step can use them.
_NEEDS_BASS     = {"--bass-anchor", "--slash-chords"}
_NEEDS_SECTIONS = {"--section-consistency"}

# ── Self-improving gate (M7) ────────────────────────────────────────────────
# The champion record is the current best aggregate we gate new candidates
# against. It lives OUTSIDE results/ (which is git-ignored) so it can be tracked
# in git as the shared regression baseline. `--promote` writes it; `--gate`
# reads it. It starts life as the `default` baseline and is replaced by winners.
CHAMPION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "champion.json")

# The ship rule (eval/README.md §"Workflow for an accuracy change", step 4):
# ship only if majmin/sevenths improve WITHOUT regressing root/seg.
GATE_WIN_METRICS   = ("majmin", "sevenths")  # improvement here = a win
GATE_GUARD_METRICS = ("root", "seg")         # must not regress
# Exit codes so the gate is usable in scripts/CI: only a real regression fails.
GATE_EXIT = {"improve": 0, "neutral": 0, "no_data": 0, "no_champion": 0, "regress": 1}


def gate_decision(cand: dict | None, champ: dict | None, eps: float = 0.005) -> dict:
    """Decide improve / regress / neutral from two `weighted` metric dicts.

    Pure (no I/O) so it can be unit-tested with synthetic aggregates. `cand` and
    `champ` are the `weighted` sub-dicts of a run aggregate ({metric: recall}).
    `eps` is a dead-band that ignores measurement noise. Verdicts:
      improve      — a win on majmin/sevenths (> eps) and no root/seg regression
      regress      — root or seg dropped more than eps (guard fails; takes priority)
      neutral      — within tolerance everywhere
      no_data      — candidate scored no songs (e.g. empty dataset)
      no_champion  — nothing to compare against yet (run --promote first)
    """
    if not cand:
        return {"verdict": "no_data", "deltas": {},
                "reasons": ["candidate scored no songs — nothing to gate"]}
    if not champ:
        return {"verdict": "no_champion", "deltas": {},
                "reasons": ["no champion on record — run --promote to set the baseline"]}
    deltas = {m: round(cand[m] - champ[m], 4) for m in cand if m in champ}
    regressed = [m for m in GATE_GUARD_METRICS if deltas.get(m, 0.0) < -eps]
    improved  = [m for m in GATE_WIN_METRICS  if deltas.get(m, 0.0) >  eps]
    if regressed:
        return {"verdict": "regress", "deltas": deltas,
                "reasons": [f"{m} regressed {deltas[m]:+.4f} (beyond ±{eps})" for m in regressed]}
    if improved:
        return {"verdict": "improve", "deltas": deltas,
                "reasons": [f"{m} improved {deltas[m]:+.4f}" for m in improved]
                           + ["no root/seg regression"]}
    return {"verdict": "neutral", "deltas": deltas,
            "reasons": [f"no majmin/sevenths gain beyond ±{eps}; no root/seg regression"]}


def compute_dataset_sig(songs: list[tuple[str, str]]) -> str:
    """Short, stable signature of the song set so a stale champion (measured on a
    different set) can be flagged. Order-independent — hashes the sorted stems."""
    import hashlib
    stems = sorted(os.path.splitext(os.path.basename(a))[0] for a, _ in songs)
    return hashlib.sha1("\n".join(stems).encode()).hexdigest()[:12]


def load_champion(path: str = CHAMPION_PATH) -> dict | None:
    """Read the champion record, or None if there isn't one yet."""
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)


def save_champion(result: dict, n_songs: int, dataset_sig: str, stamp: str,
                  path: str = CHAMPION_PATH) -> None:
    """Persist a run's aggregate as the new champion baseline."""
    agg = result.get("aggregate", {})
    rec = {
        "profile":     result["profile"],
        "flags":       result["flags"],
        "n_songs":     n_songs,
        "dataset_sig": dataset_sig,
        "promoted_at": stamp,
        "weighted":    agg.get("weighted", {}),
        "macro":       agg.get("macro", {}),
    }
    with open(path, "w") as f:
        json.dump(rec, f, indent=2)


def find_songs(dataset_dir: str) -> list[tuple[str, str]]:
    """Return [(audio_path, ref_lab_path)] for every audio file with a sibling .lab."""
    songs = []
    for name in sorted(os.listdir(dataset_dir)):
        stem, ext = os.path.splitext(name)
        if ext.lower() not in AUDIO_EXTS:
            continue
        audio = os.path.join(dataset_dir, name)
        ref   = os.path.join(dataset_dir, stem + ".lab")
        if os.path.isfile(ref):
            songs.append((audio, ref))
        else:
            print(f"  [skip] {name}: no sibling {stem}.lab", file=sys.stderr)
    return songs


def _venv_python(venv: str) -> str | None:
    p = os.path.join(REPO_ROOT, venv, "bin", "python3.11")
    return p if os.path.isfile(p) else None


def prepare_aux(audio: str, stem: str, aux_dir: str, need_bass: bool, need_sections: bool) -> dict:
    """Generate (and cache) the bass stem and/or sections JSON a profile needs.

    Returns {"bass_wav": path|None, "sections_json": path|None}. Missing venvs
    are reported and the corresponding artifact is left None so the caller can
    drop the dependent flags.
    """
    os.makedirs(aux_dir, exist_ok=True)
    out = {"bass_wav": None, "sections_json": None}

    if need_bass:
        bass_wav = os.path.join(aux_dir, "bass.wav")
        if os.path.isfile(bass_wav):
            out["bass_wav"] = bass_wav
        else:
            demucs_py = _venv_python("venv_demucs")
            if not demucs_py:
                print("  [aux] venv_demucs missing — bass-dependent flags will be dropped", file=sys.stderr)
            else:
                print(f"  [aux] splitting bass stem for {stem} …", flush=True)
                r = subprocess.run(
                    [demucs_py, os.path.join(REPO_ROOT, "stem_splitter.py"),
                     "-i", audio, "-o", aux_dir, "--stems", "bass"],
                    cwd=REPO_ROOT,
                )
                if r.returncode == 0 and os.path.isfile(bass_wav):
                    out["bass_wav"] = bass_wav
                else:
                    print(f"  [aux] bass split failed for {stem}", file=sys.stderr)

    if need_sections:
        sections_json = os.path.join(aux_dir, "sections.json")
        if os.path.isfile(sections_json):
            out["sections_json"] = sections_json
        else:
            allin1_py = _venv_python("venv_allin1")
            if not allin1_py:
                print("  [aux] venv_allin1 missing — section-dependent flags will be dropped", file=sys.stderr)
            else:
                print(f"  [aux] detecting sections for {stem} …", flush=True)
                r = subprocess.run(
                    [allin1_py, os.path.join(REPO_ROOT, "run_allin1.py"),
                     "-i", audio, "-o", sections_json],
                    cwd=REPO_ROOT,
                )
                if r.returncode == 0 and os.path.isfile(sections_json):
                    out["sections_json"] = sections_json
                else:
                    print(f"  [aux] section detection failed for {stem}", file=sys.stderr)

    return out


def resolve_flags(flags: list[str], aux: dict | None, prepare: bool) -> tuple[list[str], list[str]]:
    """Inject --bass-wav / --sections-json when available; drop dependent flags
    that can't be satisfied. Returns (effective_flags, dropped_flags)."""
    flags = list(flags)
    dropped: list[str] = []
    has_bass     = bool(aux and aux.get("bass_wav"))
    has_sections = bool(aux and aux.get("sections_json"))

    if any(f in _NEEDS_BASS for f in flags):
        if has_bass:
            flags += ["--bass-wav", aux["bass_wav"]]
        else:
            dropped += [f for f in flags if f in _NEEDS_BASS]
            flags = [f for f in flags if f not in _NEEDS_BASS]
    if any(f in _NEEDS_SECTIONS for f in flags):
        if has_sections:
            flags += ["--sections-json", aux["sections_json"]]
        else:
            dropped += [f for f in flags if f in _NEEDS_SECTIONS]
            flags = [f for f in flags if f not in _NEEDS_SECTIONS]
    return flags, dropped


def run_profile_on_song(audio: str, ref_lab: str, flags: list[str], work_dir: str) -> dict | None:
    """Run the chord step on one song and score it. Returns metrics or None on failure.

    The chord step may exit non-zero if LilyPond/PDF rendering fails, but the
    .lab is written *before* rendering — so we score whenever the .lab exists,
    regardless of exit code.
    """
    os.makedirs(work_dir, exist_ok=True)
    stem    = os.path.splitext(os.path.basename(audio))[0]
    est_lab = os.path.join(work_dir, stem + ".lab")
    out_base = os.path.join(work_dir, stem)  # chord step writes <base>.pdf/.json/etc here

    cmd = [sys.executable, os.path.join(REPO_ROOT, "chord_chart_render.py"),
           "-i", audio, "-o", out_base, "--lab-out", est_lab, *flags]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)

    if not os.path.isfile(est_lab):
        tail = (proc.stderr or proc.stdout or "")[-500:]
        print(f"  [fail] {stem}: no .lab produced (exit {proc.returncode})\n{tail}", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(f"  [warn] {stem}: chord step exited {proc.returncode} (likely render); scoring .lab anyway",
              file=sys.stderr)

    # A malformed label in the reference (e.g. a musician typo mir_eval rejects)
    # must not crash the whole run — report it and skip just this song.
    try:
        return score_pair(ref_lab, est_lab)
    except Exception as e:
        print(f"  [fail] {stem}: scoring error ({e}); skipping. "
              f"Check {ref_lab} for an invalid chord label.", file=sys.stderr)
        return None


def aggregate(rows: list[dict]) -> dict:
    """Duration-weighted mean (MIREX-style) + macro mean across songs."""
    agg = {"weighted": {}, "macro": {}, "n_songs": len(rows)}
    total_dur = sum(r["metrics"]["duration"] for r in rows) or 1.0
    for m in REPORTED_METRICS:
        vals = [r["metrics"][m] for r in rows if m in r["metrics"]]
        if not vals:
            continue
        agg["weighted"][m] = round(
            sum(r["metrics"][m] * r["metrics"]["duration"] for r in rows) / total_dur, 4)
        agg["macro"][m] = round(sum(vals) / len(vals), 4)
    return agg


def run_one_profile(name: str, flags: list[str], songs, work_root: str, prepare_aux_flag: bool) -> dict:
    print(f"\n{'='*60}\n  PROFILE: {name}   flags: {' '.join(flags) or '(none)'}\n{'='*60}")
    need_bass     = any(f in _NEEDS_BASS for f in flags)
    need_sections = any(f in _NEEDS_SECTIONS for f in flags)
    rows = []
    for audio, ref in songs:
        stem = os.path.splitext(os.path.basename(audio))[0]
        aux = None
        if prepare_aux_flag and (need_bass or need_sections):
            aux = prepare_aux(audio, stem, os.path.join(work_root, "aux", stem),
                              need_bass, need_sections)
        eff_flags, dropped = resolve_flags(flags, aux, prepare_aux_flag)
        if dropped:
            print(f"  [{stem}] dropped (no aux): {' '.join(sorted(set(dropped)))}", file=sys.stderr)
        print(f"  → {stem} …", flush=True)
        metrics = run_profile_on_song(audio, ref, eff_flags,
                                      os.path.join(work_root, name))
        if metrics is not None:
            rows.append({"song": stem, "metrics": metrics})
    return {"profile": name, "flags": flags, "songs": rows, "aggregate": aggregate(rows) if rows else {}}


def _profile_flags(name: str) -> list[str]:
    """Resolve a built-in profile name to its flag list, or exit with the list."""
    if name not in PROFILES:
        sys.exit(f"Unknown profile '{name}'. Known: {', '.join(PROFILES)}")
    return PROFILES[name]


def persist_results(out_dir: str, dataset: str, n_songs: int, results: list[dict]) -> str:
    """Write a timestamped results JSON (the regression record) and return its path."""
    os.makedirs(out_dir, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    label = "_".join(r["profile"] for r in results) or "run"
    out_json = os.path.join(out_dir, f"{stamp}_{label}.json")
    with open(out_json, "w") as f:
        json.dump({"dataset": dataset, "n_songs": n_songs, "results": results}, f, indent=2)
    return out_json


def print_gate(profile: str, decision: dict, champ: dict | None,
               dataset_sig: str, n_songs: int) -> None:
    """Print the gate verdict, the per-metric deltas, and the reasons."""
    icon = {"improve": "✅ IMPROVE", "regress": "❌ REGRESS", "neutral": "➖ NEUTRAL",
            "no_data": "…  NO DATA", "no_champion": "…  NO CHAMPION"}[decision["verdict"]]
    print(f"\n{'='*60}\n  GATE: '{profile}' vs champion  →  {icon}\n{'='*60}")
    if champ and champ.get("dataset_sig") and champ["dataset_sig"] != dataset_sig:
        print(f"  ⚠  champion was measured on a different song set "
              f"({champ.get('n_songs', '?')} songs); current set is {n_songs}. "
              f"Re-promote the baseline for an apples-to-apples comparison.")
    if decision["deltas"]:
        print("  Δ vs champion (duration-weighted recall):")
        for m in REPORTED_METRICS:
            if m in decision["deltas"]:
                print(f"    {m:10}  {decision['deltas'][m]:+.4f}")
    for r in decision["reasons"]:
        print(f"  • {r}")
    print()


def print_table(results: list[dict]) -> None:
    metrics = [m for m in REPORTED_METRICS if m != "seg"] + ["seg"]
    # Per-profile aggregate (duration-weighted) side by side.
    print(f"\n{'='*60}\n  AGGREGATE (duration-weighted recall)\n{'='*60}")
    header = f"  {'metric':10}" + "".join(f"{r['profile'][:14]:>16}" for r in results)
    print(header)
    for m in metrics:
        line = f"  {m:10}"
        for r in results:
            v = r["aggregate"].get("weighted", {}).get(m)
            line += f"{(f'{v:.3f}' if v is not None else '—'):>16}"
        print(line)
    # Delta vs first profile when comparing.
    if len(results) >= 2:
        base = results[0]
        print(f"\n  Δ vs '{base['profile']}':")
        for r in results[1:]:
            line = f"  {r['profile'][:10]:10}"
            for m in metrics:
                b = base["aggregate"].get("weighted", {}).get(m)
                v = r["aggregate"].get("weighted", {}).get(m)
                if b is None or v is None:
                    line += f"{'—':>16}"
                else:
                    d = v - b
                    line += f"{(f'{d:+.3f}'):>16}"
            print(line)
    n = results[0]["aggregate"].get("n_songs", 0) if results else 0
    print(f"\n  scored {n} song(s)\n")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run + score the chord step over a labeled dataset.")
    p.add_argument("--dataset", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset"),
                   help="Dataset dir of <name>.<audio> + <name>.lab pairs")
    p.add_argument("--profile", action="append", default=[],
                   help=f"Built-in profile (repeatable). Known: {', '.join(PROFILES)}")
    p.add_argument("--compare", nargs="+", metavar="PROFILE",
                   help="Shortcut for multiple --profile (prints deltas vs the first)")
    p.add_argument("--flags", default=None,
                   help="Ad-hoc flag string → an extra profile named 'custom'")
    p.add_argument("--gate", metavar="PROFILE", default=None,
                   help="Run PROFILE and compare it to the champion (eval/champion.json). "
                        "Prints an improve/regress/neutral verdict; exits non-zero only on "
                        "a regression. Works at 0 songs (verdict: no data).")
    p.add_argument("--promote", metavar="PROFILE", default=None,
                   help="Run PROFILE and save its aggregate as the new champion baseline.")
    p.add_argument("--gate-eps", type=float, default=0.005, dest="gate_eps",
                   help="Dead-band for the gate: deltas within ±eps count as no change "
                        "(default: 0.005).")
    p.add_argument("--prepare-aux", action="store_true",
                   help="Pre-generate bass stem (venv_demucs) + sections (venv_allin1) when a profile needs them")
    p.add_argument("--limit", type=int, default=0, help="Only score the first N songs (0 = all)")
    p.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "results"),
                   help="Directory for the results JSON + per-profile work dirs")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if not os.path.isdir(args.dataset):
        sys.exit(f"Dataset dir not found: {args.dataset}\nSee eval/README.md for the expected layout.")

    songs = find_songs(args.dataset)
    if args.limit:
        songs = songs[: args.limit]
    dataset_sig = compute_dataset_sig(songs)
    work_root = os.path.join(args.out, "work")

    # ── Mode: promote a profile's result to the champion baseline ─────────
    if args.promote:
        flags = _profile_flags(args.promote)
        if not songs:
            sys.exit("✗  nothing to promote: the dataset has 0 songs. Add labelled "
                     "songs (see eval/README.md) before setting a baseline.")
        res = run_one_profile(args.promote, flags, songs, work_root, args.prepare_aux)
        if not res["aggregate"]:
            sys.exit("✗  nothing to promote: no songs scored successfully.")
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        save_champion(res, len(res["songs"]), dataset_sig, stamp)
        print_table([res])
        print(f"  ✓ promoted '{args.promote}' as champion → {CHAMPION_PATH}")
        print(f"    commit eval/champion.json to share this baseline.\n")
        return

    # ── Mode: gate a profile against the champion ─────────────────────────
    if args.gate:
        flags = _profile_flags(args.gate)
        cand = run_one_profile(args.gate, flags, songs, work_root, args.prepare_aux)
        champ = load_champion()
        decision = gate_decision(cand["aggregate"].get("weighted"),
                                 (champ or {}).get("weighted"), args.gate_eps)
        print_gate(args.gate, decision, champ, dataset_sig, n_songs=len(cand["songs"]))
        if songs:
            persist_results(args.out, args.dataset, len(songs), [cand])
        sys.exit(GATE_EXIT.get(decision["verdict"], 0))

    # ── Default mode: run + score one or more profiles, print the A/B table ─
    # Resolve (and validate) profiles before the dataset check so an unknown
    # profile name is reported even on an empty dataset.
    names: list[str] = []
    names += args.compare or []
    names += args.profile
    profiles: list[tuple[str, list[str]]] = [(n, _profile_flags(n)) for n in names]
    if args.flags is not None:
        profiles.append(("custom", args.flags.split()))
    if not profiles:
        profiles = [("default", PROFILES["default"])]

    if not songs:
        sys.exit(f"No (audio, .lab) pairs found in {args.dataset}. See eval/README.md.")
    print(f"  dataset: {args.dataset}  ({len(songs)} song(s))")

    results = [run_one_profile(name, flags, songs, work_root, args.prepare_aux)
               for name, flags in profiles]
    print_table(results)
    out_json = persist_results(args.out, args.dataset, len(songs), results)
    print(f"  results → {out_json}\n")


if __name__ == "__main__":
    main()

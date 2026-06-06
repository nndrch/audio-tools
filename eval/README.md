# Chord-detection evaluation harness

Phase 0 of [`docs/chord-detection-implementation-plan.md`](../docs/chord-detection-implementation-plan.md):
the measurement gate. **No accuracy change in Phases 1–3 ships without moving
the numbers here.** Improvements are quantified with MIREX-style weighted chord
recall, not eyeballed.

## Pieces

| File | Role |
|---|---|
| [`score.py`](score.py) | Score one detected `.lab` vs a ground-truth `.lab` (mir_eval weighted recall). |
| [`run.py`](run.py) | Run the chord step over the whole dataset under a flag profile, score each song, print a per-profile aggregate + A/B deltas. |
| `dataset/` | The hand-labeled test set (you populate this — see below). |
| `results/` | Auto-written run outputs + scores. Git-ignored. |

Both scripts run in **`venv_crema`** (it has `chord_chart_render.py`'s deps and
`mir_eval`):

```bash
bash setup.sh                       # installs mir_eval into venv_crema (requirements_crema.txt)
# or, into an existing venv:
./venv_crema/bin/python3.11 -m pip install "mir_eval>=0.7"
```

## Dataset layout

Flat directory of audio + ground-truth annotation pairs sharing a stem:

```
eval/dataset/
  song-01.wav      song-01.lab
  song-02.mp3      song-02.lab
  ...
```

- Audio: `.wav` `.mp3` `.m4a` `.flac` `.aiff` `.ogg`. **Audio is git-ignored**
  (the repo's `*.wav` etc. rules) — reference your own files; don't commit them.
- `.lab`: **tracked in git** — this is the labeled set worth preserving.

### `.lab` format (Harte)

Tab- or space-separated `start  end  label`, seconds, one segment per line:

```
0.000   2.005   C:maj
2.005   4.010   A:min
4.010   6.015   F:maj
6.015   8.020   G:7
8.020  10.025   N
```

Labels are Harte shorthand — `root:quality`, e.g. `C:maj`, `A:min`, `G:7`,
`F:maj7`, `B:hdim7`, or `N` for no chord. This is exactly what
`chord_chart_render.py --lab-out` emits, so detected and reference files are
directly comparable. Target material for the set: **pop / rock /
singer-songwriter** (~15 songs); complex jazz/prog harmony is out of scope.

> Tooling tip: annotations exported from Sonic Visualiser / Chordino, or any
> existing Harte-format set, drop straight in.

## Metrics

`score.py` surfaces duration-weighted recall (MIREX-style) in `[0, 1]`:

| Metric | Meaning |
|---|---|
| `root` | correct root, quality ignored |
| `majmin` | correct root + major/minor third — **the headline pop/rock number** |
| `sevenths` | correct root + triad + 7th ("majmin7") |
| `mirex` | MIREX ≥3-shared-pitch-class criterion |
| `seg` | segmentation quality (under/over-segmentation) |

## Usage

```bash
# baseline (current production defaults)
./venv_crema/bin/python3.11 eval/run.py --profile default

# A/B: prints deltas vs the first profile
./venv_crema/bin/python3.11 eval/run.py --compare default viterbi

# accuracy profile (needs bass stem + sections → pre-generate them)
./venv_crema/bin/python3.11 eval/run.py --profile accuracy --prepare-aux

# ad-hoc flags
./venv_crema/bin/python3.11 eval/run.py --flags "--key-snap --viterbi-smoothing"

# quick smoke run on the first 3 songs
./venv_crema/bin/python3.11 eval/run.py --profile default --limit 3

# score a single pair directly
./venv_crema/bin/python3.11 eval/score.py ref.lab est.lab
```

### Built-in profiles (`run.py`)

| Profile | Flags | Needs aux? |
|---|---|---|
| `default` | _(production defaults: HPSS on, madmom fallback on)_ | no |
| `no-madmom` | `--no-madmom-fallback` | no |
| `hpss-off` | `--hpss-mode off` | no |
| `viterbi` | `--viterbi-smoothing` | no |
| `keysnap` | `--key-snap` | no |
| `accuracy` | `--key-snap --viterbi-smoothing --section-consistency --bass-anchor --slash-chords` | bass stem + sections |

`--prepare-aux` generates the bass stem (`venv_demucs`) and section JSON
(`venv_allin1`) per song and caches them under `results/work/aux/`. Without it,
stem/section-dependent flags are dropped (with a warning) so the run still
completes.

## Workflow for an accuracy change

1. Record the baseline: `run.py --profile default` → note the aggregate.
2. Implement the change behind a flag (or new profile in `PROFILES`).
3. A/B it: `run.py --compare default <your-profile>`.
4. Ship only if `majmin` / `sevenths` improve without regressing `root`/`seg`.
5. The `results/*.json` files are your regression record.

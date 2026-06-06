# Chord Detection & Sheet Quality — Progress Report

**Project:** audio-tools (session materials generator)
**Branch:** `feat/chord-accuracy-eval-harness`
**Date:** 2026-06-06
**Tracks:** [`chord-detection-implementation-plan.md`](chord-detection-implementation-plan.md)

This report records what has been built so far against the implementation plan,
what was verified, and what remains. It is the companion status doc to the plan —
read the plan for the *why* and the design; read this for *where we are*.

---

## TL;DR

- **Phase 0 (validation harness)** — built and verified, except it has **no
  dataset yet**, so it cannot yet produce a baseline number. The dataset is the
  single hard blocker for all downstream accuracy work.
- **Phase 4 (MusicXML output quality, P0/P1)** — built and verified. This phase
  is independent of the harness ("can run in parallel anytime" in the plan), so
  it was completed first as a low-risk win.
- **Phases 1, 2, 3 (accuracy core, profile, dual model)** — not started. All are
  gated on a measured Phase 0 baseline.

Nothing in this branch changes the default detection behaviour. `--lab-out` is
off by default; the MusicXML changes only affect the `.musicxml` export path.

---

## ✅ Built & verified

### Phase 0 — Validation harness (the measurement gate)

| File | What it does | Status |
|---|---|---|
| [`eval/score.py`](../eval/score.py) | Scores one detected `.lab` vs a ground-truth `.lab` with `mir_eval` duration-weighted recall: `root`, `majmin`, `sevenths`, `mirex`, `seg`. Aligns both annotations to a common span, fills gaps with `N`. | **Done, smoke-tested** |
| [`eval/run.py`](../eval/run.py) | Runs the chord step over the whole dataset under a flag profile, captures `--lab-out`, scores each song, prints per-profile aggregate + A/B deltas. 6 built-in profiles; `--prepare-aux` generates the bass stem (`venv_demucs`) + sections (`venv_allin1`) for stem/section-dependent profiles; persists results JSON for regression tracking. | **Done (untested end-to-end — needs dataset)** |
| [`eval/README.md`](../eval/README.md) | Dataset layout, `.lab` format, metric definitions, usage, the per-change workflow. | **Done** |
| `eval/dataset/.gitkeep` | Placeholder. Audio is git-ignored; ground-truth `.lab` files **are** tracked. | **Done** |

Supporting wiring:
- `mir_eval>=0.7` added to [`requirements_crema.txt`](../requirements_crema.txt);
  `setup.sh:73` already installs that file, so `bash setup.sh` pulls it in.
  (`venv_crema` currently has `mir_eval 0.8.2`.)
- `eval/results/` git-ignored in [`.gitignore`](../.gitignore) (reproducible
  outputs); the `.lab` ground truth under `eval/dataset/` is deliberately tracked.

**`--lab-out` flag** ([`chord_chart_render.py:2167`](../chord_chart_render.py#L2167)) —
the harness's hook into the pipeline. Writes the detected chord sequence as a
Harte `.lab` (`start  end  label`, seconds) **before** LilyPond rendering, so the
labels survive even if PDF rendering fails. Emits the crema `root:quality` strings
the codebase already uses (`mir_eval`-parseable); `X`/empty → `N`. Off by default.

**Verification performed:**
- All three Python files compile under `venv_crema` (`py_compile`).
- `score.py` smoke-tested with synthetic pairs: identical files → all metrics
  `1.000`; a 3/4-root-correct, 2/4-third-correct mismatch → `root 0.750`,
  `majmin 0.500`, exactly as the math predicts.
- `--lab-out` writer confirmed field-consistent with the segment dict structure
  (`time` / `beats` / `chord`).

### Phase 4 — MusicXML output quality (P0/P1)

Rewrote the `<harmony>` serialization in
[`chord_chart_render.py`](../chord_chart_render.py) (the `bar_chords_to_musicxml`
path and its helper). The PDF/LilyPond path is untouched.

**P0:**
- **`kind`-corruption fix** — `ChordSymbol` is now built from an explicit
  `(root, kind, bass)` using the MusicXML kind-value enum
  (`_QUALITY_TO_M21_KIND`) instead of a figure string. Figure-string parsing
  silently mis-read qualities — `hdim7` decoded to *minor-seventh*, and unknown
  qualities collapsed to a bare *major* triad. Unknown qualities now fall back to
  the nearest base (major/minor via `_QUALITY_TO_SIMPLE`), never silently to major.
- **Tempo** — first measure gets a `MetronomeMark` (quarter referent, matching the
  1.0-ql-per-grid-beat model), emitting both `<metronome>` and `<sound tempo>`.
- **Composer junk** — `score.metadata.composer = ""` so renderers stop printing
  "Music21" on the chart.

**P1:**
- **`kind text`** — `chordKindStr` carries the printed Berklee suffix (`m`, `maj7`,
  `ø7`, …) so PDF, terminal, and MusicXML agree on the symbol.
- **Treble clef** + **final barline** (`light-heavy`).
- **System breaks** — a `SystemLayout(isNew=True)` every `bars_per_line` measures
  so MusicXML line breaks match the PDF instead of MuseScore auto-packing.

**Verification performed (synthetic 6- and 8-bar charts → serialized MusicXML):**
- `<metronome>` + `per-minute>120` present.
- `B:hdim7` → `<kind>half-diminished`, `E:dim7` → `diminished-seventh`, unknown
  quality → `major` (fallback, not silent corruption).
- Slash chord `A:min` + bass E → `<bass>` element.
- Sections → `rehearsal` marks; final bar → `light-heavy`; 4-bar break →
  `<print new-system="yes">`.
- Composer "Music21" string absent from output.

---

## ⛔ Blocker

**No labeled dataset.** `eval/dataset/` holds only `.gitkeep`. Until it has
`<name>.<audio>` + `<name>.lab` pairs, `run.py` cannot produce the **baseline
number** that Phase 0.4 requires, and every accuracy change in Phases 1–3 is
defined as "must beat that baseline." This is a data-sourcing/licensing decision
(plan's open risk #1): own catalog vs. a public annotated set (e.g. an
Isophonics/Billboard pop subset). ~15 pop/rock/singer-songwriter songs with
Harte `.lab` ground truth are the target.

---

## ⬜ Not started (gated on a Phase 0 baseline)

| Phase | Item | Plan ref | Notes |
|---|---|---|---|
| 0.1 | Assemble labeled test set | plan §0 | The blocker above. |
| 0.4 | Record `master` baseline | plan §0 | One `run.py --profile default` once data exists. |
| 1.1 | Analytic beat grid for stabilized input | plan §1.1 | S / low risk. |
| 1.2 | Beat-sync **median** posterior aggregation | plan §1.2 | M / med. New path over `crema_probs`. |
| 1.3 | Reduced-vocab **decoding** (sum mass, not argmax-then-collapse) | plan §1.3 | M / med. Highest-leverage change. |
| 1.4 | HPSS vs. Demucs-harmonic input A/B | plan §1.4 | Decide by measurement; keep whichever wins. |
| 2 | `--profile accuracy` (flip existing priors on) | plan §2 | S–M / low. Also web schema wiring. |
| 3 | Disagreement-aware dual model (madmom everywhere + arbitration) | plan §3 | M / med. Sequenced last (most likely to regress). |
| 4 | Remaining MusicXML polish, if any surfaces in MuseScore round-trip | plan §4 | P0/P1 done; round-trip QA still worth doing once a real chart exists. |

---

## How to resume

```bash
# 1. Put audio + ground-truth .lab pairs in eval/dataset/ (see eval/README.md).
# 2. Baseline:
./venv_crema/bin/python3.11 eval/run.py --profile default
# 3. From there, every Phase 1–3 change is an A/B against that baseline:
./venv_crema/bin/python3.11 eval/run.py --compare default <new-profile>
#    Ship only if majmin / sevenths improve without regressing root / seg.
```

The `eval/results/*.json` files are the regression record.

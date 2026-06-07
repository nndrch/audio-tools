# Chord Detection & Sheet Quality — Progress Report

**Project:** audio-tools (session materials generator)
**Branch:** `feat/chord-accuracy-eval-harness`
**Date:** 2026-06-06
**Tracks:** [`chord-detection-implementation-plan.md`](chord-detection-implementation-plan.md)

This report records what has been built so far against the implementation plan, what was verified, and what remains. It is the companion status doc to the plan — read the plan for the *why* and the design; read this for *where we are*.

---

## TL;DR

- **Phase 0 (validation harness)** — built and verified, except it has **no dataset yet**, so it cannot yet produce a baseline number. The dataset is the single hard blocker for all downstream accuracy work.
- **Phase 4 (MusicXML output quality, P0/P1)** — built and verified. This phase is independent of the harness ("can run in parallel anytime" in the plan), so it was completed first as a low-risk win.
- **Phases 1, 2, 3 (accuracy core, profile, dual model)** — not started. All are gated on a measured Phase 0 baseline.

Nothing in this branch changes the default detection behaviour. `--lab-out` is off by default; the MusicXML changes only affect the `.musicxml` export path.

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
- `mir_eval>=0.7` added to [`requirements_crema.txt`](../requirements_crema.txt); `setup.sh:73` already installs that file, so `bash setup.sh` pulls it in. (`venv_crema` currently has `mir_eval 0.8.2`.)
- `eval/results/` git-ignored in [`.gitignore`](../.gitignore) (reproducible outputs); the `.lab` ground truth under `eval/dataset/` is deliberately tracked.

**`--lab-out` flag** ([`chord_chart_render.py:2167`](../chord_chart_render.py#L2167)) — the harness's hook into the pipeline. Writes the detected chord sequence as a Harte `.lab` (`start  end  label`, seconds) **before** LilyPond rendering, so the labels survive even if PDF rendering fails. Emits the crema `root:quality` strings the codebase already uses (`mir_eval`-parseable); `X`/empty → `N`. Off by default.

**Verification performed:**
- All three Python files compile under `venv_crema` (`py_compile`).
- `score.py` smoke-tested with synthetic pairs: identical files → all metrics `1.000`; a 3/4-root-correct, 2/4-third-correct mismatch → `root 0.750`, `majmin 0.500`, exactly as the math predicts.
- `--lab-out` writer confirmed field-consistent with the segment dict structure (`time` / `beats` / `chord`).

### Phase 4 — MusicXML output quality (P0/P1)

Rewrote the `<harmony>` serialization in [`chord_chart_render.py`](../chord_chart_render.py) (the `bar_chords_to_musicxml` path and its helper). The PDF/LilyPond path is untouched.

**P0:**
- **`kind`-corruption fix** — `ChordSymbol` is now built from an explicit `(root, kind, bass)` using the MusicXML kind-value enum (`_QUALITY_TO_M21_KIND`) instead of a figure string. Figure-string parsing silently mis-read qualities — `hdim7` decoded to *minor-seventh*, and unknown qualities collapsed to a bare *major* triad. Unknown qualities now fall back to the nearest base (major/minor via `_QUALITY_TO_SIMPLE`), never silently to major.
- **Tempo** — first measure gets a `MetronomeMark` (quarter referent, matching the 1.0-ql-per-grid-beat model), emitting both `<metronome>` and `<sound tempo>`.
- **Composer junk** — `score.metadata.composer = ""` so renderers stop printing "Music21" on the chart.

**P1:**
- **`kind text`** — `chordKindStr` carries the printed Berklee suffix (`m`, `maj7`, `ø7`, …) so PDF, terminal, and MusicXML agree on the symbol.
- **Treble clef** + **final barline** (`light-heavy`).
- **System breaks** — a `SystemLayout(isNew=True)` every `bars_per_line` measures so MusicXML line breaks match the PDF instead of MuseScore auto-packing.

**Verification performed (synthetic 6- and 8-bar charts → serialized MusicXML):**
- `<metronome>` + `per-minute>120` present.
- `B:hdim7` → `<kind>half-diminished`, `E:dim7` → `diminished-seventh`, unknown quality → `major` (fallback, not silent corruption).
- Slash chord `A:min` + bass E → `<bass>` element.
- Sections → `rehearsal` marks; final bar → `light-heavy`; 4-bar break → `<print new-system="yes">`.
- Composer "Music21" string absent from output.

---

## Dataset: built in parallel, **not** a blocker

The labelled dataset (`eval/dataset/`) is being assembled by the team in parallel, so the detection phases are **decoupled from it**: each ships behind a **default-off flag** (production behaviour unchanged) and is *validated* as songs arrive — not blocked on them. The tool **self-improves as data points are added**: re-running the harness against a growing set, gated against a stored baseline, auto-reports whether a flag helps or regresses.

Intake is DAW-agnostic (Logic, Pro Tools, anything):

```
musician fills eval/annotation-template.csv  (time, chord — any DAW)
        → eval/import_chords.py  (rejects typos; computes spans)
        → eval/dataset/<name>.lab  (scorer-ready)
```

See [`eval/ANNOTATION-GUIDE.md`](../eval/ANNOTATION-GUIDE.md) (hand to musicians) and [`eval/import_chords.py`](../eval/import_chords.py).

---

## Web test-arm: locked accuracy structure

To A/B this branch against the current build, the web UI's chord-accuracy configuration is **locked to a fixed structure** rather than user-controlled, so every render is comparable. Single flag: `STRUCTURE_LOCKED` in [`web/lib/validation.ts`](../web/lib/validation.ts) (set `false` to restore user-controlled settings). It is enforced in three places:

- **Server-side** ([`web/lib/pipeline.ts`](../web/lib/pipeline.ts) `applyLockedStructure`) — forces the flags onto the CLI regardless of (stale) client state.
- **Client hydration** ([`web/app/page.tsx`](../web/app/page.tsx)) — skips `localStorage`, so non-structural knobs sit at defaults.
- **UI** ([`web/components/AdvancedSettings.tsx`](../web/components/AdvancedSettings.tsx)) — the advanced-settings toggle is replaced with a read-only locked notice.

Locked structure (`LOCKED_STRUCTURE`): detect sections · **HPSS + drum removal** · bass-anchored roots · same-named-section consistency · key-snap · key-aware Viterbi smoothing. Stems + sections are forced on (dependencies); slash chords off. Per-song fields (title, key, time-sig, bpm) stay user-editable.

> ⚠ **`hpss-no-drums` caveat.** The plan (§1.4) keeps `hpss` as the default and says decide drum-removal *by measurement* — it can be out-of-distribution and lose. It's locked here **deliberately, as the test arm**, precisely so this A/B settles it. It also forces a Demucs pass (slower). Note: this arm exercises the Phase 2/3 correction levers on the **current** detection core — the Phase 1 work (M1–M3) is not wired into the pipeline yet (M5 pending).

---

## Development routine (how the next phases are built)

Standing rules for this work:

1. **Small, measurable, testable milestones.** Each milestone is one function or one wiring step with a defined, checkable result.
2. **Test in isolation — never run the whole pipeline to validate.** New DSP is exercised with **synthetic inputs** to the single function (we have no audio yet, and a full run is slow/non-deterministic). Each milestone ships with its own unit check.
3. **Stop after every completed milestone** and report before starting the next.
4. **Default-off.** Nothing changes production behaviour until a flag is flipped and the harness shows a win on real data.

Adding a new phase later follows the same shape: write the function → synthetic test → wire behind a flag → register an eval profile → A/B with `--gate`.

---

## Milestones — Phase 1 (detection core), Phase 2, self-improving harness

| # | Milestone | Isolated test | Status |
|---|---|---|---|
| M1 | `analytic_beat_grid()` — beat times from BPM + downbeat phase | exact period spacing, phase honoured, coverage to end | ✅ 10/10 |
| M2 | `beat_sync_posteriors()` — mass-preserving posterior per beat window | mean tracks summed mass; trimmed_mean resists transients; median diverges; degenerate→uniform | ✅ 20/20 |
| M3 | `marginalize_to_reduced_vocab()` — summed mass over `{maj,min}` / 7th vocab | summed mass beats argmax-then-collapse on the C-family-vs-Am case; X→N; stride; coverage | ✅ 19/19 |

> **Design note (M2, from adversarial review).** The plan called for a *median* posterior aggregation. Verification showed a per-class median is **not mass-preserving** and contradicts Phase 1.3's "decode on summed mass" — a steady minority class can beat a dominant class whose mass rotates across frames. So the **default is `mean`** (which *is* summed-mass, and commutes with marginalisation), with `trimmed_mean` (drops outlier *frames*, stays mass-preserving) for transient resistance and `median` kept as an A/B-only option. **M5 must marginalise per-frame (M3) before aggregating** so any non-`mean` aggregator operates on whole chord families, not competing fine classes.

> **Design note (M3, from adversarial review).** Verification (verified against the live crema model) caught that crema emits **two** no-chord classes — `N` and `X` (unknown) — and `X` mass was being silently dropped, which would render a spurious chord on noisy beats. Fixed: `X`→`N` (the file-wide convention). Three contracts are now pinned for later milestones: **(M4)** beat *confidence* must be a normalised share (`winning_col / row_sum`), never the raw summed mass (which can exceed 1 and would break the 0.70–0.80 gates); **(M4)** the `add_7th` decode should be **two-stage** (win root on the `{maj,min}` view, then refine quality) to avoid the family-split regressing the root; **(M5)** must **not** re-run `simplify_chord()` on reduced labels (it would collapse `dim`→`min`, `sus`→`maj`).
| M4 | reduced-vocab **decode** → `beat_chords` assembly | output dict shape/keys match `hybrid_bar_chords` input | ⬜ |
| M5 | wire M1–M4 into `main()` behind `--analytic-beats` / `--reduced-vocab-decode` | compiles; default path byte-identical (flags off) | ⬜ |
| M6 | Phase 2 `--profile accuracy` first-class in pipeline/CLI + dep wiring | flag→flag-set mapping; conflict error with `--skip-stems`/`--skip-sections` | ⬜ |
| M7 | self-improving harness: baseline/champion + `run.py --gate` + promote | synthetic results JSON → correct improve/regress verdict; works at 0 songs | ⬜ |
| M8 | register Phase 1 & 3 eval profiles + extension points | profiles list + flags resolve | ⬜ |
| M9 | final compile sweep + doc update | `py_compile` clean; this table filled in | ⬜ |

Phase 3 (disagreement-aware dual model) and Phase 1.4 (HPSS↔Demucs A/B) remain profile-level work to schedule after M1–M8 land and the first data points exist.

---

## How to resume / add a data point

```bash
# Add ground truth (musician sends a filled template + the song):
./venv_crema/bin/python3.11 eval/import_chords.py incoming/song-01.csv -o eval/dataset

# Baseline (current production defaults):
./venv_crema/bin/python3.11 eval/run.py --profile default

# A/B a phase flag against the baseline; ship only on a majmin/sevenths win
# with no root/seg regression:
./venv_crema/bin/python3.11 eval/run.py --compare default <phase-profile>
```

The `eval/results/*.json` files are the regression record.

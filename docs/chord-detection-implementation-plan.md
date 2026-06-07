# Chord Detection & Sheet Quality — Implementation Plan

**Project:** audio-tools (session materials generator)
**Date:** 2026-06-06
**Synthesizes two source docs:**

- `chord-detection-improvements.md` — recognition-accuracy plan (input isolation, reduced-vocab decoding, prior profile, beat-sync chroma, dual model, sheet quality).
- `chord-chart-musicxml-improvements.md` — MusicXML output-quality proposal (tempo, composer junk, `kind` corruption, clef/barline, layout) derived from `chord-chart-generation-reference.md`.

> **Status: plan only — no code changed.** This document is the bridge from the two proposal docs to concrete, ordered, validated engineering work.

---

## 0. Ground truth — what the code already does

Before planning, the actual detection flow was traced ([`chord_chart_render.py:1853` `main()`](../chord_chart_render.py#L1853)). Several recommendations are **already partly built**, which reshapes the work:

| Source recommendation | Current reality in code | Net work |
|---|---|---|
| #1 HPSS harmonic-isolated input | **Already the default** — `--hpss-mode hpss` strips percussive, keeps harmonic ([`_apply_hpss_preprocessing:1815`](../chord_chart_render.py#L1815)). `hpss-no-drums` is opt-in. | Build the A/B harness; decide on aggressive path. Not a default change. |
| #2 Reduced-vocab **decoding** | **Not done at decode time.** CREMA emits argmax over 170 classes → `simplify_chord()` collapses *after* ([`main:1964-1967`](../chord_chart_render.py#L1964)). Posteriors (`crema_probs`, 170-wide) **are** available, and a maj/min marginalizer exists ([`_marginalize_crema_to_root_mode:1361`](../chord_chart_render.py#L1361)) but only the Viterbi path uses it. | Real architectural change — decode on summed posterior mass, not argmax-then-collapse. |
| #3 Accuracy-first default profile | `--bass-anchor`, `--key-snap`, `--viterbi-smoothing`, `--section-consistency` all exist but are **opt-in**; orchestrated in `main()` ([`1991-2120`](../chord_chart_render.py#L1991)) and gated/sequenced in [`pipeline.py:355`](../pipeline.py#L355). | Add a profile that flips them on + wire stem dependencies. Low code risk. |
| #4 Median beat-synchronous aggregation | **Not done.** `beat_sync_chords()` does a confidence-weighted vote over **argmax labels** per beat ([`chord_sheet.py:128`](../chord_sheet.py#L128)), not median over posteriors. | New aggregation path over `crema_probs`. Couples tightly with #2. |
| #5 Always-on disagreement-aware dual model | madmom runs **only** on bars with mean conf `< 0.70` ([`main:1993`](../chord_chart_render.py#L1993)); it already re-detects the **whole** song via `--dump-segments`. | Run everywhere + disagreement arbitration. Medium effort. |
| #6 MusicXML `<harmony>` correctness | Mostly done: root/kind/bass, one-per-change via segments, key+time written, sections→rehearsal ([`bar_chords_to_musicxml:848`](../chord_chart_render.py#L848)). | Remaining gaps = the P0/P1 items in the MusicXML proposal doc. |

**Two correctness notes discovered while tracing:**

1. **Beats are re-detected with librosa here, not madmom.** `chord_chart_render.py` calls `detect_beats()` (librosa) ([`main:1913`](../chord_chart_render.py#L1913)) — madmom downbeats live only in the stabilizer. But the input is **already beat-stabilized to a perfect grid**, so the beat grid could instead be derived analytically from the known BPM (deterministic, exact). This is a free accuracy + determinism win that #4 depends on. See Phase 1.
2. The maj/min marginalizer ([`:1361`](../chord_chart_render.py#L1361)) lumps `7`, `aug`, `sus2/4` into **major** and `dim/dim7/hdim7` into **minor**. A reduced vocab of `{maj, min, maj7, min7, 7, dim, sus}` needs a **finer** marginalizer. Reused by #2.

---

## Phase 0 — Validation harness (keystone; build first)

**Both source docs make this a precondition.** Nothing below ships without a number.

- **0.1 Labeled test set.** Assemble ~15 songs representative of the target material (pop / rock / singer-songwriter) with ground-truth chord annotations in Harte/`.lab` format (time-stamped `start end label`). Store under `eval/dataset/` (audio refs + `.lab` files; keep audio out of git — reference by path/checksum).
- **0.2 Metric.** Add `eval/score.py` computing **MIREX-style weighted chord-symbol recall** against the **reduced vocabulary** using `mir_eval.chord` (already a standard dep; add to `requirements_crema.txt`). Report `majmin`, `majmin7`, and `root` recall.
- **0.3 Runner.** `eval/run.py` takes a flag set, runs the full chord step on each song, emits the detected chords as `.lab`, scores them, and prints a per-song + aggregate table. Must support A/B: run two flag profiles and print the delta.
- **0.4 Baseline.** Record current `master` numbers as the regression floor.

**Effort:** M. **Risk:** low. **Unblocks:** every other phase (each is gated on a measured win, not eyeballing).

---

## Phase 1 — Architectural core (highest leverage)

These three are coupled; implement and validate together. Touches the frame→beat→bar path: `detect_chords_crema` → (new) beat-median posteriors → (new) reduced-vocab decode → `hybrid_bar_chords`.

### 1.1 Analytic beat grid for stabilized input

- When the input is the stabilized WAV (or `--bpm` is known from the `.bpm` sidecar), derive beat times from BPM + downbeat phase instead of re-running librosa ([`main:1913`](../chord_chart_render.py#L1913)). Deterministic, exact, removes a noise source that #4 would otherwise inherit.
- Fall back to `detect_beats()` when no grid is known (standalone use on raw audio).

### 1.2 Beat-synchronous **median** posterior aggregation (#4)

- New function `beat_sync_posteriors(crema_probs, times, beat_times)` → `(n_beats, n_classes)` using the **median** across the frames inside each beat window (mean as a fallback for ≤2-frame windows). Median resists drum transients and brief passing tones — exactly the energy that flips major/minor.
- Replaces the argmax-label vote inside `beat_sync_chords` ([`chord_sheet.py:164-182`](../chord_sheet.py#L164)) for the chord path; keep the old function for the standalone `chord_sheet.py` CLI or refactor both onto the new core.

### 1.3 Reduced-vocabulary **decoding** (#2)

- Generalize `_marginalize_crema_to_root_mode` into `marginalize_to_reduced_vocab(probs, vocab, add_7th)` producing summed probability mass over `{maj, min}` (default) or `{maj, min, maj7, min7, 7, dim, sus}` (`--add-7th`).
- Decode per beat by **argmax over summed mass** (optionally Viterbi over the reduced set — the existing `_viterbi_decode` / `_build_key_transition_matrix` at [`:1455`/`:1391`](../chord_chart_render.py#L1391) already operate on marginalized posteriors and can be reused).
- **Why this is the lever:** if CREMA spreads mass `C:maj 0.30 / C:maj7 0.30 / C:7 0.25` but argmaxes to `A:min 0.35`, today's argmax-then-`simplify_chord` picks **Am**; summing the C-major family (0.85) over Am (0.35) picks **C**. Removes the over-labeled-extension and many root-swap errors *before* any smoothing operates on the labels.
- `simplify_chord()` ([`:147`](../chord_chart_render.py#L147)) becomes a display/fallback helper, no longer the primary quality decision.

### 1.4 HPSS vs. Demucs-harmonic input A/B (#1)

- Default stays `hpss` (already the safe, in-distribution default the doc recommends).
- Add the aggressive path as an option: Demucs harmonic mix (bass+guitar+piano+other, drop vocals+drums) fed to CREMA. Reuse the stems-first ordering already in [`pipeline.py:355`](../pipeline.py#L355).
- **Decide by measurement** on the Phase 0 set — the doc's own caveat is that the cleaner stem can be out-of-distribution and *lose*. Keep whichever wins; do not assume.

**Effort:** L. **Risk:** medium (changes the core decision). **Validation:** Phase 0 recall must beat baseline on `majmin`/`majmin7`; ship only the variants that win.

---

## Phase 2 — Accuracy-first default profile (#3)

Flip the existing, already-implemented priors from opt-in to a named profile. Each maps to an observed error class:

| Error class | Lever (exists) | Code |
|---|---|---|
| Relative/fifth root swaps (C↔Am, C↔G) | `--bass-anchor` | [`_apply_bass_anchor:1626`](../chord_chart_render.py#L1626) |
| Major/minor 3rd flips | `--key-snap` + `--viterbi-smoothing` | [`key_snap_bars:614`](../chord_chart_render.py#L614), [`_apply_viterbi_smoothing:1511`](../chord_chart_render.py#L1511) |
| One-off errors in repeated parts | `--section-consistency` | [`_apply_section_consistency:1727`](../chord_chart_render.py#L1727) |

- Add `--profile accuracy` to `pipeline.py` (and the web advanced-settings schema in [`web/lib/validation.ts`](../web/lib/validation.ts) + [`web/lib/pipeline.ts`](../web/lib/pipeline.ts)) that enables: `viterbi-smoothing`, `key-snap`, `section-consistency`, `bass-anchor`, `slash-chords`.
- **Dependency wiring:** `bass-anchor`/`slash-chords` require the bass stem → forces stems-first (already handled, [`pipeline.py:459`](../pipeline.py#L459)). `section-consistency` requires section detection (on unless `--skip-sections`). Surface a clear error if the profile is combined with `--skip-stems`/`--skip-sections` (pattern already exists at [`pipeline.py:344`](../pipeline.py#L344)).
- Keep `default` profile as today (speed). Document the runtime trade-off (accuracy profile ≈ +Demucs pass).

**Effort:** S–M. **Risk:** low (no new algorithms). **Validation:** profile must beat the default profile on Phase 0; confirm each lever's marginal contribution by ablation.

---

## Phase 3 — Disagreement-aware dual model (#5)

- Run madmom across **all** bars, not just `< 0.70` (it already detects the whole song via `--dump-segments`, [`main:2002`](../chord_chart_render.py#L2002) — the cost is already paid).
- Compute per-bar **agreement** between the reduced-vocab CREMA decode and madmom.
- On **disagreement**, arbitrate with combined evidence rather than raw confidence: bass-anchor root + diatonic/key prior + section vote. Disagreement bars are where errors concentrate, so this is targeted.
- Add disagreement stats to the analysis JSON ([`main:2227`](../chord_chart_render.py#L2227)) for debugging and as a quality signal.

**Effort:** M. **Risk:** medium (arbitration can regress if mis-tuned — gate hard on Phase 0). **Validation:** must not regress agreement-bar accuracy; net recall win required.

---

## Phase 4 — Sheet / MusicXML output quality (#6)

The reduced vocab from Phase 1 makes `ChordSymbol` serialization cleaner. Remaining gaps are exactly the **MusicXML proposal doc** — execute its P0/P1 here (see `chord-chart-musicxml-improvements.md` for full detail and measured evidence):

- **P0:** add tempo (`metronome` + `sound`); drop the `Music21` composer; fix the `_QUALITY_TO_M21` corruption where `hdim7`→`minor-seventh` and unknown qualities collapse to **major** ([`:808`](../chord_chart_render.py#L808)) — less likely after Phase 1's constrained vocab, but still emit the nearest valid `kind`.
- **P1:** `kind text` Berklee symbols (`mi`, `Maj7`, `ø7`, `°`); treble clef + final barline; 4-bars/line system breaks for PDF↔MusicXML parity.

**Effort:** S (localized to `bar_chords_to_musicxml` + 2 helpers; PDF path untouched).
**Risk:** low. **Validation:** round-trips in MuseScore + a web renderer (reference §9 checklist).

---

## Ordering & rationale

```
Phase 0  Validation harness        ← gates everything (no metric, no merge)
Phase 1  Median posteriors (#4)
         + reduced-vocab decode (#2)  ← carries most of the accuracy gain
         + analytic beat grid (1.1)
         + HPSS/Demucs A/B (#1)
Phase 2  Accuracy-first profile (#3)  ← flips existing priors; low risk
Phase 3  Disagreement dual model (#5)
Phase 4  MusicXML polish (#6)         ← independent; can run in parallel anytime
```

Phase 1 is the only true architectural change and the highest leverage. Phases 2 and 4 are low-risk and could be done first for quick wins (2 needs no new algorithms; 4 is independent). Phase 3 is the most likely to regress and is sequenced last so the Phase 0 harness and Phase 1/2 gains are locked in before adding arbitration.

## Per-phase effort / risk summary

| Phase | Item | Effort | Risk | Gated on Phase 0 |
|---|---|---|---|---|
| 0 | Eval harness + labeled set | M | Low | — |
| 1.1 | Analytic beat grid | S | Low | Yes |
| 1.2 | Median beat-sync posteriors | M | Med | Yes |
| 1.3 | Reduced-vocab decode | M | Med | Yes |
| 1.4 | HPSS vs Demucs A/B | M | Med | Yes |
| 2 | Accuracy-first profile | S–M | Low | Yes |
| 3 | Disagreement dual model | M | Med | Yes |
| 4 | MusicXML P0/P1 | S | Low | No |

## Risks & open questions

- **Test-set licensing.** Hand-labeled pop/rock needs audio we can store/reference; confirm source (own catalog vs. public annotated sets like a pop subset of Isophonics/Billboard).
- **`add_7th` interaction with reduced vocab.** Default simplifies to maj/min; the reduced decode must respect `--add-7th` to expose 7ths/sus, and the finer marginalizer must match.
- **Determinism of `librosa.effects.hpss`** margin tuning — fold `--hpss-margin` into the A/B sweep.
- **Profile runtime.** Accuracy profile forces a Demucs pass even when the user didn't ask for stems; document and/or make the bass-only stem a lighter extraction.

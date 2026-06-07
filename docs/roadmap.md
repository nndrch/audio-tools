# audio-tools — Project Roadmap

**Project:** audio-tools — end-to-end "session materials" pipeline. Drop in a song, get back a beat-stabilized WAV, a chord chart (PDF + MusicXML), isolated stems, an optional backing track, and an analysis JSON — from a CLI, a local web UI, or (planned) a double-click desktop app.

This is the project-wide roadmap: the stages already shipped, where we are now, and what's next. For the deep design/status of any single workstream, follow the linked companion docs.

**Companion docs:** [`web-mvp-prd.md`](web-mvp-prd.md) · [`desktop-mvp-prd.md`](desktop-mvp-prd.md) · [`chord-detection-implementation-plan.md`](chord-detection-implementation-plan.md) · [`chord-detection-progress.md`](chord-detection-progress.md) · [`advanced-settings.md`](advanced-settings.md) · [`library-alternatives.md`](library-alternatives.md)

---

## At a glance

```
Stage 1  Core CLI pipeline              ✅ shipped   (3 tools + orchestrator)
Stage 2  Web MVP — Session Materials    ✅ shipped   (local Next.js wrapper)
Stage 3  Output quality & analysis      ✅ shipped   (MusicXML, sections, stem presence)
Stage 4  Musiversal brand + backing     ✅ shipped   (brand UI, session-type guide tracks)
Stage 5  Detection-accuracy levers      ✅ shipped   (HPSS, bass-anchor, Viterbi, slash, allin1)
Stage 6  Chord-accuracy eval harness    🔨 current   (measurement gate + phased detection rework)
Stage 7  Desktop app (Electron)         ⬜ planned   (bundled Python, double-click installer)
Stage 8  Cloud worker / remote backend  ⬜ future    (the production target)
```

---

## ✅ Concluded stages

### Stage 1 — Core CLI pipeline *(May 2026)*
The foundation: three composable command-line tools wired by an orchestrator.

- **Beat Stabilizer** ([`beat_stabilizer.py`](../beat_stabilizer.py)) — warps audio so every beat locks to a perfect grid, with a DAW-ready intro trim (starts one bar before beat 1).
- **Chord Chart** ([`chord_chart_render.py`](../chord_chart_render.py), [`chord_sheet.py`](../chord_sheet.py)) — two-model detection (crema primary + madmom fallback on low-confidence bars), bar-level quantization, hybrid bar/beat chords, `--add-7th`, bar-phase alignment, and an analysis JSON. Renders a PDF via LilyPond.
- **Stem Splitter** ([`stem_splitter.py`](../stem_splitter.py)) — up to 6 stems via Demucs.
- **Pipeline** ([`pipeline.py`](../pipeline.py)) — runs all three in sequence, passing BPM / downbeats / meter between steps via a `.bpm` sidecar.

### Stage 2 — Web MVP: Session Materials Creator *(May 2026)*
A local-only Next.js wrapper around the CLI so non-CLI users can drop a file and download a ZIP. Built in the 6 phases of [`web-mvp-prd.md`](web-mvp-prd.md) (repo prep → progress instrumentation → upload screen → pipeline spawn + processing screen → success/ZIP/cleanup → polish). Cloud (Vercel) was ruled out for the worker — long CPU jobs, native binaries, ML runtimes — so both layers run on `localhost`, with `web/lib/pipeline.ts` left as a clean swap point for a future remote worker. Followed by a hardening pass: stranded-job recovery on restart, live progress, manual cancel, race-free status writes, artifacts moved to `$TMPDIR`.

### Stage 3 — Output quality & richer analysis *(May 2026)*
Made the artifacts editable and the analysis honest.

- **MusicXML export** alongside the PDF — charts open in MuseScore / Sibelius / Dorico.
- **Downbeat tracking + meter inference** (madmom DBN) and a **tempo-change early stop** that aborts cleanly on multi-tempo songs instead of mangling the warp.
- **Song-section detection** (MSAF) → A/B/C rehearsal marks on PDF + MusicXML.
- **Stem presence detection** — every stem gets `present` / `rms_dbfs_peak` / `loud_seconds` so bleed isn't mistaken for a real take; done page redesigned as an analysis report.

### Stage 4 — Musiversal brand, backing tracks & UX *(May 2026)*
- **Musiversal-branded UI** — brand fonts embedded as base64 `@font-face` (fully offline), ebony CTAs, pill audio players, brand fonts in the PDF chart.
- **Session-type backing tracks** — pick the instrument you'll play; the app mixes the other stems into a guide track (`--session-type`, `--backing-track-out`).
- **UX** — promoted song info, friendlier advanced settings, pre-analysis on upload (BPM / key / meter shown instantly), settings panels gated behind a chosen file.

### Stage 5 — Detection-accuracy levers *(May 2026)*
First wave of accuracy work — a toolbox of opt-in correction levers.

- **HPSS chord-input cleaning** (strip percussive, keep harmonic before detection).
- **Bass-anchored root correction** + **section-aware chord consistency** (repeat parts agree).
- **Key-conditioned Viterbi smoothing** and **slash-chord (inversion) labelling**.
- **allin1 section detection** (parallel execution + UI controls) alongside MSAF.
- Robustness fixes: BPM octave correction, beat-tracker octave-flip normalization, exposed section threshold, false tempo-change fix. Survey of swap-in options in [`library-alternatives.md`](library-alternatives.md).

---

## 🔨 Current stage — Stage 6: Chord-accuracy eval harness + phased detection rework

Branch `feat/chord-accuracy-eval-harness`. Stage 5 added levers but no way to *measure* whether any of them help. This stage builds the measurement gate and reworks the detection core on top of it, so the tool **self-improves as labelled songs are added** — every change ships behind a default-off flag and is gated on a measured win, never blocked on the dataset.

**Done & verified**
- **Phase 0 — Validation harness:** `eval/score.py` (mir_eval recall scorer), `eval/run.py` (dataset runner + A/B deltas), the `--lab-out` pipeline hook, a DAW-agnostic annotation intake (`annotation-template.csv` → `import_chords.py` → scorer `.lab`), and a locked web test-arm for A/B. Verified end-to-end *except* it has no dataset yet, so it can't print a baseline number — the dataset is the one remaining blocker.
- **Phase 4 — MusicXML output quality (P0/P1):** `kind`-corruption fix, tempo, composer-junk removal, Berklee kind-text, clef/barline, system breaks. Done first as a low-risk win.

**In progress — Phase 1 (architectural core):** move from *argmax-then-collapse* to *decode on summed posterior mass* over an analytic beat grid. Milestones M1–M3 built and unit-tested (`analytic_beat_grid`, `beat_sync_posteriors`, `marginalize_to_reduced_vocab`); **M4 (reduced-vocab decode → `beat_chords`) is next**, then M5 wires it behind flags.

> Full milestone table and status live in [`chord-detection-progress.md`](chord-detection-progress.md); the design rationale is in [`chord-detection-implementation-plan.md`](chord-detection-implementation-plan.md).

---

## ⬜ Next stages

### Finish Stage 6 (chord detection)
1. **Phase 1 core** — M4 (reduced-vocab decode) → M5 (wire behind `--analytic-beats` / `--reduced-vocab-decode`, default path unchanged).
2. **Phase 2 — `--profile accuracy`** — promote the Stage 5 levers into one named profile with dependency wiring + conflict errors. Low risk, no new algorithms.
3. **Self-improving harness** — baseline/champion store + `run.py --gate` + promote, so re-running as songs arrive auto-reports improve/regress (works at 0 songs).
4. **Phase 1.4 — HPSS vs Demucs-harmonic input A/B**, decided by measurement on the dataset.
5. **Phase 3 — Disagreement-aware dual model** — run madmom on all bars, arbitrate CREMA↔madmom disagreements with combined evidence. Sequenced last (highest regression risk).

### Stage 7 — Desktop app (Electron) — *planned, not started*
Per [`desktop-mvp-prd.md`](desktop-mvp-prd.md): a double-click `.dmg` that embeds the existing `web/` Next.js app in an Electron shell with bundled Python venvs, native binaries, and ML models — zero install, no terminal, all inference local. Phases: dev-machine prototype → packaged arm64 `.dmg` → universal binary (optional) → Windows (optional) → auto-update (optional). For non-technical testers/demos only; production stays "web + remote backend". *(No `desktop/` directory exists yet — this is design-only.)*

### Stage 8 — Cloud worker / remote backend — *future*
The eventual production target named in both PRDs: move the heavy Python worker off `localhost` to Modal / Replicate / a dedicated GPU box. The web UI's `web/lib/pipeline.ts` swap point and the desktop shell are both designed to host this version unchanged. Unlocks the deferred web-MVP open questions: sharable result URLs (needs auth + storage), concurrent jobs (real queue), per-stage timing, and pre-upload audio preview.

---

## How this roadmap is maintained

When a stage's scope changes as it's implemented, update this file in the same commit as the code so the roadmap never drifts. Each stage links to the doc that owns its detail — keep the *why* and milestone-level status there; keep this file at the one-paragraph-per-stage altitude.

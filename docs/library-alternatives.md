# Library alternatives — audio pipeline survey

A comparative reference for every library, service, and model that could replace or supplement the four phases of the current audio-tools pipeline. Each phase opens with the current choice and follows with a pros/cons table covering open-source and commercial alternatives. A dedicated section at the end covers music.ai's full module catalogue (per the user's request) and explains why DeepMind Lyria sits outside this comparison.

Verify any pricing or licensing detail with the vendor before committing — the field moves quickly.

---

## Current stack reference

| Phase | Library / model | Where it lives |
|---|---|---|
| Beat Detection | madmom RNN+DBN downbeat tracker (primary) → librosa `beat_track` (fallback); time-sig by autocorrelation of onset strength | `beat_stabilizer.py`, `chord_sheet.py` |
| Beat Stabilizer | `pyrubberband` wrapping the `rubberband` CLI (R3 engine), timemap-based warping | `beat_stabilizer.py` |
| Chord Detection | crema 602-class CRNN (primary) → madmom DeepChroma + CRF (low-confidence fallback) | `chord_chart_render.py`, `madmom_chord_detect.py` |
| Stem Splitting | Demucs v4 (`htdemucs_6s`/`htdemucs`/`htdemucs_ft`/`mdx_extra`) | `stem_splitter.py` |

---

## 1. Beat Detection

Finds beat positions, downbeats, tempo (BPM), and time signature.

| Library | License / Cost | Pros | Cons |
|---|---|---|---|
| **madmom** *(current primary)* | BSD-3 (plus a few NC-licensed extras) | Best-in-class downbeat tracking; emits beats + downbeats + meter in one pass; ~10 years of academic validation; works well across genres | Last upstream release in 2019; old NumPy/scipy pins; needs a dedicated venv; RNN inference is slow on CPU; community-maintained patches required for modern Python |
| **librosa.beat** *(current fallback)* | ISC | Pure Python, no native deps; well documented; fast on CPU | Beats only — no downbeats, no meter; tends to lock onto eighth- or half-time grids on rubato or sparse material; weak on swung 6/8 |
| **Essentia** | AGPL-3.0 or commercial dual-license | Industrial-grade C++ engine; `RhythmExtractor2013` and `BeatTrackerMultiFeature` often more robust than librosa; emits BPM histogram + beat positions + downbeats | AGPL is viral — incompatible with closed-source distribution unless you pay; build complexity (libav, fftw, taglib); less recent model research |
| **BeatNet** *(Heydari et al., 2021)* | MIT | Real-time online inference; particle filter on RNN activations; handles tempo changes within a track; downbeat-aware | Smaller community than madmom; pytorch-heavy; less battle-tested on edge genres |
| **Beat This!** *(Schlüter et al., 2024)* | MIT | Transformer-based — current SOTA on MIREX, GTZAN, Ballroom; markedly better on non-4/4 and within-track tempo modulation | Pytorch + GPU is realistic for production; community validation still ongoing; larger model footprint |
| **Aubio** | GPL-3 | Cross-platform C library + Python bindings; light; streaming-friendly; covers onsets + tempo + beats | WSOLA-era beat tracker, behind RNN-based methods; GPL-3 is viral; meter detection is weak |
| **Spotify Audio Analysis API** | Commercial, limited free tier | Track-level tempo + time signature + per-beat times instantly for any indexed track; no inference cost on your side | Only works for tracks Spotify has indexed — useless for user uploads; OAuth; rate limits; Spotify trimmed Audio Features access in late 2024 (deprecation risk) |
| **TempoCNN** *(Schreiber, 2018)* | AGPL | Single-shot tempo from a 12-second excerpt; fast; trained on ballroom + extended-genre data | Tempo only — no beats or downbeats; AGPL; little recent maintenance |
| **music.ai (Chords and Beat Mapping workflow)** | Commercial, per-minute | Production API; combined chord + beat output in one job; SLA available; offloads GPU from your infra | Output schema not published — per-beat times, downbeats, and time signature support is unverified without a sales call; per-minute cost; audio leaves your infra |

**Recommendation.** Keep madmom for now. Watch **Beat This!** — its non-4/4 behaviour is a clear future upgrade with a permissive license. Essentia is the strongest fallback if madmom's dependency pins become unworkable, *provided* your distribution model can absorb AGPL.

---

## 2. Beat Stabilizer (time-stretching to grid)

Warps audio so every detected beat lands on an even tempo grid.

| Library | License / Cost | Pros | Cons |
|---|---|---|---|
| **Rubber Band Library R3** *(current, via pyrubberband)* | GPL-2 core; commercial license available (~£600/yr) | Best-in-class free phase vocoder; great transient preservation; crispness presets (0–6); timemap mode is exactly what variable warping needs; used by Audacity, Ardour, others | GPL-2 is viral for closed-source distribution; CLI binary must be installed system-wide; some smearing on extreme stretch ratios |
| **librosa `effects.time_stretch`** | ISC | Pure Python, trivial install | Uniform stretch only — no timemap, can't anchor variable beats; phase-vocoder artefacts on drums; quality clearly below R3 |
| **SoundTouch (`soundstretch`)** | LGPL | Cross-platform C++; realtime-capable; fast; CLI binary or library | Quality below R3 on drums and vocals; transient artefacts; quiet upstream since ~2021 |
| **élastique Pro 3 / élastique SOLOIST** *(zplane)* | Commercial, per-seat / per-shipped-product | Industry-standard quality (used inside Ableton's Complex/Complex Pro, Cubase, FL Studio, Serato); fast; great transient preservation; formant control | Closed source; per-unit licensing; C SDK only — no Python wrapper out of the box |
| **DIRAC3** *(zplane)* | Commercial | Highest-quality offline mode in zplane's lineup; tuned for polyphonic + monophonic | Closed source; more expensive than élastique |
| **WORLD / pyworld** | BSD | Best for monophonic vocals; preserves formants | Polyphonic music sounds metallic / unnatural — wrong tool for full mixes |
| **pyTSMod** *(Driedger / Müller)* | MIT | Reference implementations of WSOLA, OLA, phase vocoder, HPS-TSM in pure Python; ideal for experimentation | Reference-quality, not optimised — slower than Rubber Band; quality below R3 on real music |
| **Stem-resync (custom)** | n/a | Split into stems → stretch each independently → re-mix. Quality ceiling above any single full-mix engine because settings can vary per stem | Adds Demucs runtime to the warp step; phase-coherence issues at the re-mix step; only worth it if you're already stem-splitting |

**Recommendation.** Rubber Band R3 is genuinely the strongest free option for full-mix warping — don't switch without a compelling reason. If you ever ship a commercial closed-source binary, you'll need either the commercial Rubber Band license or élastique. The stem-resync approach is the only quality ceiling above R3 short of going commercial — worth prototyping since your pipeline already produces stems.

**No commercial API offers full-mix time-stretching as a service.** music.ai, LALAL.AI, Moises, AudioShake — none of them sell a "warp to BPM" workflow. This phase will stay local for the foreseeable future.

---

## 3. Chord Detection

Per-beat chord recognition from a full mix.

| Library | License / Cost | Pros | Cons |
|---|---|---|---|
| **crema** *(current primary)* | ISC | 602-chord vocabulary covering extensions, slash chords, suspended; fast inference; clean librosa integration | Frame-by-frame model — no long-range context, so wobbles on harmonically ambiguous bars; training set skews Western pop/rock; not maintained since ~2018 |
| **madmom DeepChromaChordRecognition** *(current fallback)* | BSD-3 (mostly) | Bidirectional RNN over deep chroma + CRF — sees context both ways; stable on harmonically close confusions (Em7 vs G/B) | Same madmom maintenance baggage; small chord alphabet (24 maj/min by default) — loses 7ths, slash, sus |
| **Chordino (NNLS Chroma + Chordino Vamp plugin)** | GPL-2 | Long-time academic baseline; very stable on classic pop/rock/jazz; bass-line aware so slash chords work | GPL-2; Vamp host integration (sonic-annotator CLI or pyvamp) is awkward; pre-deep-learning features miss subtle qualities |
| **Essentia HPCP + ChordsDetection** | AGPL or commercial | Industrial-grade chroma; built-in key + chord detectors; consistent with the rest of an Essentia stack | AGPL; small alphabet (maj/min/dim/aug + 7); less accurate than crema on rich harmony |
| **BTC-ISMIR2019** *(Bidirectional Transformer for Chord recognition)* | MIT | Transformer-based; beat the prior SOTA on Billboard, Isophonics, RWC; large chord alphabet (maj/min/7/maj7/min7/dim/aug/sus2/sus4) | Research code — needs productionisation work; pytorch + GPU recommended; not pip-installable |
| **autochord** | MIT | Pure Python, librosa-based; dead-simple API; recent maintenance | Quality clearly below crema/madmom on complex material; small alphabet |
| **omnizart** | MIT (weights vary) | Full transcription suite (chord, beat, vocal, drum, piano-roll); single dependency; pretrained models | Heavy install (TF + weights); chord component solid but not SOTA; quiet since 2022 |
| **music.ai (Chords module / Chords and Beat Mapping workflow)** | Commercial, per-minute | Production-tuned for messy real-world audio; includes **bass detection + root key**; combined with beat output in one workflow; SLA available | Chord alphabet not published — needs a sales call; per-call cost; audio leaves your infra; vendor lock-in; can't tune |
| **Chordify API** | Commercial subscription | Excellent UX-tuned output (guitarist-friendly chord rationalisation); robust on user uploads | Per-call cost; data and IP leave your infra; opinionated output; no tuning |
| **Klangio API** | Commercial (per-minute) | Polyphonic transcription including chords + lead sheet; clean API; high quality on isolated instruments | Per-minute fees compound at scale; vendor lock-in; less validated on full-mix popular music than crema |
| **AnthemScore / Capo / Chord AI** *(consumer apps)* | Commercial (one-time or sub) | Best UX-tuned chord output; great for end-user products | No public API; not embeddable |

**Recommendation.** crema + madmom fallback is a reasonable open-source baseline. The clearest upgrade paths are:

- **BTC-ISMIR2019** if you can wrap research code (best accuracy on standard benchmarks).
- **Chordino** if you want to keep the alphabet small and prioritise stability over breadth.
- **music.ai Chords** if you're already paying them for stems and want consolidated billing.

---

## 4. Stem Splitting (source separation)

Isolates vocals, drums, bass, etc. from a full mix.

| Library / Service | License / Cost | Pros | Cons |
|---|---|---|---|
| **Demucs v4 / Hybrid Transformer Demucs** *(current)* | MIT | Strong all-around quality, especially bass + drums; the 6-stem `htdemucs_6s` (vocals, drums, bass, guitar, piano, other) is unique among free tools; well-maintained; CPU-runnable | Vocals not absolute SOTA in 2024+; slow on CPU (~5× realtime at `--shifts=1`); large weights (~1 GB for 6-stem) |
| **Spleeter** | MIT | First mainstream OSS separator; trivial install; 2/4/5-stem variants; fastest on CPU | Quality clearly below Demucs/MDX/BS-Roformer; TF1.x lineage causes setup pain; quiet since ~2021 |
| **Open-Unmix (umx / umxhq / umxl)** | MIT | Pure pytorch; small and fast; well-documented; MUSDB18-trained | Quality below Demucs on most material; vocals adequate, not great; 4 stems only |
| **MDX-Net (kuielab / mdx_extra)** | MIT | MDX Challenge 2021–22 winners; vocal isolation often cleaner than Demucs; already usable as a Demucs `--model` option | Per-stem models — multiple runs to assemble a full 4-stem set; non-vocal stems uneven |
| **BS-Roformer / Mel-Roformer** *(Lu et al., 2023; Kim et al., 2024)* | MIT (community ports) | Current SOTA on MUSDB18 and MDX23; used by Moises, MVSEP, UVR for their top results; vocals particularly strong | Pytorch + GPU for production use; community-trained checkpoints vary in quality; more DIY integration than Demucs |
| **Ultimate Vocal Remover (UVR5)** | MIT (GUI wraps MIT models) | Pre-bundles Demucs + MDX-Net + BS-Roformer in one tool; easy to A/B different models | GUI-first — backend automation means scripting the Python equivalents yourself |
| **music.ai (Musical Stems module)** | Commercial, per-minute | **8 stems**: vocals, bass, drums, guitars, strings, piano, keys, wind — wider than Demucs's 6; production-grade quality; SLA available; no GPU on your infra | Per-minute fee at scale (~$0.15/min per stem extracted from public examples); audio leaves your infra (privacy / IP concern); async job latency; can't tune; vendor lock-in |
| **Moises.ai API** *(consumer brand of music.ai)* | Commercial (~$0.05–$0.15/song) | Strong consumer brand; BS-Roformer-based; fast; 5–6 stems; clean API | Per-song cost; audio leaves your infra; vendor lock-in |
| **LALAL.AI API** | Commercial (~$10 / 90 min) | Wide stem set (vocals, drums, bass, electric guitar, acoustic guitar, piano, synth, voice/de-noise); fast; clean API | Per-minute cost; quality on guitar stems uneven; audio leaves your infra |
| **AudioShake API** | Commercial (enterprise pricing) | Used by Spotify Karaoke and Apple Music Sing for stem isolation; high quality; broader instrument coverage; legal-cleared training data | Enterprise contract terms; unfriendly for hobbyist or small-scale use |
| **iZotope RX Music Rebalance** | Commercial (~$400 RX Standard) | Surgical UI control; integrates with post-production workflows | Desktop GUI only — not embeddable; not for batch automation |
| **SpectraLayers Pro** *(Steinberg)* | Commercial (~$300) | Best UX for manual stem cleanup; great ARA2 integration | Desktop GUI; no API |
| **MVSEP** | Commercial (credits, $5–20/mo) | Hosted access to BS-Roformer, MDX, Demucs ensembles; results often beat single-model output | Per-credit cost; results-only — can't integrate the model itself |

**Recommendation.** Demucs is the right open-source default for your current shape — broad genre coverage and the only major free model with a 6-stem variant including guitar + piano. The clearest quality upgrade is **BS-Roformer for vocals specifically** (use it instead of Demucs's vocals stem; keep Demucs for the rest), at the cost of a multi-model orchestration layer.

If you go commercial, **music.ai Musical Stems is the strongest API choice** for breadth (8 stems including strings/keys/wind, which no free option offers). **AudioShake** is the right answer for enterprise / B2B contracts.

---

## music.ai — deep dive

The user specifically asked for a full audit of music.ai's offering. This section consolidates everything visible on their public site.

### Module catalogue (six public modules)

From [music.ai/modules](https://music.ai/modules/), all six modules and their official descriptions:

| # | Module | Section | Description (verbatim from page) | Pipeline-phase fit |
|---|---|---|---|---|
| 1 | **Music Identification (Magic)** | The Industry's Best Tools | "Quickly identify music tracks from the world's largest music catalog." | Adjacent — could pre-fill title/artist/key on uploads matching commercial releases |
| 2 | **Advanced Metadata (Cyanite)** | The Industry's Best Tools | "Get advanced metadata for efficient cataloging and content discovery." | Adjacent — could replace your "genre" dropdown with auto-detection |
| 3 | **Multitrack Mixing (Roex)** | The Industry's Best Tools | "AI-powered multitrack mixing to achieve professional results." | Adjacent — post-stem mixing, not in your current pipeline |
| 4 | **Musical Stems** | Top Modules | "Isolate vocals, bass, drums, guitars, strings, piano, keys, and wind from any audio file using AI algorithms." | **Phase 4 — Stem Splitting** |
| 5 | **Lyrics** | Top Modules | "Transcribe and align lyrics from any audio file, converting sung content into textual form. Optimized for singing." | Adjacent — could add lyric sheet output to the lead sheet PDF |
| 6 | **Chords** | Top Modules | "Transcribe chords and root key from audio, providing timeline of chord annotations in different classes and bass detection." | **Phase 3 — Chord Detection** |

### Workflow templates (curated public list)

From [music.ai/workflows](https://music.ai/workflows/):

| Workflow | Category | Underlying module(s) |
|---|---|---|
| Perform Advanced Stem Separation | Stem Separation and Enhancement | Musical Stems |
| Quickly Extract Audio Metadata | Metadata and Classification | Cyanite |
| Transcribe and Align Lyrics Precisely | Transcription and Alignment | Lyrics |
| **Chords and Beat Mapping** | Transcription and Alignment | Chords (emits chord events **+ BPM** in one job) |

The workflow page surfaces only the three above as "featured", but cross-links from the Chords-and-Beat-Mapping page reference at least two more templates: `Transcribe BPM and Beats` and `Transcribe Key and Chords of Songs`. Their standalone URLs 404 — they exist in the workflow builder but aren't surfaced as separate marketing pages.

### Pipeline-phase coverage map

| Phase | music.ai coverage | Confidence |
|---|---|---|
| 1. Beat Detection | **Yes** — included in the "Chords and Beat Mapping" workflow (BPM at minimum) | Confirmed for BPM; per-beat positions, downbeats, and time signature emit are **unverified** from public pages |
| 2. Beat Stabilizer | **No** — no time-stretching / warp-to-grid module in the public catalogue | Confirmed absent |
| 3. Chord Detection | **Yes** — Chords module with bass detection + root key | Confirmed |
| 4. Stem Splitting | **Yes** — Musical Stems with 8 stems (vocals, bass, drums, guitars, strings, piano, keys, wind) | Confirmed |

### Pricing (per [music.ai/pricing](https://music.ai/pricing/))

| Tier | Cost | Included | Notes |
|---|---|---|---|
| Pay-as-you-go | $0 base + per-minute per module | 2 concurrent jobs, 48h temporary storage | Free entry tier |
| Professional | $25 / month | $25 credit included; ~5% discount on per-minute rates; 10 concurrent jobs; 100 GB permanent storage | Suitable for small commercial use |
| Business / Enterprise | Custom | Business SLA, dedicated integration engineer, volume discount | Required for high throughput or contractual obligations |

**Sample per-minute rates** (pay-as-you-go → professional):
- AI music detection: **$0.03 → $0.0285**
- Drum stem separation: **$0.15 → $0.1425**
- Lyrics transcription: **$0.17 → $0.1615**
- Text-to-speech: **$0.00044 per character**

A 4-minute song with 4 stems extracted at pay-as-you-go runs ≈ **$2.40** in stem fees alone, plus chord/beat workflow on top.

### Open questions before adopting music.ai

These are not answerable from the public marketing pages — they require a sales/support conversation:

1. **Beat output granularity.** Does `Chords and Beat Mapping` emit per-beat timestamps + downbeat markers + time signature, or only a single track-level BPM? This is the gating question for replacing madmom.
2. **Chord alphabet.** Is the Chords module's vocabulary closer to crema's 602 classes or madmom's 24-class maj/min set? Does it include 7ths, slash chords, sus, dim7, m7b5?
3. **Output format.** JSON shape, MIDI tempo map availability, MusicXML support, CSV export.
4. **Time-stretch / warp-to-grid.** Has this been considered as a future module? They have all the inputs needed (beats + audio) but no public workflow for it.
5. **Data retention and training rights.** Per-tier audio retention policy and whether submitted audio can be used for model training.

### When music.ai makes sense

- Your product moves toward paid SaaS, and the engineering cost of maintaining four model pipelines (Demucs venv, crema venv, madmom venv, Rubber Band binary) outweighs per-minute fees.
- You need wider stem coverage than Demucs (strings, keys-vs-piano split, wind instruments).
- You want one vendor billing for stems + chords + lyrics + metadata.

### When to stay on the current stack

- Cost-sensitive volume — even at the pro tier, a 4-stem 4-minute song costs ≈ $2.40 vs. $0 marginal cost locally.
- Privacy / on-device / IP-sensitivity requirements — music.ai requires upload to their cloud.
- You want local control over beat detection and warping anyway, and partial coverage (paying music.ai for stems + chords but running beats + warping locally) means a hybrid orchestration layer for limited benefit.

---

## DeepMind Lyria — out of scope

[Lyria](https://deepmind.google/models/lyria/) (current: **Lyria 3**, with **Lyria RealTime** for streaming generation) is a **text-to-music generation** model. It produces audio from prompts — it does not analyse, separate, or warp existing audio.

| Pipeline phase | Lyria coverage |
|---|---|
| Beat Detection | None |
| Beat Stabilizer | None |
| Chord Detection | None |
| Stem Splitting | None |

Lyria's access surfaces (Gemini, YouTube Shorts Dream Track, Google Vids, ProducerAI, Google AI Studio, Vertex AI Studio) are all music-generation interfaces, not analysis APIs. Lyria's open-source companion **Magenta RealTime** is the same shape.

**The only way Lyria touches a pipeline like this one is the inverse direction** — generating reference backing tracks, intro/outro material, or full demo songs. That's a separate product, not an alternative to any of the four analysis phases.

---

## Cross-cutting recommendations

### Patterns worth borrowing from the commercial state of the art

- **Ensembling.** Every commercial service that beats single open-source models does so by running multiple models per stem and averaging. Demucs already supports `--shifts` (random-shift averaging within one model). True multi-model ensembling — e.g. Demucs + BS-Roformer voting per stem — is the highest-leverage quality win for an open-source stack.
- **Stem-then-detect.** Chord detection accuracy improves measurably when run on a harmonic-only stem (vocals + guitar + piano, drums and bass removed) rather than the full mix. Your pipeline already produces these stems — chaining `Demucs → harmonic stems mix → crema` is a free quality win.
- **Drum-stem beat tracking.** Beat detection on the drum stem alone is more stable than full-mix beat tracking for the same reason — fewer harmonic onsets to confuse the tracker.

### Licensing landmines

| License family | Examples in this survey | Practical impact |
|---|---|---|
| **AGPL-3.0** | Essentia, TempoCNN core | Viral over network — even hosted SaaS that wraps it must release source. The worst sticking point if you ever go closed-source commercial. |
| **GPL-2** | Aubio, Rubber Band core, Chordino | Source disclosure required for any distributed binary that links it. Hosted-only services are usually OK; desktop apps are not. |
| **MIT / BSD / ISC** | crema, librosa, Demucs, BeatNet, BS-Roformer community ports, music.ai's portable model outputs | The safe set. Most of the current pipeline is already here — that's the right call. |

### Rolled-up recommendation

Given the project's current shape (free open-source, self-hosted, no per-call costs), the strongest moves are:

1. **Swap Demucs vocals → BS-Roformer for vocals** while keeping Demucs for non-vocal stems. Largest quality win available without paying anyone.
2. **Watch Beat This!** for the next beat-tracker upgrade. Permissive license; better non-4/4 behaviour than madmom; not yet community-validated enough to swap today.
3. **Chain stems → chord detection** for free accuracy. The harmonic-only stem mix is much cleaner input than the full mix.
4. **Stay on Rubber Band R3** for warping — no commercial API sells this, and no free engine beats it.
5. **If the project pivots to paid SaaS**: evaluate **music.ai** as a 3-of-4 vendor (stems, chords, lyrics-as-a-bonus) and keep madmom + Rubber Band local. Get the open questions above answered first via a sales call.

---

## Sources

- music.ai modules page: https://music.ai/modules/
- music.ai workflows page: https://music.ai/workflows/
- music.ai pricing page: https://music.ai/pricing/
- music.ai Chords and Beat Mapping workflow: https://music.ai/workflows/transcription-and-alignment/chords-and-beat-mapping/
- DeepMind Lyria: https://deepmind.google/models/lyria/
- Vendor and library homepages linked in the tables above.

*Knowledge cutoff: January 2026. Pricing and model versions move fast — verify before depending on any number in this document.*

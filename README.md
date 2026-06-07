# audio-tools

End-to-end "session materials" pipeline for a song: drop in an audio file, get back a beat-stabilized WAV, a chord chart (PDF + MusicXML), isolated stems, an optional backing track, and an analysis JSON.

Three CLI tools that compose into one pipeline, plus a browser UI:

1. **Beat Stabilizer** — warps audio so every beat locks to a perfect rhythmic grid (like Ableton's "Warp to grid") with optional intro trim so it drops straight into a DAW at bar 1
2. **Chord Chart** — two-model chord detection (crema + madmom), section detection (MSAF, opt-in), renders both PDF and MusicXML so charts are editable in MuseScore / Sibelius
3. **Stem Splitter** — separates audio into up to 6 stems via Demucs, with energy-based "is this stem actually present" detection, and an optional **backing track** (all stems minus the one you'll play / sing)

The browser UI — **Session Materials Creator** — is the recommended way to use everything; it's a single-page Next.js app that runs locally and wraps the CLI pipeline.

---

## What's new

| Feature | Where |
|---|---|
| **Session Materials Creator** — Musiversal-branded web UI | `web/` |
| **Session-type backing track** — pick the instrument you'll play; the app mixes the other stems into a guide track | `stem_splitter.py --session-type`, `--backing-track-out` |
| **MusicXML export** — chord chart is editable in MuseScore, Sibelius, Dorico | `chord_chart_render.py` (always on when LilyPond run completes) |
| **Song-section detection** — A/B/C rehearsal marks on PDF + MusicXML (opt-in, via MSAF) | `chord_chart_render.py --detect-sections` / web advanced settings |
| **Stem presence detection** — every stem gets `present`, `rms_dbfs_peak`, `loud_seconds`; low-energy stems are flagged in UI and never offered as if they were real takes | `stem_splitter.py` (`stems_info.json` sidecar) |
| **Downbeat tracking + meter inference** — madmom's DBNDownBeatTrackingProcessor; correct bar phase without `--time-sig` for 3/4 and 6/8 | `beat_stabilizer.py`, `chord_chart_render.py` |
| **Tempo-change early stop** — pipeline aborts cleanly on multi-tempo songs instead of silently producing a mangled warp | `beat_stabilizer.py`, `--allow-tempo-change` to override |
| **PDF page-1 preview** — PyMuPDF renders a flat PNG so the chord chart shows inline on the done page (embedded PDF viewers are flaky across browsers) | `chord_chart_render.py`, surfaced via `/api/jobs/[id]/metadata` |

---

## Requirements

- macOS (Apple Silicon or Intel) or Linux
- Python 3.11 and Python 3.13+ (both needed — see why below)
- Node.js 20+ (for the web UI)
- [Homebrew](https://brew.sh) (macOS)

---

## Web UI (recommended)

```bash
bash setup.sh                # one-time: ffmpeg, rubberband, LilyPond, the three venvs
cd web && npm install        # one-time: web dependencies
npm run dev                  # then open http://localhost:3000
```

The UI shows a drop zone, runs analysis (BPM, key, meter) instantly on file drop, lets you tweak song info and advanced settings, then streams progress through stabilise → chord detect → stems. The done page shows an inline PDF preview, audio players for stabilised + each stem + backing track, and per-artifact download buttons plus a "download everything (ZIP)" CTA.

**Brand assets.** The brand fonts (Season Musiversal, Season Sans, Inter) ship inline as base64 `@font-face` blocks in `web/app/fonts.css` so the app works fully offline with zero external font dependencies.

**Limits.** 50 MB upload, ~6 min audio, single concurrent job per server, artifacts auto-deleted after 24 h.

**Storage.** Job artifacts (the uploaded audio, intermediate files, output ZIPs) are written to `$TMPDIR/audio-tools-jobs/` (e.g. `/var/folders/.../T/audio-tools-jobs` on macOS, `/tmp/audio-tools-jobs` on Linux). Set `AUDIO_TOOLS_JOBS_DIR=/custom/path` to override.

See [`docs/web-mvp-prd.md`](docs/web-mvp-prd.md) for design rationale and roadmap.

---

## Setup

Run once after cloning:

```bash
bash setup.sh
```

This installs:
- `rubberband` and `ffmpeg` via Homebrew
- LilyPond for PDF rendering
- Beat-stabilizer Python deps into the system Python
- `venv_crema/` (Python 3.11 + crema + TensorFlow 2.x + music21 + MSAF + PyMuPDF) for chord detection, MusicXML export, section detection, and PDF preview
- `venv_madmom/` (Python 3.11 + madmom, NumPy 1.26.4) for beat / downbeat detection and the optional chord fallback
- `venv_demucs/` (Python 3.11 + PyTorch + Demucs) for stem splitting and backing-track mixing

> **Why separate virtual environments?** `crema` needs TensorFlow 2.x (incompatible with Python 3.13+), `madmom` requires NumPy 1.x with Cython extensions, and Demucs needs PyTorch — they all conflict with each other and with the system Python. Each tool runs in its own isolated environment; `pipeline.py` wires them together automatically.

The brand fonts (Season Musiversal Sans, Season Sans SemiBold) referenced by the PDF chord chart's title and subtitle are looked up via `fontconfig`. They are not bundled — if absent on your system, LilyPond falls back to its default; the rest of the chart still renders correctly.

---

## Usage

### Full pipeline (recommended)

```bash
python3 pipeline.py -i song.wav
python3 pipeline.py -i song.wav --bpm 84 --title "My Song" --open
python3 pipeline.py -i song.wav --strength 0.8 --key "bes:major" --open
python3 pipeline.py -i song.wav --stems vocals,drums       # keep only two stems in ZIP
python3 pipeline.py -i song.wav --session-type bass        # also writes a backing track minus bass
python3 pipeline.py -i song.wav --detect-sections          # add A/B/C rehearsal marks
python3 pipeline.py -i song.wav --skip-stems               # skip stem splitting
python3 pipeline.py -i song.wav --no-trim-intro            # skip DAW-ready trim
python3 pipeline.py -i song.wav --allow-tempo-change       # process multi-tempo songs anyway
```

This runs all three steps in sequence. BPM, downbeat indices, and detected meter are passed between steps automatically via a `.bpm` sidecar file — no need to repeat them.

**Output files** (written to `~/Desktop/audio-tools-tests/<songname>/`):
- `song_stabilised.wav` — beat-locked audio, trimmed to start one bar before beat 1
- `song_stabilised.wav.bpm` — BPM + meter sidecar (used internally)
- `song_chord_chart.pdf` — the chord chart (Season Musiversal Sans title, Season Sans SemiBold subtitle)
- `song_chord_chart.musicxml` — editable lead sheet (MuseScore / Sibelius / Dorico)
- `song_chord_chart_preview.png` — page-1 thumbnail (rendered via PyMuPDF)
- `song_chord_chart.json` — analysis metadata (key, meter, sections, confidence stats)
- `song_stems/vocals.wav`, `drums.wav`, … — one WAV per stem
- `song_stems/stems_info.json` — per-stem `{present, rms_dbfs_peak, loud_seconds}`
- `song_backing_track.wav` — present when `--session-type` was set

### Beat stabilizer only

```bash
python3 beat_stabilizer.py -i song.wav -o song_stable.wav
python3 beat_stabilizer.py -i song.wav -o song_stable.wav --bpm 120
python3 beat_stabilizer.py -i song.wav -o song_stable.wav --bpm 98 --strength 0.8
python3 beat_stabilizer.py -i song.wav --detect-only   # just print BPM + meter, don't write
python3 beat_stabilizer.py -i song.wav -o out.wav --no-trim-intro
python3 beat_stabilizer.py -i song.wav -o out.wav --allow-tempo-change
```

| Flag | Description |
|------|-------------|
| `--bpm` | Target BPM. Auto-detected and rounded if omitted. |
| `--strength` | `1.0` = fully locked to grid, `0.0` = unchanged (default: `1.0`) |
| `--detect-only` | Print detected BPM + meter and exit without writing any file |
| `--no-trim-intro` | Disable the default intro trim (see below) |
| `--beats-per-bar` | Bar length used for the intro trim (default: auto-detected from downbeats) |
| `--allow-tempo-change` | Don't abort when a sustained tempo change is detected mid-song |

#### Intro trim (on by default)

After stabilisation, the output is trimmed so it starts **exactly one bar before the first detected beat**, using the detected downbeat phase. Drop the file into a DAW, set the project tempo, place the clip at bar 1 beat 1, everything lines up. Works for 3/4 and 6/8 because we use madmom's downbeat tracker, not a hard-coded 4-beat assumption. If the first beat is less than one bar from the start, silence is prepended.

#### Half-time auto-detection

When `--bpm` is supplied, the stabilizer compares the detected beat count against the expected count (`target_bpm / 60 × duration`). If the ratio is ≈ 2, it means the beat tracker locked onto 8th notes instead of quarter notes (common in half-time grooves). In that case, all 8th-note beats are kept as warp anchors (2× correction density) and mapped to the 8th-note grid at `target_bpm`, so the output plays at the correct quarter-note tempo without doubling the length.

#### Tempo-change early stop

By default, the stabilizer scans for sustained step-changes in BPM (≥ 6 BPM or ≥ 6 %, persisting for 4+ rolling windows). If one is found, it emits a structured `EARLY_STOP {"reason":"tempo_change", ...}` line and exits non-zero — single-tempo warping would mangle the audio across the boundary. Pass `--allow-tempo-change` to proceed anyway (accepting an audibly imperfect warp).

#### Beat detection

Uses **madmom's RNN + DBN downbeat tracker** (via `venv_madmom`). Significantly more accurate than librosa, especially for half-time grooves, expressive timing, and 3/4 / 6/8 material. librosa is the fallback when `venv_madmom` is not found, with a default `beats_per_bar=4` assumption.

Benchmark on a half-time groove (78 BPM):

| Detector | Anchors | CV | Mean error | p90 error | Max error |
|----------|---------|-----|-----------|-----------|-----------|
| Before stabilisation | — | 3.83% | 16.1 ms | 31.9 ms | 142 ms |
| librosa (quarter notes) | 259 | 1.89% | 9.5 ms | 20.2 ms | 78 ms |
| librosa (8th-note density) | 517 | 2.04% | 9.4 ms | 14.6 ms | 90 ms |
| **madmom (8th-note density)** | **528** | **0.92%** | **4.7 ms** | **10.8 ms** | **31 ms** |

### Chord chart only

```bash
./venv_crema/bin/python3.11 chord_chart_render.py -i song.wav
./venv_crema/bin/python3.11 chord_chart_render.py -i song.wav --title "My Song" --open
./venv_crema/bin/python3.11 chord_chart_render.py -i song.wav --key "f:minor" --bpm 84

# Enable the madmom fallback + key snapping + section detection for tricky songs:
./venv_crema/bin/python3.11 chord_chart_render.py -i song.wav --madmom-fallback --key-snap --detect-sections --open
```

**Basic flags**

| Flag | Description |
|------|-------------|
| `--title` | Chart title (default: filename); rendered in Season Musiversal Sans |
| `--bpm` | Override BPM. Auto-read from `.bpm` sidecar if present, otherwise detected. |
| `--key` | Key signature e.g. `f:minor`, `bes:major` (default: auto-detected) |
| `--time-sig` | Beats per bar e.g. `3`, `4` (default: auto-detected from downbeats) |
| `--bars-per-line` | How many bars per system (default: `4`) |
| `--no-bpm` | Hide BPM from subtitle |
| `--no-key` | Hide key from subtitle |
| `--no-meter` | Hide meter from subtitle |
| `--subtitle` | Override the entire subtitle line (use `""` to hide it) |
| `--detect-sections` | Run MSAF for A/B/C rehearsal marks; off by default (false positives on short songs) |
| `--open` | Open the PDF when done |

**Two-model chord detection**

Chord detection uses [crema](https://github.com/bmcfee/crema) as the primary model. For bars where crema's confidence is low, a secondary pass runs [madmom](https://github.com/CPJKU/madmom)'s bidirectional RNN + CRF chord recogniser.

| Flag | Default | Description |
|------|---------|-------------|
| `--no-madmom-fallback` | — | Disable the madmom fallback (it is **on by default**). |
| `--madmom-threshold` | `0.70` | Bars whose mean crema confidence falls below this are passed to madmom. |
| `--key-snap` | off | After all chord detection, snap any remaining non-diatonic chord in a low-confidence bar to the nearest diatonic equivalent. |
| `--key-snap-threshold` | `0.65` | Only bars below this mean confidence are eligible for key snapping. |
| `--add-7th` | off | Keep maj7, m7, and dominant 7 qualities; otherwise all chords are simplified to plain major or minor. |
| `--mid-bar-threshold` | `0.80` | Minimum crema confidence for a within-bar chord change to appear. Below this the bar keeps its beat-1 chord. |
| `--half-time` | off | Force every-other-beat selection. Auto-triggered when `--bpm` is ≈ half the detected rate. |
| `--compound` | off | Force 6/8 notation when beats-per-bar is 3 (most 6/8 songs are auto-detected). |

**Enharmonic spelling.** Chord roots are spelled to match the detected key: sharp keys (G, D, A, E, B, F#) use C#/F#/G# etc.; flat keys (F, B♭, E♭, A♭) use D♭/G♭/A♭ etc. The same spelling is applied in the PDF, MusicXML, and terminal output.

### Stem splitter only

```bash
./venv_demucs/bin/python3.11 stem_splitter.py -i song.wav
./venv_demucs/bin/python3.11 stem_splitter.py -i song.wav --stems vocals,drums
./venv_demucs/bin/python3.11 stem_splitter.py -i song.wav --model htdemucs
./venv_demucs/bin/python3.11 stem_splitter.py -i song.wav --session-type bass --backing-track-out backing.wav
```

| Flag | Description |
|------|-------------|
| `--stems` | Comma-separated stems to keep e.g. `vocals,drums` (default: all) |
| `--model` | Demucs model: `htdemucs_6s` (default, 6 stems), `htdemucs` (4 stems, faster), `htdemucs_ft` (fine-tuned), `mdx_extra` |
| `--output-dir` | Output folder (default: `<input>_stems/`) |
| `--session-type` | One of `vocals`, `guitar`, `bass`, `piano`, `other`; the stem to exclude from the backing track |
| `--backing-track-out` | Path to write the backing-track WAV (all stems minus `--session-type`, peak-normalised to −1 dBFS, 24-bit PCM) |
| `--progress-json` | Emit machine-readable `PROGRESS {…}` lines on stdout for the pipeline |

> **Note:** On first run, Demucs downloads model weights (~80–320 MB), cached afterwards.

Each stem also gets a presence assessment written to `stems_info.json`:

```json
{
  "vocals": { "present": true,  "rms_dbfs_peak": -15.7, "loud_seconds": 141.0 },
  "piano":  { "present": false, "rms_dbfs_peak": -41.6, "loud_seconds": 0.0   }
}
```

A stem is considered "present" when at least 2 s of consecutive 1-second windows exceed −30 dBFS RMS. Low-energy stems are still in the ZIP but flagged in the UI so users don't mistake bleed for a real take.

### Skip stabilization

If you already have a stable file and just want the chord chart:

```bash
python3 pipeline.py -i stable_song.wav --skip-stabilize --open
```

---

## Supported input formats

`wav`, `mp3`, `m4a`, `aiff`, `flac`, `ogg` — up to 50 MB / ~6 min in the web UI; no hard cap on the CLI side.

Output is always WAV (lossless) for stabilised audio and stems, PDF + MusicXML for the chord chart.

---

## Confidence thresholds

| Constant | Default | File | What it controls |
|----------|---------|------|-----------------|
| `CONFIDENCE_WARN` | `0.45` | `chord_sheet.py` | Segments flagged `?` in terminal; warning added to PDF if > 30% |
| `MID_BAR_THRESHOLD` | `0.80` | `chord_chart_render.py` | Minimum confidence for a within-bar chord split to appear |
| `MADMOM_THRESHOLD` | `0.70` | `chord_chart_render.py` | Bar mean confidence below which madmom re-evaluates |
| `KEY_SNAP_THRESHOLD` | `0.65` | `chord_chart_render.py` | Bar mean confidence below which key snapping applies |
| `PRESENCE_DB` | `-30.0` | `stem_splitter.py` | RMS threshold (dBFS) for a "loud" 1-second window |
| `PRESENCE_RUN_S` | `2.0` | `stem_splitter.py` | Minimum consecutive seconds above threshold for "present" |

All four chord-related thresholds can be overridden per-run via the matching CLI flag.

---

## File structure

```
audio-tools/
├── pipeline.py              # Full pipeline runner (all 3 steps + EARLY_STOP plumbing)
├── beat_stabilizer.py       # Beat/downbeat detection, time-warping, tempo-change guard
├── chord_sheet.py           # Chord detection & beat alignment (library)
├── chord_chart_render.py    # PDF + MusicXML + PNG preview renderer
├── madmom_chord_detect.py   # Standalone madmom chord chart generator
├── stem_splitter.py         # Stem separation + presence detection + backing-track mix
├── quick_analyze.py         # Fast BPM/key/meter probe used by the web UI's /api/analyze
├── requirements.txt         # System Python deps (beat stabilizer)
├── requirements_crema.txt   # crema venv deps (chord tools + music21 + MSAF + PyMuPDF)
├── requirements_madmom.txt  # madmom venv deps (beat/downbeat detection + secondary chord)
├── requirements_demucs.txt  # demucs venv deps (stem splitter)
├── setup.sh                 # One-time setup script
├── venv_crema/              # Auto-created by setup.sh — do not commit
├── venv_madmom/             # Auto-created by setup.sh — do not commit
├── venv_demucs/             # Auto-created by setup.sh — do not commit
└── web/                     # Session Materials Creator — Next.js wrapper
    ├── app/                 # App-router pages: upload, processing, done
    ├── components/          # DropZone, SongInfo, AdvancedSettings, …
    ├── lib/                 # Job state, pipeline spawn, zod schemas, ZIP helper
    ├── app/api/             # /api/jobs, /api/jobs/[id]/{file,zip,cancel,metadata}, /api/analyze
    └── app/fonts.css        # Embedded brand fonts (base64 @font-face)
```

---

## Libraries and tools

Every public/third-party dependency this project leans on, grouped by environment.

### System tools

| Tool | Why |
|------|-----|
| **[FFmpeg](https://ffmpeg.org/)** | Audio decoding for non-WAV inputs; pydub backend |
| **[Rubber Band](https://breakfastquay.com/rubberband/)** | Phase-vocoder time-stretching used by the beat stabilizer via pyrubberband |
| **[LilyPond](https://lilypond.org/)** ≥ 2.26 | Renders the chord chart PDF from generated `.ly` source |
| **[fontconfig](https://www.freedesktop.org/wiki/Software/fontconfig/)** | Lets LilyPond find the Season Musiversal Sans / Season Sans SemiBold brand fonts at render time |
| **[Homebrew](https://brew.sh/)** | macOS package manager (installs the three above) |

### System Python (beat stabilizer)

| Package | Role |
|---------|------|
| **[librosa](https://librosa.org/)** ≥ 0.10 | Audio loading; fallback beat tracker when madmom is unavailable |
| **[soundfile](https://python-soundfile.readthedocs.io/)** ≥ 0.12 | WAV I/O |
| **[pyrubberband](https://github.com/bmcfee/pyrubberband)** ≥ 0.3 | Python bindings for Rubber Band |
| **[pydub](https://github.com/jiaaro/pydub)** ≥ 0.25 | Format conversion for non-WAV inputs |
| **[NumPy](https://numpy.org/)** ≥ 1.24 | Array math |

### `venv_madmom` (beat detection + secondary chord detector)

| Package | Role |
|---------|------|
| **[madmom](https://github.com/CPJKU/madmom)** | RNN + DBN beat tracker, downbeat tracker, `DeepChromaChordRecognitionProcessor` chord fallback |
| **[Cython](https://cython.org/)** | Required at install time for madmom's compiled extensions |
| **NumPy** < 2.0 | Pinned — madmom's C extensions reference `np.int` / `np.float` removed in NumPy 2 |
| **[SciPy](https://scipy.org/)** | Numeric primitives used by madmom |
| **[mido](https://github.com/mido/mido)** | MIDI I/O used by madmom internals |
| **librosa** / **soundfile** / **pydub** | Shared audio helpers |

### `venv_crema` (chord detection, MusicXML, sections, preview)

| Package | Role |
|---------|------|
| **[crema](https://github.com/bmcfee/crema)** 0.2 | Primary chord-recognition CNN; 602-class output covering maj/min/7th/sus/dim/aug/half-dim |
| **[TensorFlow](https://www.tensorflow.org/)** 2.10–2.15 | crema model backend |
| **[Keras](https://keras.io/)** 2.x | High-level API for the crema model |
| **[scikit-learn](https://scikit-learn.org/)** < 1.6 | crema preprocessing utilities |
| **[music21](https://web.mit.edu/music21/)** ≥ 9.1 | MusicXML score construction (Phase 1 deliverable) |
| **[MSAF](https://pythonhosted.org/msaf/)** ≥ 0.1.80 | Music Structure Analysis Framework — section boundaries + label clustering for A/B/C rehearsal marks |
| **[PyMuPDF](https://pymupdf.readthedocs.io/)** ≥ 1.24 | Renders chord chart PDF page 1 to PNG for the done-page preview |
| **librosa** / **soundfile** / **pydub** / **NumPy** | Shared audio + math helpers |

### `venv_demucs` (stem splitting + backing track)

| Package | Role |
|---------|------|
| **[Demucs](https://github.com/adefossez/demucs)** 4.0.1 | Source separation: `htdemucs_6s` (6 stems), `htdemucs`, `htdemucs_ft`, `mdx_extra` |
| **[PyTorch](https://pytorch.org/)** | Neural network backend |
| **[torchaudio](https://pytorch.org/audio/)** | Audio I/O for Demucs + the backing-track mixer |
| **[torchcodec](https://github.com/pytorch/torchcodec)** | Codec layer used by torchaudio in PyTorch 2.x |

### Web app (`web/`)

**Runtime dependencies**

| Package | Role |
|---------|------|
| **[Next.js](https://nextjs.org/)** ^14.2 | App-router framework; everything except the Python subprocesses runs here |
| **[React](https://react.dev/)** ^18.3 | UI library |
| **[react-dropzone](https://react-dropzone.js.org/)** ^14.3 | File drop handling on the upload page |
| **[lucide-react](https://lucide.dev/)** ^1.16 | Icon set used across the UI |
| **[archiver](https://github.com/archiverjs/node-archiver)** ^7 | Streams the per-job ZIP download |
| **[uuid](https://github.com/uuidjs/uuid)** ^11 | Job IDs |
| **[zod](https://zod.dev/)** ^3.23 | Schema validation for the advanced-settings payload |

**Dev dependencies**

| Package | Role |
|---------|------|
| **[TypeScript](https://www.typescriptlang.org/)** ^5.7 | Static typing |
| **[Tailwind CSS](https://tailwindcss.com/)** ^3.4 | Utility-first styling; brand tokens defined in `tailwind.config.ts` |
| **[PostCSS](https://postcss.org/)** ^8.4 + **[autoprefixer](https://github.com/postcss/autoprefixer)** ^10.4 | CSS pipeline |
| **@types/** packages | Type definitions for archiver / node / react / react-dom / uuid |

### Brand fonts (UI + PDF chord chart)

- **Season Musiversal Sans** — display font (display headings + PDF chord chart title)
- **Season Sans** family — body, semibold, bold etc. (body copy + PDF chord chart subtitle)
- **[Inter](https://rsms.me/inter/)** — caption / micro UI text

Fonts ship as base64-embedded `@font-face` declarations in `web/app/fonts.css` (no network fetch). For LilyPond to use them in PDFs they must also be installed system-wide via fontconfig — otherwise the chart still renders, just in LilyPond's default font.

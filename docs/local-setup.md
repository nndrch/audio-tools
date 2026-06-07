# Local Development Setup

How to install everything you need to run the audio-tools web UI on your own machine. macOS Apple Silicon is the primary target; Intel macOS and Linux work with minor adjustments. Windows is not currently supported (use WSL2 if you must).

For a non-technical walkthrough with a single-file installer, see [`desktop-mvp-prd.md`](desktop-mvp-prd.md) — that's the future plan, not yet shipped. For now, follow this guide.

---

## TL;DR (if you already have Homebrew + Node)

```bash
git clone <repo> audio-tools
cd audio-tools
bash setup.sh          # ~10 min: installs system tools + 3 Python venvs
cd web
npm install            # ~1 min: web app deps
npm run dev            # opens http://localhost:3000
```

If anything fails, scroll to [Troubleshooting](#troubleshooting).

---

## What gets installed and why

The pipeline depends on three completely separate environments because the libraries it uses have incompatible Python and NumPy version requirements:

| Tool | Stack | Why isolated |
|---|---|---|
| Beat stabilization | system Python + `numpy`, `librosa`, `pyrubberband` | Works on any modern Python |
| Beat detection (madmom) | `venv_madmom` (Python 3.11 + NumPy < 2.0) | madmom's Cython extensions were compiled against the NumPy 1.x C API |
| Chord recognition (crema) | `venv_crema` (Python 3.11 + TensorFlow 2.x) | crema needs TF 2 which has its own NumPy / Keras pins |
| Stem splitting (Demucs) | `venv_demucs` (Python 3.11 + PyTorch CPU) | Demucs is a torch model and a torch CLI in one |

Plus three system binaries:

- **ffmpeg** — converts mp3/m4a/aiff to wav before processing
- **rubberband** — high-quality time-stretching for beat warping
- **lilypond** — typesets the chord chart PDF

Total disk usage after install: ~3.5 GB (mostly PyTorch + TensorFlow + Demucs model weights downloaded on first run).

---

## Prerequisites

### macOS

1. **Xcode Command Line Tools** — provides `git` and a system Python.
   ```bash
   xcode-select --install
   ```

2. **[Homebrew](https://brew.sh)** — package manager.
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

3. **Python 3.11** (Homebrew installs alongside system Python; both are needed).
   ```bash
   brew install python@3.11
   ```

4. **Node.js 20+** for the web UI.
   ```bash
   brew install node
   ```
   Verify: `node --version` should report ≥ v20.

### Linux (Ubuntu / Debian)

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip \
                    rubberband-cli ffmpeg lilypond \
                    build-essential nodejs npm git
```

Verify Node is ≥ v20 — Ubuntu's default repos sometimes ship older versions. If not, install from [NodeSource](https://github.com/nodesource/distributions).

---

## Step-by-step installation

```bash
# 1. Clone
git clone <repo-url> audio-tools
cd audio-tools

# 2. Run the setup script (handles system deps + all three venvs)
bash setup.sh
```

`setup.sh` runs six phases and prints `[N / 6]` markers. Expect ~10 min on a fast machine, longer on first run because PyTorch and TensorFlow wheels are large downloads.

```bash
# 3. Install web app dependencies
cd web
npm install
```

```bash
# 4. Start the dev server
npm run dev
```

Open <http://localhost:3000>. Drop a 30-second wav to test the full pipeline without waiting long.

---

## What `setup.sh` does

1. **System dependencies** — `brew install python@3.11 rubberband ffmpeg` on macOS, equivalent `apt` packages on Linux.
2. **Beat-stabilizer deps** — `pip install -r requirements.txt` into the system Python. Includes `numpy`, `librosa`, `soundfile`, `pyrubberband`, `pydub`.
3. **Crema venv** — creates `venv_crema/` with Python 3.11, installs `setuptools<70` first (crema's loader needs `pkg_resources`), then crema itself with TensorFlow 2.x.
4. **Madmom venv** — creates `venv_madmom/` with Python 3.11, installs `numpy>=1.20,<2.0` + `Cython` *first*, then madmom with `--no-build-isolation` so its setup.py can find them. On Apple Silicon `ARCHFLAGS="-arch arm64"` is set so the Cython extensions compile native.
5. **Demucs venv** — creates `venv_demucs/` with Python 3.11, installs PyTorch CPU + Demucs. Models download on first inference run, not here.
6. **LilyPond** — verifies it's installed; auto-installs via Homebrew on macOS if missing.

---

## Verifying the install

A direct sanity check that doesn't require the web UI:

```bash
# Detect BPM only (fastest end-to-end test of the madmom venv)
python3 beat_stabilizer.py -i path/to/test.wav --detect-only

# Full pipeline (slowest — exercises everything)
python3 pipeline.py -i path/to/test.wav --title "Test Song"
```

Both should print `PROGRESS {...}` lines if you pass `--progress-json`.

---

## Updating

```bash
git pull
bash setup.sh          # only re-runs what's missing; safe to re-run
cd web && npm install  # pulls any new web deps
```

`setup.sh` is idempotent — it won't reinstall things that are already in place. If you want a clean rebuild of a venv, delete it first (`rm -rf venv_madmom`) and re-run.

---

## Uninstalling

The footprint is contained — there is no global install. Remove the repo and the cached model weights:

```bash
cd ..
rm -rf audio-tools
rm -rf "$TMPDIR/audio-tools-jobs"          # job artifacts (macOS / Linux)
rm -rf ~/.cache/torch/hub/checkpoints      # Demucs model weights
```

Homebrew packages (ffmpeg, rubberband, lilypond, python@3.11) are general- purpose; remove only if you don't want them anymore: `brew uninstall <name>`.

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'numpy'` when running the pipeline

The web server is spawning the wrong Python. macOS has both Apple's `/usr/bin/python3` (where `setup.sh` puts the beat-stabilizer deps) and Homebrew's `python3` (which doesn't have them). [`web/lib/pipeline.ts`](../web/lib/pipeline.ts) auto-detects this, but if your setup is unusual, override:

```bash
AUDIO_TOOLS_PYTHON=/usr/bin/python3 npm run dev
```

### `madmom` install fails with `ModuleNotFoundError: No module named 'Cython'`

The fix is in current `setup.sh` (uses `--no-build-isolation`). If you're seeing this from an older clone, `git pull` and re-run `setup.sh`.

### `rubberband: command not found` during pipeline

Install it: `brew install rubberband` (macOS) or `sudo apt install rubberband-cli` (Linux).

### `lilypond` install hangs or fails on macOS

`brew install lilypond` is a large download (~150 MB) and includes its own Python. Let it finish; first run can take several minutes.

### Web UI shows "Waiting in queue…" and never starts

A previous job is stuck. The dev server has auto-recovery on restart — stop it (Ctrl+C in the `npm run dev` terminal) and restart. Stranded jobs in `$TMPDIR/audio-tools-jobs/` will be marked as error and the queue will clear.

### Demucs first run is slow

Demucs downloads model weights (~80 MB per model, ~250 MB for `htdemucs_6s`) on first inference. This happens once per machine, then cached in `~/.cache/torch/hub/checkpoints/`. Subsequent runs are fast.

### Apple Silicon: `madmom` compiled wrong arch

`setup.sh` sets `ARCHFLAGS="-arch arm64"` on Apple Silicon. If you migrated from an Intel Mac via Migration Assistant, delete `venv_madmom/` and re-run `setup.sh`.

---

## Where things live

| Path | What |
|---|---|
| `pipeline.py`, `beat_stabilizer.py`, … | Python pipeline scripts |
| `requirements*.txt` | Pinned deps for each venv |
| `venv_crema/`, `venv_madmom/`, `venv_demucs/` | Isolated Python envs (gitignored) |
| `web/` | Next.js app — UI + API routes that spawn the pipeline |
| `web/lib/pipeline.ts` | Node-side orchestrator (swap point for future cloud worker) |
| `$TMPDIR/audio-tools-jobs/` | Per-job uploads, logs, output ZIPs (auto-cleaned at 24 h) |
| `~/.cache/torch/hub/checkpoints/` | Demucs model weights |

---

## Next steps

- See [`web-mvp-prd.md`](web-mvp-prd.md) for the design of the current web app
- See [`desktop-mvp-prd.md`](desktop-mvp-prd.md) for the planned standalone desktop installer that removes all of this setup for non-technical users

#!/usr/bin/env bash
# setup.sh  —  One-time setup for audio-tools.
# Run once after cloning:  bash setup.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ARCH="$(uname -m)"   # arm64 | x86_64
OS="$OSTYPE"         # darwin* | linux-gnu

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║          audio-tools  setup              ║"
echo "╚══════════════════════════════════════════╝"
echo "  Platform: $OS  arch: $ARCH"

# ── 1. System dependencies ──────────────────────────────────
echo ""
echo "[ 1 / 7 ]  System dependencies"

if [[ "$OS" == "darwin"* ]]; then
    if ! command -v brew &>/dev/null; then
        echo "  ✗  Homebrew not found."
        echo "     Install it from https://brew.sh then re-run this script."
        exit 1
    fi
    echo "  Installing python@3.11, rubberband, ffmpeg via Homebrew …"
    brew install python@3.11 rubberband ffmpeg

    # On Apple Silicon, Homebrew lives in /opt/homebrew and ships arm64 wheels.
    # On Intel, it lives in /usr/local — its Python targets macOS 10.15 which
    # is too old for some pre-built wheels (e.g. llvmlite needs macosx_12_0).
    # Setting MACOSX_DEPLOYMENT_TARGET=12.0 makes pip find the correct wheels.
    if [[ "$ARCH" == "arm64" ]] && [[ -x "/opt/homebrew/bin/python3.11" ]]; then
        PYTHON311="/opt/homebrew/bin/python3.11"
    else
        PYTHON311="$(brew --prefix python@3.11)/bin/python3.11"
        export MACOSX_DEPLOYMENT_TARGET="12.0"
        echo "  Intel Mac detected — setting MACOSX_DEPLOYMENT_TARGET=12.0 for wheel compatibility"
    fi

else
    echo "  Linux ($ARCH) detected. Checking for required system packages …"
    MISSING=()
    command -v python3.11 &>/dev/null || MISSING+=("python3.11")
    command -v ffmpeg     &>/dev/null || MISSING+=("ffmpeg")
    # rubberband is 'rubberband-cli' on Debian/Ubuntu, 'rubberband' on others.
    command -v rubberband &>/dev/null || MISSING+=("rubberband-cli")
    if [ ${#MISSING[@]} -gt 0 ]; then
        echo "  Missing: ${MISSING[*]}"
        echo "  Install with:  sudo apt install ${MISSING[*]}"
        exit 1
    fi
    echo "  All system dependencies found."
    PYTHON311="python3.11"
fi

# ── 2. System Python — beat stabilizer deps ─────────────────
echo ""
echo "[ 2 / 7 ]  Beat stabilizer dependencies (system Python)"
python3 -m pip install -r requirements.txt --break-system-packages 2>/dev/null \
    || python3 -m pip install -r requirements.txt
echo "  ✓  Done"

# ── 3. Create crema venv (Python 3.11) ──────────────────────
echo ""
echo "[ 3 / 7 ]  Creating crema virtual environment (Python 3.11) …"
"$PYTHON311" -m venv venv_crema
./venv_crema/bin/pip install --upgrade pip --quiet
# setuptools<70 must come before crema to restore pkg_resources
./venv_crema/bin/pip install "setuptools<70" --quiet
./venv_crema/bin/pip install -r requirements_crema.txt
echo "  ✓  venv_crema ready"

# ── 4. Create madmom venv (Python 3.11) ─────────────────────
#
# madmom's Cython extensions must be compiled against NumPy <2.0 and
# require numpy + Cython to be present *before* madmom is installed
# (its setup.py imports numpy at build time).
#
# On Apple Silicon, ARCHFLAGS is set so the extensions compile for arm64.
# On Intel / Linux the variable is a no-op.
echo ""
echo "[ 4 / 7 ]  Creating madmom virtual environment (Python 3.11) …"
"$PYTHON311" -m venv venv_madmom
./venv_madmom/bin/pip install --upgrade pip --quiet

# setuptools<70 keeps pkg_resources around — madmom 0.16 imports it at the
# top of madmom/__init__.py, and newer setuptools drops it from the default
# install. Pin to the same constraint we already use in venv_crema.
./venv_madmom/bin/pip install "setuptools<70" --quiet

# Step 1: install build dependencies first
./venv_madmom/bin/pip install "numpy>=1.20,<2.0" Cython --quiet

# Step 2: install madmom (compiles Cython extensions against the numpy above).
# --no-build-isolation is required because madmom's setup.py imports numpy and
# Cython directly; without it, pip spins up a fresh build env that doesn't see
# the build deps we installed above and the build fails with ModuleNotFoundError.
if [[ "$(uname -m)" == "arm64" ]]; then
    echo "  Apple Silicon detected — setting ARCHFLAGS for arm64 …"
    ARCHFLAGS="-arch arm64" ./venv_madmom/bin/pip install --no-build-isolation madmom
else
    ./venv_madmom/bin/pip install --no-build-isolation madmom
fi

# Step 3: install remaining runtime deps
./venv_madmom/bin/pip install -r requirements_madmom.txt --quiet
echo "  ✓  venv_madmom ready"

# ── 5. Create demucs venv (Python 3.11) ─────────────────────
echo ""
echo "[ 5 / 7 ]  Creating demucs virtual environment (Python 3.11) …"
"$PYTHON311" -m venv venv_demucs
./venv_demucs/bin/pip install --upgrade pip --quiet
./venv_demucs/bin/pip install -r requirements_demucs.txt
echo "  ✓  venv_demucs ready"

# ── 6. Create allin1 venv (Python 3.11) ─────────────────────
#
# allin1 depends on madmom 0.16 whose source ships with two Python 3.10+
# incompatibilities: (a) `from collections import MutableSequence` (removed in
# 3.10 — must be collections.abc) and (b) deprecated `np.float` / `np.int`
# aliases (removed in NumPy 1.24).  We fix these with a perl one-liner after
# installing.
#
# allin1 also depends on natten for its DiNAT attention model.  natten ≥ 0.15
# dropped the natten1dav/natten2dav/natten1dqkrpb/natten2dqkrpb functions that
# allin1 1.1.0 imports.  We patch allin1's dinat.py to use the new na1d/na2d
# API instead.
echo ""
echo "[ 6 / 7 ]  Creating allin1 virtual environment (Python 3.11) …"
"$PYTHON311" -m venv venv_allin1
./venv_allin1/bin/pip install --upgrade pip --quiet
# Build deps first so madmom compiles against NumPy <2.0
./venv_allin1/bin/pip install "setuptools<70" "numpy>=1.20,<2.0" Cython --quiet
./venv_allin1/bin/pip install allin1

# Rebuild madmom from source (--no-build-isolation + --no-deps preserves the
# numpy pin) so Cython extensions are compiled against the correct NumPy ABI.
if [[ "$(uname -m)" == "arm64" ]]; then
    ARCHFLAGS="-arch arm64" ./venv_allin1/bin/pip install --no-build-isolation --no-deps --force-reinstall madmom
else
    ./venv_allin1/bin/pip install --no-build-isolation --no-deps --force-reinstall madmom
fi

# Patch 1: madmom Python 3.10+ / NumPy 1.24+ compatibility
find ./venv_allin1/lib/python3.11/site-packages/madmom/ -name "*.py" -exec perl -pi -e '
  s/\bnp\.float\b/np.float64/g;
  s/\bnp\.int\b/np.int64/g;
  s/\bnp\.complex\b/np.complex128/g;
  s/\bnp\.object\b/object/g;
  s/\bnp\.bool\b/np.bool_/g;
  s/from collections import MutableSequence/from collections.abc import MutableSequence/g;
  s/from collections import MutableMapping/from collections.abc import MutableMapping/g;
' {} \;

# Patch 2: allin1 dinat.py — update natten API (natten1dav → na1d, etc.)
DINAT="./venv_allin1/lib/python3.11/site-packages/allin1/models/dinat.py"
if [[ -f "$DINAT" ]] && grep -q "natten1dav" "$DINAT"; then
    python3 - "$DINAT" <<'PYEOF'
import sys, re, pathlib
p = pathlib.Path(sys.argv[1])
src = p.read_text()

# 1. Replace the import line
src = src.replace(
    "from natten.functional import natten1dav, natten1dqkrpb, natten2dav, natten2dqkrpb",
    "from natten.functional import na1d as _natten_na1d, na2d as _natten_na2d",
)

# 2. Replace the two-step forward computation with a single na1d/na2d call
src = re.sub(
    r'attention_scores = self\.nattendqkrpb\(query_layer, key_layer, self\.rpb, self\.kernel_size, self\.dilation\).*?context_layer = self\.nattendav\(attention_probs, value_layer, self\.kernel_size, self\.dilation\)',
    'context_layer = self._na_fn(query_layer, key_layer, value_layer, self.kernel_size, self.dilation, self.rpb)',
    src, flags=re.DOTALL,
)

# 3. Replace the function assignments in the 1D/2D class __init__ methods
src = src.replace(
    "self.nattendqkrpb = natten1dqkrpb\n    self.nattendav = natten1dav",
    "self._na_fn = lambda q, k, v, ks, d, rpb: _natten_na1d(q, k, v, ks, dilation=d, rpb=rpb, scale=1.0)",
)
src = src.replace(
    "self.nattendqkrpb = natten2dqkrpb\n    self.nattendav = natten2dav",
    "self._na_fn = lambda q, k, v, ks, d, rpb: _natten_na2d(q, k, v, ks, dilation=d, rpb=rpb, scale=1.0)",
)

p.write_text(src)
print("  Patched dinat.py")
PYEOF
fi

./venv_allin1/bin/python3.11 -c "import allin1" 2>/dev/null \
    && echo "  ✓  venv_allin1 ready (model weights download on first use)" \
    || echo "  ✗  venv_allin1 import check failed — check patches above"

# ── 7. Verify LilyPond ──────────────────────────────────────
echo ""
echo "[ 7 / 7 ]  Checking LilyPond …"
if ! command -v lilypond &>/dev/null; then
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "  Installing LilyPond via Homebrew …"
        brew install lilypond
    else
        echo "  ✗  LilyPond not found. Install with:  sudo apt install lilypond"
        exit 1
    fi
fi
echo "  ✓  LilyPond $(lilypond --version 2>&1 | head -1 | awk '{print $3}')"

# ── Done ────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  ✓  Setup complete!                      ║"
echo "║                                          ║"
echo "║  Full pipeline:                          ║"
echo "║    python3 pipeline.py -i song.wav       ║"
echo "║                                          ║"
echo "║  Individual tools:                       ║"
echo "║    python3 beat_stabilizer.py -i song.wav -o out.wav"
echo "║    ./venv_crema/bin/python3.11 chord_chart_render.py -i out.wav"
echo "║    ./venv_demucs/bin/python3.11 stem_splitter.py -i out.wav"
echo "╚══════════════════════════════════════════╝"
echo ""

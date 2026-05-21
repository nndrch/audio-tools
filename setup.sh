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
# allin1 uses natten for its DiNAT attention model.  natten >= 0.17 dropped MPS
# support and its Flex Attention backend requires head_dim to be a power-of-two
# (allin1 uses head_dim=12).  We replace dinat.py entirely with a pure-PyTorch
# neighborhood attention implementation that works on MPS, CPU, and CUDA.
echo ""
echo "[ 6 / 7 ]  Creating allin1 virtual environment (Python 3.11) …"
"$PYTHON311" -m venv venv_allin1
./venv_allin1/bin/pip install --upgrade pip --quiet
# Build deps first so madmom compiles against NumPy <2.0
./venv_allin1/bin/pip install "setuptools<70" "numpy>=1.20,<2.0" Cython --quiet
./venv_allin1/bin/pip install allin1
# torchaudio >= 2.11 made torchcodec a required runtime dep for audio I/O
./venv_allin1/bin/pip install torchcodec

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

# Patch 2: allin1 dinat.py — replace natten with pure-PyTorch neighborhood attention.
#
# natten >= 0.17 dropped MPS support and its Flex Attention backend requires
# head_dim to be a power-of-two (allin1 uses head_dim=12, which is not).
# We overwrite the file entirely with pure-PyTorch implementations that work
# on any device (MPS, CPU, CUDA) with any head_dim.
#
# Note: this patch targets a file inside venv_allin1 (not source-controlled).
# If you reinstall allin1 you must re-run setup.sh to reapply this patch.
DINAT="./venv_allin1/lib/python3.11/site-packages/allin1/models/dinat.py"
if [[ -f "$DINAT" ]]; then
    cat > "$DINAT" <<'DINATEOF'
"""This is a modification of:
  https://github.com/huggingface/transformers/blob/main/src/transformers/models/dinat/modeling_dinat.py
  so that it can provide both 1D and 2D attention.
"""

import math
import torch
from abc import ABC,  abstractmethod
from typing import Optional, Tuple, Callable
import torch.nn.functional as _F


# ---------------------------------------------------------------------------
# Pure-PyTorch neighborhood attention — replaces natten entirely.
#
# natten >= 0.17 dropped MPS support and Flex Attention requires head_dim to
# be a power-of-two (the allin1 model uses head_dim=12).  These hand-rolled
# implementations run on any device (MPS, CPU, CUDA) with any head_dim.
# ---------------------------------------------------------------------------

def _natten_na1d(q, k, v, kernel_size, **kw):
    """1-D neighborhood attention.  Layout: [B, T, heads, head_dim] (heads-last)."""
    scale = kw.get("scale", 1.0)
    d = kw.get("dilation", 1)
    ks = int(kernel_size[0] if hasattr(kernel_size, "__len__") else kernel_size)
    d  = int(d[0]           if hasattr(d,           "__len__") else d)
    half = ks // 2
    pad  = half * d

    B, T, nH, D = q.shape

    # Work in [B, nH, T, D] so we can pad the T dimension easily.
    q_ = q.permute(0, 2, 1, 3)          # [B, nH, T,      D]
    k_ = k.permute(0, 2, 1, 3)
    v_ = v.permute(0, 2, 1, 3)

    # Pad T on both sides with zeros for border handling.
    k_p = _F.pad(k_, (0, 0, pad, pad))  # [B, nH, T+2p,   D]
    v_p = _F.pad(v_, (0, 0, pad, pad))

    # Gather ks neighbours per query position -> [B, nH, T, ks, D]
    k_n = torch.stack(
        [k_p[:, :, pad + (j - half)*d : pad + (j - half)*d + T, :] for j in range(ks)],
        dim=3,
    )
    v_n = torch.stack(
        [v_p[:, :, pad + (j - half)*d : pad + (j - half)*d + T, :] for j in range(ks)],
        dim=3,
    )

    # Attention scores  [B, nH, T, ks]  then softmax
    scores = torch.einsum("bhtd,bhtkd->bhtk", q_ * scale, k_n)
    attn   = torch.softmax(scores, dim=-1)

    # Weighted sum of values  [B, nH, T, D]
    out = torch.einsum("bhtk,bhtkd->bhtd", attn, v_n)

    return out.permute(0, 2, 1, 3).contiguous()   # [B, T, nH, D]


def _natten_na2d(q, k, v, kernel_size, **kw):
    """2-D neighborhood attention.  Layout: [B, H, W, heads, head_dim] (heads-last)."""
    scale = kw.get("scale", 1.0)
    d = kw.get("dilation", 1)
    ks = int(kernel_size[0] if hasattr(kernel_size, "__len__") else kernel_size)
    d  = int(d[0]           if hasattr(d,           "__len__") else d)
    half = ks // 2
    pad  = half * d

    B, H_img, W_img, nH, D = q.shape

    # Work in [B, nH, H, W, D]
    q_ = q.permute(0, 3, 1, 2, 4)
    k_ = k.permute(0, 3, 1, 2, 4)
    v_ = v.permute(0, 3, 1, 2, 4)

    # Pad H and W:  F.pad args go right->left: (D_l,D_r, W_l,W_r, H_l,H_r)
    k_p = _F.pad(k_, (0, 0,  pad, pad,  pad, pad))
    v_p = _F.pad(v_, (0, 0,  pad, pad,  pad, pad))

    # Gather ks^2 neighbours  -> [B, nH, H, W, ks^2, D]
    k_n, v_n = [], []
    for i in range(ks):
        for j in range(ks):
            ho, wo = (i - half)*d, (j - half)*d
            k_n.append(k_p[:, :, pad+ho:pad+ho+H_img, pad+wo:pad+wo+W_img, :])
            v_n.append(v_p[:, :, pad+ho:pad+ho+H_img, pad+wo:pad+wo+W_img, :])
    k_n = torch.stack(k_n, dim=4)   # [B, nH, H, W, ks^2, D]
    v_n = torch.stack(v_n, dim=4)

    scores = torch.einsum("bnhwd,bnhwkd->bnhwk", q_ * scale, k_n)
    attn   = torch.softmax(scores, dim=-1)
    out    = torch.einsum("bnhwk,bnhwkd->bnhwd", attn, v_n)

    return out.permute(0, 2, 3, 1, 4).contiguous()   # [B, H, W, nH, D]
from ..config import Config
from .utils import *


# Copied from transformers.models.beit.modeling_beit.drop_path
def drop_path(input, drop_prob=0.0, training=False, scale_by_keep=True):
  """
  Drop paths (Stochastic Depth) per sample (when applied in main path of residual blocks).

  Comment by Ross Wightman: This is the same as the DropConnect impl I created for EfficientNet, etc networks,
  however, the original name is misleading as 'Drop Connect' is a different form of dropout in a separate paper...
  See discussion: https://github.com/tensorflow/tpu/issues/494#issuecomment-532968956 ... I've opted for changing the
  layer and argument names to 'drop path' rather than mix DropConnect as a layer name and use 'survival rate' as the
  argument.
  """
  if drop_prob == 0.0 or not training:
    return input
  keep_prob = 1 - drop_prob
  shape = (input.shape[0],) + (1,) * (input.ndim - 1)  # work with diff dim tensors, not just 2D ConvNets
  random_tensor = keep_prob + torch.rand(shape, dtype=input.dtype, device=input.device)
  random_tensor.floor_()  # binarize
  output = input.div(keep_prob) * random_tensor
  return output


# Copied from transformers.models.beit.modeling_beit.BeitDropPath with Beit->Dinat
class DinatDropPath(nn.Module):
  """Drop paths (Stochastic Depth) per sample (when applied in main path of residual blocks)."""

  def __init__(self, drop_prob: Optional[float] = None) -> None:
    super().__init__()
    self.drop_prob = drop_prob

  def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
    return drop_path(hidden_states, self.drop_prob, self.training)

  def extra_repr(self) -> str:
    return "p={}".format(self.drop_prob)


class _NeighborhoodAttentionNd(ABC, nn.Module):
  # rpb is learnable relative positional biases; same concept is used Swin.
  rpb: nn.Parameter
  _na_fn: Callable

  def __init__(
    self,
    cfg: Config,
    dim: int,
    num_heads: int,
    kernel_size: int,
    dilation: int
  ):
    super().__init__()
    if dim % num_heads != 0:
      raise ValueError(
        f"The hidden size ({dim}) is not a multiple of the number of attention heads ({num_heads})"
      )

    self.num_attention_heads = num_heads
    self.attention_head_size = int(dim / num_heads)
    self.all_head_size = self.num_attention_heads * self.attention_head_size
    self.kernel_size = kernel_size
    self.dilation = dilation

    self.query = nn.Linear(self.all_head_size, self.all_head_size, bias=cfg.qkv_bias)
    self.key = nn.Linear(self.all_head_size, self.all_head_size, bias=cfg.qkv_bias)
    self.value = nn.Linear(self.all_head_size, self.all_head_size, bias=cfg.qkv_bias)

    self.dropout = nn.Dropout(cfg.drop_attention)

  def forward(
    self,
    hidden_states: torch.Tensor,
    output_attentions: Optional[bool] = False,
  ) -> Tuple[torch.Tensor]:
    query_layer = self.transpose_for_scores(self.query(hidden_states))
    key_layer = self.transpose_for_scores(self.key(hidden_states))
    value_layer = self.transpose_for_scores(self.value(hidden_states))

    # Apply the scale factor before computing attention weights. It's usually more efficient because
    # attention weights are typically a bigger tensor compared to query.
    # It gives identical results because scalars are commutable in matrix multiplication.
    query_layer = query_layer / math.sqrt(self.attention_head_size)

    # Compute neighborhood attention in one call.
    # Pure-PyTorch implementations use heads-last layout [batch, ..., heads, head_dim], so no
    # permutation is needed — transpose_for_scores already produces that layout.
    # scale=1.0 prevents re-scaling the already-scaled query.
    context_layer = self._na_fn(query_layer, key_layer, value_layer,
                                self.kernel_size, self.dilation, self.rpb)
    # context_layer is heads-last; merge heads back into channels.
    new_context_layer_shape = context_layer.size()[:-2] + (self.all_head_size,)
    context_layer = context_layer.reshape(new_context_layer_shape)

    # output_attentions is always False during inference; attention weights are not returned.
    outputs = (context_layer,)

    return outputs

  def transpose_for_scores(self, x):
    # Pure-PyTorch na1d/na2d use heads-last layout [batch, ..., heads, head_dim].
    # The view already produces that shape — no permutation needed.
    new_x_shape = x.size()[:-1] + (self.num_attention_heads, self.attention_head_size)
    return x.view(new_x_shape)


class NeighborhoodAttention1d(_NeighborhoodAttentionNd):
  def __init__(
    self,
    cfg: Config,
    dim: int,
    num_heads: int,
    kernel_size: int,
    dilation: int
  ):
    super().__init__(cfg, dim, num_heads, kernel_size, dilation)
    self.rpb = nn.Parameter(
      torch.zeros(num_heads, (2 * self.kernel_size - 1)),
      requires_grad=True,
    )
    self._na_fn = lambda q, k, v, ks, d, rpb: _natten_na1d(q, k, v, ks, dilation=d, scale=1.0)


class NeighborhoodAttention2d(_NeighborhoodAttentionNd):
  def __init__(
    self,
    cfg: Config,
    dim: int,
    num_heads: int,
    kernel_size: int,
    dilation: int
  ):
    super().__init__(cfg, dim, num_heads, kernel_size, dilation)
    self.rpb = nn.Parameter(
      torch.zeros(num_heads, (2 * self.kernel_size - 1), (2 * self.kernel_size - 1)),
      requires_grad=True,
    )
    self._na_fn = lambda q, k, v, ks, d, rpb: _natten_na2d(q, k, v, ks, dilation=d, scale=1.0)


# Copied from transformers.models.nat.modeling_nat.NeighborhoodAttentionOutput
class NeighborhoodAttentionOutput(nn.Module):
  def __init__(self, config: Config, dim: int):
    super().__init__()
    self.dense = nn.Linear(dim, dim)
    self.dropout = nn.Dropout(config.drop_attention)

  def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
    hidden_states = self.dense(hidden_states)
    hidden_states = self.dropout(hidden_states)

    return hidden_states


class _NeighborhoodAttentionModuleNd(ABC, nn.Module):
  self: _NeighborhoodAttentionNd

  def __init__(self, cfg: Config, dim: int):
    super().__init__()
    # self.self = _NeighborhoodAttentionNd(config, dim, num_heads, kernel_size, dilation)
    self.output = NeighborhoodAttentionOutput(cfg, dim)

  def forward(
    self,
    hidden_states: torch.Tensor,
    output_attentions: Optional[bool] = False,
  ) -> Tuple[torch.Tensor]:
    self_outputs = self.self(hidden_states, output_attentions)
    attention_output = self.output(self_outputs[0])
    outputs = (attention_output,) + self_outputs[1:]  # add attentions if we output them
    return outputs


class NeighborhoodAttentionModule1d(_NeighborhoodAttentionModuleNd):
  def __init__(self, cfg: Config, dim: int, num_heads: int, kernel_size: int, dilation: int):
    super().__init__(cfg, dim)
    self.self = NeighborhoodAttention1d(cfg, dim, num_heads, kernel_size, dilation)


class NeighborhoodAttentionModule2d(_NeighborhoodAttentionModuleNd):
  def __init__(self, cfg: Config, dim: int, num_heads: int, kernel_size: int, dilation: int):
    super().__init__(cfg, dim)
    self.self = NeighborhoodAttention2d(cfg, dim, num_heads, kernel_size, dilation)


# Copied from transformers.models.nat.modeling_nat.NatIntermediate with Nat->Dinat
class DinatIntermediate(nn.Module):
  def __init__(self, config: Config, dim_in: int, dim_out: int):
    super().__init__()
    self.dense = nn.Linear(dim_in, dim_out)
    if isinstance(config.act_transformer, str):
      self.intermediate_act_fn = get_activation_function(config.act_transformer)
    else:
      self.intermediate_act_fn = config.act_transformer

  def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
    hidden_states = self.dense(hidden_states)
    hidden_states = self.intermediate_act_fn(hidden_states)
    return hidden_states


# Copied from transformers.models.nat.modeling_nat.NatOutput with Nat->Dinat
class DinatOutput(nn.Module):
  def __init__(self, config: Config, dim_in: int, dim_out: int):
    super().__init__()
    self.dense = nn.Linear(dim_in, dim_out)
    self.dropout = nn.Dropout(config.drop_hidden)

  def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
    hidden_states = self.dense(hidden_states)
    hidden_states = self.dropout(hidden_states)
    return hidden_states


class _DinatLayerNd(ABC, nn.Module):
  attention: _NeighborhoodAttentionModuleNd
  attention2: Optional[_NeighborhoodAttentionModuleNd]

  def __init__(
    self,
    cfg: Config,
    dim: int,
    kernel_size: int,
    dilation: int,
    drop_path_rate: float,
    double_attention: bool,
  ):
    super().__init__()
    self.double_attention = double_attention
    self.kernel_size = kernel_size
    self.dilation = dilation
    self.window_size = self.kernel_size * self.dilation
    if double_attention:
      self.window_size *= 2
    self.layernorm_before = nn.LayerNorm(dim, eps=cfg.layer_norm_eps)
    self.drop_path = DinatDropPath(drop_path_rate) if drop_path_rate > 0.0 else nn.Identity()
    dim_after = dim * 2 if double_attention else dim
    self.layernorm_after = nn.LayerNorm(dim_after, eps=cfg.layer_norm_eps)
    self.intermediate = DinatIntermediate(cfg, dim_after, int(dim_after * cfg.mlp_ratio))
    self.output = DinatOutput(cfg, int(dim_after * cfg.mlp_ratio), dim)

  @abstractmethod
  def maybe_pad(self, *args, **kwargs):
    raise NotImplementedError

  def forward(
    self,
    hidden_states: torch.Tensor,
    output_attentions: Optional[bool] = False,
  ) -> Tuple[torch.Tensor, torch.Tensor]:
    if len(hidden_states.shape) > 3:
      is_2d = True
      N, K, T, C = hidden_states.size()
    else:
      is_2d = False
      N, T, C = hidden_states.shape
    shortcut = hidden_states

    hidden_states = self.layernorm_before(hidden_states)
    # pad hidden_states if they are smaller than kernel size x dilation
    if is_2d:
      hidden_states, pad_values = self.maybe_pad(hidden_states, K, T)
      _, height_pad, width_pad, _ = hidden_states.shape
    else:
      hidden_states, pad_values = self.maybe_pad(hidden_states, T)

    attention_inputs = hidden_states
    hidden_states_list = []
    for attention in [self.attention, self.attention2]:
      if attention is None:
        continue

      attention_output = attention(attention_inputs, output_attentions=output_attentions)
      attention_output = attention_output[0]

      if is_2d:
        was_padded = pad_values[3] > 0 or pad_values[5] > 0
        if was_padded:
          attention_output = attention_output[:, :K, :T, :].contiguous()
      else:
        was_padded = pad_values[3] > 0
        if was_padded:
          attention_output = attention_output[:, :T, :].contiguous()

      hidden_states = shortcut + self.drop_path(attention_output)
      hidden_states_list.append(hidden_states)

    if self.double_attention:
      hidden_states = torch.cat(hidden_states_list, dim=-1)
      shortcut = torch.stack(hidden_states_list).sum(dim=0) / 2.
    else:
      shortcut = hidden_states
    layer_output = self.layernorm_after(hidden_states)
    layer_output = self.output(self.intermediate(layer_output))

    layer_output = shortcut + self.drop_path(layer_output)

    # layer_outputs = (layer_output, attention_outputs[1]) if output_attentions else (layer_output,)
    layer_outputs = (layer_output,)
    return layer_outputs


class DinatLayer1d(_DinatLayerNd):
  def __init__(
    self,
    cfg: Config,
    dim: int,
    num_heads: int,
    kernel_size: int,
    dilation: int,
    drop_path_rate: float,
    double_attention: bool,
  ):
    super().__init__(cfg, dim, kernel_size, dilation, drop_path_rate, double_attention)
    self.attention = NeighborhoodAttentionModule1d(cfg, dim, num_heads, kernel_size, dilation)
    if double_attention:
      self.attention2 = NeighborhoodAttentionModule1d(cfg, dim, num_heads, kernel_size, dilation * 2)
    else:
      self.attention2 = None

  def maybe_pad(self, hidden_states, frames):
    window_size = self.window_size
    pad_values = (0, 0, 0, 0)
    if frames < window_size:
      pad_l = 0
      pad_r = max(0, window_size - frames)
      pad_values = (0, 0, pad_l, pad_r)
      hidden_states = nn.functional.pad(hidden_states, pad_values)
    return hidden_states, pad_values


class DinatLayer2d(_DinatLayerNd):
  def __init__(
    self,
    cfg: Config,
    dim: int,
    num_heads: int,
    kernel_size: int,
    dilation: int,
    drop_path_rate: float
  ):
    super().__init__(cfg, dim, kernel_size, dilation, drop_path_rate, double_attention=False)
    self.attention = NeighborhoodAttentionModule2d(cfg, dim, num_heads, kernel_size, dilation)
    self.attention2 = None

  def maybe_pad(self, hidden_states, height, width):
    window_size = self.window_size
    pad_values = (0, 0, 0, 0, 0, 0)
    if height < window_size or width < window_size:
      pad_l = pad_t = 0
      pad_r = max(0, window_size - width)
      pad_b = max(0, window_size - height)
      pad_values = (0, 0, pad_l, pad_r, pad_t, pad_b)
      hidden_states = nn.functional.pad(hidden_states, pad_values)
    return hidden_states, pad_values
DINATEOF
    echo "  Patched dinat.py (pure-PyTorch neighborhood attention, no natten dependency)"
fi

# Pre-download the 8 allin1 model weights (~11 MB total) from HuggingFace.
# This avoids on-demand downloads during a live job, which fail on servers
# that cannot reach huggingface.co.  Set HF_TOKEN for higher rate limits.
echo "  Downloading allin1 model weights …"
./venv_allin1/bin/python3.11 download_allin1_models.py \
    || echo "  ⚠  Model download failed. Run download_allin1_models.py manually"
echo "      or rsync from a Mac where the models are already cached."
echo "      See download_allin1_models.py --help for instructions."

./venv_allin1/bin/python3.11 -c "import allin1" 2>/dev/null \
    && echo "  ✓  venv_allin1 ready" \
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

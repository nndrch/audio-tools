#!/usr/bin/env python3
"""
download_allin1_models.py  —  Pre-download the allin1 model weights from HuggingFace.

Usage (inside venv_allin1):
    ./venv_allin1/bin/python3.11 download_allin1_models.py

If the server cannot reach huggingface.co, download the models on your local
Mac and rsync the cache directory to the server instead (see --help output).

Environment variables:
    HF_TOKEN   — HuggingFace access token (avoids unauthenticated rate limits).
                 Create one at https://huggingface.co/settings/tokens
                 (a read-only token is enough).
"""

from __future__ import annotations

import os
import sys
import time
import argparse

REPO_ID = "taejunkim/allinone"
FILENAMES = [
    "harmonix-fold0-0vra4ys2.pth",
    "harmonix-fold1-3ozjhtsj.pth",
    "harmonix-fold2-gmgo0nsy.pth",
    "harmonix-fold3-i92b7m8p.pth",
    "harmonix-fold4-1bql5qo0.pth",
    "harmonix-fold5-x4z5zeef.pth",
    "harmonix-fold6-x7t226rq.pth",
    "harmonix-fold7-qwwskhg6.pth",
]


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--cache-dir",
        default=None,
        help="Override the HuggingFace cache directory (default: ~/.cache/huggingface/hub)",
    )
    p.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of download attempts per file (default: 3)",
    )
    args = p.parse_args()

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("ERROR: huggingface_hub is not installed in this Python environment.")
        print("       Run:  pip install huggingface_hub")
        sys.exit(1)

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if not token:
        print("NOTE: No HF_TOKEN environment variable set.")
        print("      Unauthenticated downloads may hit rate limits.")
        print("      Set one with:  export HF_TOKEN=hf_...")
        print()

    print(f"Downloading {len(FILENAMES)} model weights from {REPO_ID} …")
    print()

    failed = []
    for i, filename in enumerate(FILENAMES, 1):
        for attempt in range(1, args.retries + 1):
            try:
                path = hf_hub_download(
                    repo_id=REPO_ID,
                    filename=filename,
                    cache_dir=args.cache_dir,
                    token=token,
                )
                print(f"  [{i}/{len(FILENAMES)}]  ✓  {filename}")
                print(f"            → {path}")
                break
            except Exception as e:
                if attempt < args.retries:
                    wait = 2 ** attempt
                    print(f"  [{i}/{len(FILENAMES)}]  attempt {attempt} failed: {e}")
                    print(f"            retrying in {wait}s …")
                    time.sleep(wait)
                else:
                    print(f"  [{i}/{len(FILENAMES)}]  ✗  {filename}: {e}")
                    failed.append(filename)
        print()

    if failed:
        print("=" * 60)
        print(f"FAILED to download {len(failed)} file(s):")
        for f in failed:
            print(f"  • {f}")
        print()
        print("This usually means the server cannot reach huggingface.co.")
        print()
        print("MANUAL FALLBACK — copy models from your local Mac:")
        print()
        print("  On your Mac, the models are already cached at:")
        print("    ~/.cache/huggingface/hub/models--taejunkim--allinone/")
        print()
        print("  Copy that directory to the server:")
        print("    rsync -avz --progress \\")
        print("      ~/.cache/huggingface/hub/models--taejunkim--allinone/ \\")
        print("      user@server:~/.cache/huggingface/hub/models--taejunkim--allinone/")
        print()
        print("  Then run this script once more — it will detect the cached")
        print("  files and skip the network download.")
        print("=" * 60)
        sys.exit(1)
    else:
        print(f"All {len(FILENAMES)} model weights downloaded successfully.")
        print()
        print("allin1 will now work offline on this machine.")
        print("(HF_HUB_OFFLINE=1 is set in run_allin1.py to avoid API calls.)")


if __name__ == "__main__":
    main()

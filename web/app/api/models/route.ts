import { NextResponse } from "next/server";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type ModelStatus = {
  id: string;
  label: string;
  present: boolean;
  note?: string;
};

async function fileExists(p: string): Promise<boolean> {
  try {
    await fs.access(p);
    return true;
  } catch {
    return false;
  }
}

async function checkAllin1(): Promise<ModelStatus> {
  const cacheDir = path.join(
    os.homedir(),
    ".cache", "huggingface", "hub",
    "models--taejunkim--allinone",
  );
  // There should be exactly 8 harmonix-fold*.pth blobs. We detect presence by
  // finding the snapshots directory and counting symlink targets inside it.
  try {
    const snapDir = path.join(cacheDir, "snapshots");
    const hashes = await fs.readdir(snapDir);
    if (hashes.length === 0) throw new Error("empty");
    const snapshotDir = path.join(snapDir, hashes[0]);
    const entries = await fs.readdir(snapshotDir);
    const pths = entries.filter((e) => e.endsWith(".pth"));
    if (pths.length >= 8) {
      return { id: "allin1", label: "Song structure model (allin1)", present: true };
    }
    return {
      id: "allin1",
      label: "Song structure model (allin1)",
      present: false,
      note: `${pths.length}/8 weight files found`,
    };
  } catch {
    return {
      id: "allin1",
      label: "Song structure model (allin1)",
      present: false,
      note: 'Run: ./venv_allin1/bin/python3.11 download_allin1_models.py',
    };
  }
}

async function checkDemucs(modelId: string, filename: string, label: string): Promise<ModelStatus> {
  const cachePath = path.join(
    os.homedir(),
    ".cache", "torch", "hub", "checkpoints",
    filename,
  );
  const present = await fileExists(cachePath);
  return {
    id: modelId,
    label,
    present,
    note: present ? undefined : "Downloads automatically on first stem-split job",
  };
}

export async function GET() {
  const [allin1, htdemucs6s, htdemucs] = await Promise.all([
    checkAllin1(),
    checkDemucs("htdemucs_6s", "5c90dfd2-34c22ccb.th", "Stem model: htdemucs_6s (6 stems, default)"),
    checkDemucs("htdemucs",    "955717e8-8726e21a.th",  "Stem model: htdemucs (4 stems)"),
  ]);

  const models = [allin1, htdemucs6s, htdemucs];
  const allPresent = models.every((m) => m.present);

  return NextResponse.json({ models, allPresent });
}

import { NextResponse } from "next/server";
import fs from "node:fs/promises";
import path from "node:path";
import { v4 as uuid } from "uuid";

import {
  MAX_DURATION_SECONDS,
  SettingsSchema,
  sanitizeFilename,
  validateUpload,
} from "@/lib/validation";
import {
  createJob,
  ensureJobsRoot,
  jobDir,
} from "@/lib/jobs";
import { probeDurationSeconds } from "@/lib/ffprobe";
import { startOrQueue } from "@/lib/pipeline";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  await ensureJobsRoot();

  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return NextResponse.json({ error: "Could not parse upload." }, { status: 400 });
  }

  const file = form.get("file");
  if (!(file instanceof File)) {
    return NextResponse.json({ error: "Missing 'file' field." }, { status: 400 });
  }

  const upload = validateUpload({ name: file.name, size: file.size });
  if (!upload.ok) {
    return NextResponse.json({ error: upload.message }, { status: upload.status });
  }

  const settingsRaw = form.get("settings");
  let settings = {};
  if (typeof settingsRaw === "string" && settingsRaw.trim()) {
    try {
      settings = JSON.parse(settingsRaw);
    } catch {
      return NextResponse.json({ error: "Invalid settings JSON." }, { status: 400 });
    }
  }
  const parsed = SettingsSchema.safeParse(settings);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Invalid settings.", issues: parsed.error.issues },
      { status: 400 },
    );
  }

  const id = uuid();
  const safeBase = sanitizeFilename(file.name.slice(0, file.name.length - upload.ext.length) || "input");
  const inputFilename = `${safeBase}${upload.ext}`;

  // Write file to disk first so ffprobe can read it
  await fs.mkdir(jobDir(id), { recursive: true });
  const inputPath = path.join(jobDir(id), inputFilename);
  const buf = Buffer.from(await file.arrayBuffer());
  await fs.writeFile(inputPath, buf);

  const duration = await probeDurationSeconds(inputPath);
  if (duration === null) {
    await fs.rm(jobDir(id), { recursive: true, force: true });
    return NextResponse.json(
      { error: "Could not read the audio file. Make sure ffprobe is installed." },
      { status: 400 },
    );
  }
  if (duration > MAX_DURATION_SECONDS) {
    await fs.rm(jobDir(id), { recursive: true, force: true });
    return NextResponse.json(
      {
        error: `Audio is too long (${Math.round(duration)}s). Max is ${MAX_DURATION_SECONDS}s.`,
      },
      { status: 400 },
    );
  }

  await createJob({
    id,
    filename: inputFilename,
    inputExt: upload.ext,
    settings: parsed.data,
  });

  startOrQueue(id);

  return NextResponse.json({ id }, { status: 201 });
}

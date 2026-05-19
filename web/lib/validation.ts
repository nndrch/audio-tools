import { z } from "zod";

export const MAX_BYTES = 50 * 1024 * 1024; // 50 MB
export const MAX_DURATION_SECONDS = 6 * 60;
export const ALLOWED_EXTENSIONS = new Set([".wav", ".mp3", ".m4a", ".aiff", ".aif", ".flac", ".ogg"]);

const stemOptions = ["vocals", "drums", "bass", "guitar", "piano", "other"] as const;

// Genre is captured as metadata on the job for now — the pipeline doesn't
// consume it yet. When the genre-aware chord vocabulary / harmony-guide work
// from the spec lands, this field is the hook point.
export const GENRE_OPTIONS = [
  "auto",
  "pop_rock",
  "folk",
  "jazz",
  "rnb",
  "funk",
  "country",
  "electronic",
  "classical",
] as const;

export const SettingsSchema = z.object({
  title: z.string().trim().max(200).optional(),
  openPdf: z.boolean().optional(),
  genre: z.enum(GENRE_OPTIONS).optional(),

  // Beat stabilizer
  bpm: z.number().positive().max(400).optional(),
  strength: z.number().min(0).max(1).optional(),
  trimIntro: z.boolean().optional(),
  beatsPerBar: z.number().int().min(2).max(12).optional(),
  skipStabilize: z.boolean().optional(),
  // Bypass the arrangement-level tempo-change guard. Default false — when a
  // sustained tempo shift is detected the pipeline stops with an EARLY_STOP
  // and the UI shows a dedicated error. Set true if you accept that the
  // warp will be musically wrong across the tempo boundary.
  allowTempoChange: z.boolean().optional(),

  // Chord chart
  key: z.string().trim().max(40).optional(),
  timeSig: z.number().int().min(2).max(12).optional(),
  barsPerLine: z.number().int().min(1).max(16).optional(),
  noBpm: z.boolean().optional(),
  noKey: z.boolean().optional(),
  noMeter: z.boolean().optional(),
  subtitle: z.string().max(200).optional(),
  add7th: z.boolean().optional(),
  midBarThreshold: z.number().min(0).max(1).optional(),
  madmomFallback: z.boolean().optional(),
  madmomThreshold: z.number().min(0).max(1).optional(),
  keyTiebreak: z.boolean().optional(),
  keySnap: z.boolean().optional(),
  keySnapThreshold: z.number().min(0).max(1).optional(),
  halfTime: z.boolean().optional(),
  compound: z.boolean().optional(),
  skipSections: z.boolean().optional(),
  deleteOnDownload: z.boolean().optional(),

  // Stems
  skipStems: z.boolean().optional(),
  stems: z.array(z.enum(stemOptions)).optional(),
  stemModel: z.enum(["htdemucs_6s", "htdemucs", "htdemucs_ft", "mdx_extra"]).optional(),
  sessionType: z.enum(["vocals", "guitar", "bass", "piano", "other"]).optional(),

  // ── Beat-detector library knobs ──
  detectorBackend:  z.enum(["auto", "madmom", "librosa"]).optional(),
  madmomBpbOptions: z.string().regex(/^\d+(,\d+)*$/).max(40).optional(),
  madmomFps:        z.number().int().min(20).max(1000).optional(),
  madmomTimeoutS:   z.number().int().min(10).max(3600).optional(),
  librosaStartBpm:  z.number().min(20).max(400).optional(),
  librosaTightness: z.number().min(1).max(1000).optional(),
  librosaHopLength: z.number().int().min(64).max(8192).optional(),
  tsWindowFactor:   z.number().min(0.01).max(1.0).optional(),

  // ── Beat-stabilizer library knobs ──
  introTrimBars:                z.number().int().min(0).max(16).optional(),
  tempoChangeWindowBars:        z.number().int().min(2).max(64).optional(),
  tempoChangePersistBars:       z.number().int().min(1).max(32).optional(),
  tempoChangeThresholdPct:      z.number().min(0).max(1).optional(),
  tempoChangeThresholdFloor:    z.number().min(0).max(100).optional(),
  pyrbCrispness:                z.number().int().min(0).max(6).optional(),

  // ── Chord-detection library knobs ──
  barPhase:           z.boolean().optional(),
  confidenceWarn:     z.number().min(0).max(1).optional(),

  // ── Stem-splitting library knobs ──
  demucsShifts:      z.number().int().min(1).max(10).optional(),
  demucsOverlap:     z.number().min(0).max(0.99).optional(),
  demucsJobs:        z.number().int().min(0).max(64).optional(),
  demucsSegment:     z.number().int().min(0).max(600).optional(),
  demucsDevice:      z.enum(["auto", "cpu", "cuda", "mps"]).optional(),
  demucsInt24:       z.boolean().optional(),
  demucsMp3:         z.boolean().optional(),
  presenceDb:        z.number().min(-120).max(0).optional(),
  presenceWindowS:   z.number().min(0.05).max(10).optional(),
  presenceRunS:      z.number().min(0.05).max(60).optional(),
  backingPeakDbfs:   z.number().min(-30).max(0).optional(),
  backingBitDepth:   z.union([z.literal(16), z.literal(24), z.literal(32)]).optional(),
});

export type Settings = z.infer<typeof SettingsSchema>;

export function extensionOf(filename: string): string {
  const i = filename.lastIndexOf(".");
  return i < 0 ? "" : filename.slice(i).toLowerCase();
}

export function sanitizeFilename(name: string): string {
  // Keep only safe chars; replace runs of unsafe ones with underscore.
  const base = name.replace(/[^A-Za-z0-9._-]+/g, "_");
  return base.replace(/^_+|_+$/g, "") || "input";
}

export function validateUpload(file: { name: string; size: number }):
  | { ok: true; ext: string }
  | { ok: false; status: 400; message: string } {
  const ext = extensionOf(file.name);
  if (!ALLOWED_EXTENSIONS.has(ext)) {
    return {
      ok: false,
      status: 400,
      message: `Unsupported file type "${ext || "(none)"}". Allowed: ${Array.from(ALLOWED_EXTENSIONS).join(", ")}.`,
    };
  }
  if (file.size > MAX_BYTES) {
    return {
      ok: false,
      status: 400,
      message: `File is too large (${(file.size / 1024 / 1024).toFixed(1)} MB). Max is ${MAX_BYTES / 1024 / 1024} MB.`,
    };
  }
  return { ok: true, ext };
}

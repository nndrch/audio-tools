import { spawn, type ChildProcessByStdio } from "node:child_process";
import type { Readable } from "node:stream";
import { createWriteStream, existsSync } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import readline from "node:readline";

import {
  jobDir,
  listJobs,
  outputDir,
  readStatus,
  stderrPath,
  updateStatus,
  zipPath,
  type JobStatus,
} from "./jobs";
import { zipDirectory } from "./zip";
import { REPO_ROOT } from "./jobs";
import { STRUCTURE_LOCKED, LOCKED_STRUCTURE, type Settings } from "./validation";

const STAGE_LABELS: Record<string, string> = {
  queued: "Queued",
  start: "Starting…",
  stabilize: "Stabilizing beats…",
  sections: "Detecting song sections…",
  chord: "Generating chord chart…",
  stems: "Splitting stems…",
  finalize: "Packaging download…",
  done: "Done",
};

export function labelForStage(stage: string): string {
  return STAGE_LABELS[stage] ?? stage;
}

type SpawnedChild = ChildProcessByStdio<null, Readable, Readable>;
let activeChild: SpawnedChild | null = null;
let activeChildPid: number | null = null;
let activeJobId: string | null = null;
let heartbeatTimer: NodeJS.Timeout | null = null;
const queue: string[] = [];
// IDs of jobs the user explicitly cancelled — checked when the child exits
// so we report "Cancelled by user" instead of a misleading exit code 143.
const cancelledIds = new Set<string>();

function startHeartbeat(id: string): void {
  stopHeartbeat();
  heartbeatTimer = setInterval(() => {
    if (activeJobId !== id) return;
    void updateStatus(id, { lastHeartbeatAt: new Date().toISOString() }).catch(() => {});
  }, 2000);
}

function stopHeartbeat(): void {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

function isProcessAlive(pid: number | null | undefined): boolean {
  if (!pid) return false;
  try {
    // Signal 0 = no-op probe; throws ESRCH if the process is gone.
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/**
 * Pick the Python interpreter that has the beat-stabilizer deps installed.
 *
 * setup.sh's Phase 2 runs `python3 -m pip install -r requirements.txt`. On
 * macOS that resolves to /usr/bin/python3 (Apple's CommandLineTools), not
 * the Homebrew python that ends up first on PATH after `brew install
 * python@3.11`. When Next.js inherits the wrong PATH, spawning bare
 * "python3" picks the Homebrew one and beat_stabilizer.py fails with
 * `ModuleNotFoundError: No module named 'numpy'`.
 *
 * Resolution order:
 *   1. $AUDIO_TOOLS_PYTHON (explicit user override)
 *   2. /usr/bin/python3 if present (the binary setup.sh wrote deps into)
 *   3. fall back to "python3" on PATH
 */
function resolvePythonExecutable(): string {
  const override = process.env.AUDIO_TOOLS_PYTHON;
  if (override && existsSync(override)) return override;
  if (existsSync("/usr/bin/python3")) return "/usr/bin/python3";
  return "python3";
}

// When the chord-accuracy structure is locked for the A/B test (see
// STRUCTURE_LOCKED in validation.ts), force the fixed settings onto whatever the
// client sent. Done here, server-side, so the defined structure holds regardless
// of stale localStorage or a hand-crafted request.
function applyLockedStructure(s: Settings): Settings {
  return STRUCTURE_LOCKED ? { ...s, ...LOCKED_STRUCTURE } : s;
}

function settingsToArgs(sIn: Settings, outDir: string, inputPath: string): string[] {
  const s = applyLockedStructure(sIn);
  const args: string[] = [
    "pipeline.py",
    "-i", inputPath,
    "--output-dir", outDir,
    "--progress-json",
  ];

  if (s.title)                  args.push("--title", s.title);

  // Stabilizer
  if (s.skipStabilize)          args.push("--skip-stabilize");
  if (s.bpm)                    args.push("--bpm", String(s.bpm));
  if (s.strength !== undefined && s.strength !== 1.0) args.push("--strength", String(s.strength));
  if (s.trimIntro === false)    args.push("--no-trim-intro");
  if (s.beatsPerBar && s.beatsPerBar !== 4) args.push("--beats-per-bar", String(s.beatsPerBar));
  if (s.allowTempoChange)       args.push("--allow-tempo-change");

  // Chord chart
  if (s.key && s.key !== "auto") args.push("--key", s.key);
  if (s.timeSig)                args.push("--time-sig", String(s.timeSig));
  if (s.barsPerLine && s.barsPerLine !== 4) args.push("--bars-per-line", String(s.barsPerLine));
  if (s.noBpm)                  args.push("--no-bpm");
  if (s.noKey)                  args.push("--no-key");
  if (s.noMeter)                args.push("--no-meter");
  if (s.subtitle !== undefined && s.subtitle !== "") args.push("--subtitle", s.subtitle);
  if (s.add7th)                 args.push("--add-7th");
  if (s.midBarThreshold !== undefined && s.midBarThreshold !== 0.80)
    args.push("--mid-bar-threshold", String(s.midBarThreshold));
  if (s.madmomFallback !== true)  args.push("--no-madmom-fallback");
  if (s.madmomThreshold !== undefined && s.madmomThreshold !== 0.70)
    args.push("--madmom-threshold", String(s.madmomThreshold));
  if (s.keyTiebreak)            args.push("--key-tiebreak");
  if (s.keySnap)                args.push("--key-snap");
  if (s.keySnapThreshold !== undefined && s.keySnapThreshold !== 0.65)
    args.push("--key-snap-threshold", String(s.keySnapThreshold));
  if (s.halfTime)               args.push("--half-time");
  if (s.compound)               args.push("--compound");
  if (s.skipSections)           args.push("--skip-sections");
  if (s.sectionThreshold !== undefined && s.sectionThreshold !== 0)
    args.push("--section-threshold", String(s.sectionThreshold));

  // Stems
  if (s.skipStems)              args.push("--skip-stems");
  if (s.stems && s.stems.length > 0) args.push("--stems", s.stems.join(","));
  if (s.stemModel && s.stemModel !== "htdemucs_6s") args.push("--stem-model", s.stemModel);
  if (s.sessionType && !s.skipStems) args.push("--session-type", s.sessionType);

  // ── Beat-detector library knobs ──
  if (s.detectorBackend && s.detectorBackend !== "auto") args.push("--detector-backend", s.detectorBackend);
  if (s.madmomBpbOptions && s.madmomBpbOptions !== "3,4") args.push("--madmom-bpb-options", s.madmomBpbOptions);
  if (s.madmomFps !== undefined && s.madmomFps !== 100) args.push("--madmom-fps", String(s.madmomFps));
  if (s.madmomTimeoutS !== undefined && s.madmomTimeoutS !== 240) args.push("--madmom-timeout-s", String(s.madmomTimeoutS));
  if (s.librosaStartBpm !== undefined && s.librosaStartBpm !== 120) args.push("--librosa-start-bpm", String(s.librosaStartBpm));
  if (s.librosaTightness !== undefined && s.librosaTightness !== 100) args.push("--librosa-tightness", String(s.librosaTightness));
  if (s.librosaHopLength !== undefined && s.librosaHopLength !== 512) args.push("--librosa-hop-length", String(s.librosaHopLength));
  if (s.tsWindowFactor !== undefined && s.tsWindowFactor !== 0.15) args.push("--ts-window-factor", String(s.tsWindowFactor));

  // ── Beat-stabilizer library knobs ──
  if (s.introTrimBars !== undefined && s.introTrimBars !== 1) args.push("--intro-trim-bars", String(s.introTrimBars));
  if (s.tempoChangeWindowBars !== undefined && s.tempoChangeWindowBars !== 8) args.push("--tempo-change-window-bars", String(s.tempoChangeWindowBars));
  if (s.tempoChangePersistBars !== undefined && s.tempoChangePersistBars !== 4) args.push("--tempo-change-persist-bars", String(s.tempoChangePersistBars));
  if (s.tempoChangeThresholdPct !== undefined && s.tempoChangeThresholdPct !== 0.06) args.push("--tempo-change-threshold-pct", String(s.tempoChangeThresholdPct));
  if (s.tempoChangeThresholdFloor !== undefined && s.tempoChangeThresholdFloor !== 6) args.push("--tempo-change-threshold-floor", String(s.tempoChangeThresholdFloor));
  if (s.pyrbCrispness !== undefined) args.push("--pyrb-crispness", String(s.pyrbCrispness));

  // ── Chord-detection library knobs ──
  if (s.barPhase === false) args.push("--no-bar-phase");
  if (s.confidenceWarn !== undefined && s.confidenceWarn !== 0.45) args.push("--confidence-warn", String(s.confidenceWarn));
  // HPSS preprocessing (default in pipeline.py is "hpss"; only push when different)
  if (s.hpssMode && s.hpssMode !== "hpss") args.push("--hpss-mode", s.hpssMode);
  if (s.hpssMargin !== undefined && s.hpssMargin !== 3.0) args.push("--hpss-margin", String(s.hpssMargin));
  // Bass-anchored root correction (requires stems — server enforces, UI guards).
  // The bass-anchor-margin flag is currently exposed only for tuning; we forward it
  // when non-default so testers can iterate without touching CLI.
  if (s.bassAnchor) args.push("--bass-anchor");
  if (s.bassAnchorMargin !== undefined && s.bassAnchorMargin !== 0.55)
    args.push("--bass-anchor-margin", String(s.bassAnchorMargin));
  // Section-aware chord consistency (post-processing; needs section detection).
  if (s.sectionConsistency) args.push("--section-consistency");
  // Slash chord (inversion) labelling. Server enforces the stems prerequisite
  // (same as bass-anchor); the UI guards against the bad combo too.
  if (s.slashChords) args.push("--slash-chords");
  // Key-conditioned Viterbi smoothing. Tuning knobs only forwarded when non-default.
  if (s.viterbiSmoothing) args.push("--viterbi-smoothing");
  if (s.viterbiStayProb !== undefined && s.viterbiStayProb !== 0.35)
    args.push("--viterbi-stay-prob", String(s.viterbiStayProb));
  if (s.viterbiCadenceBoost !== undefined && s.viterbiCadenceBoost !== 4.0)
    args.push("--viterbi-cadence-boost", String(s.viterbiCadenceBoost));

  // ── Stem-splitting library knobs ──
  if (s.demucsShifts !== undefined && s.demucsShifts !== 1) args.push("--demucs-shifts", String(s.demucsShifts));
  if (s.demucsOverlap !== undefined && s.demucsOverlap !== 0.25) args.push("--demucs-overlap", String(s.demucsOverlap));
  if (s.demucsJobs !== undefined && s.demucsJobs > 0) args.push("--demucs-jobs", String(s.demucsJobs));
  if (s.demucsSegment !== undefined && s.demucsSegment > 0) args.push("--demucs-segment", String(s.demucsSegment));
  if (s.demucsDevice && s.demucsDevice !== "auto") args.push("--demucs-device", s.demucsDevice);
  if (s.demucsInt24) args.push("--demucs-int24");
  if (s.demucsMp3) args.push("--demucs-mp3");
  if (s.presenceDb !== undefined && s.presenceDb !== -30) args.push("--presence-db", String(s.presenceDb));
  if (s.presenceWindowS !== undefined && s.presenceWindowS !== 1.0) args.push("--presence-window-s", String(s.presenceWindowS));
  if (s.presenceRunS !== undefined && s.presenceRunS !== 2.0) args.push("--presence-run-s", String(s.presenceRunS));
  if (s.backingPeakDbfs !== undefined && s.backingPeakDbfs !== -1) args.push("--backing-peak-dbfs", String(s.backingPeakDbfs));
  if (s.backingBitDepth !== undefined && s.backingBitDepth !== 24) args.push("--backing-bit-depth", String(s.backingBitDepth));

  return args;
}

// On module load: mark orphaned "running" jobs as error, re-queue any "queued" jobs.
// A "running" job whose PID is no longer alive (server restart, OS reboot, manual
// kill) cannot be resumed — Python pipelines aren't checkpointed — so we surface
// an explicit error and free the queue slot.
void (async () => {
  const ids = await listJobs().catch(() => [] as string[]);
  const toQueue: Array<{ id: string; startedAt: string }> = [];
  for (const id of ids) {
    const status = await readStatus(id).catch(() => null);
    if (!status) continue;
    if (status.state === "running" && !isProcessAlive(status.pid)) {
      await updateStatus(id, {
        state: "error",
        error: { exitCode: null, stderrTail: "Server was restarted while this job was running. Please try again." },
        finishedAt: new Date().toISOString(),
        pid: null,
      }).catch(() => {});
    } else if (status.state === "queued") {
      toQueue.push({ id, startedAt: status.startedAt });
    }
  }
  toQueue
    .sort((a, b) => a.startedAt.localeCompare(b.startedAt))
    .forEach(({ id }) => { if (!queue.includes(id)) queue.push(id); });
  void pumpQueue();
})();

/**
 * Cancel a queued or running job.
 *
 * For queued jobs: removes the id from the in-memory queue and marks the
 * status.json as error.
 *
 * For the actively-running job: sends SIGTERM to the entire process group
 * (which catches madmom's nested subprocess and Demucs workers) and follows
 * up with SIGKILL after 3 s if the group is still alive. The id is recorded
 * in `cancelledIds` so the runJob close handler labels the result as
 * "Cancelled by user" instead of reporting exit code 143.
 */
export async function cancelJob(id: string): Promise<boolean> {
  const status = await readStatus(id);
  if (!status) return false;
  if (status.state === "done" || status.state === "error") return false;

  if (status.state === "queued") {
    const idx = queue.indexOf(id);
    if (idx >= 0) queue.splice(idx, 1);
    await updateStatus(id, {
      state: "error",
      error: { exitCode: null, stderrTail: "Cancelled by user." },
      finishedAt: new Date().toISOString(),
      pid: null,
    });
    return true;
  }

  // Running.
  // SAFETY: only kill if this Node process is actively running this job and
  // the in-memory pid matches the spawned child. We never trust an on-disk
  // pid alone — a stale/corrupted status.json could otherwise let us signal
  // an unrelated process owned by the same user.
  if (activeJobId === id && activeChild && activeChildPid) {
    cancelledIds.add(id);
    const pid = activeChildPid;
    try {
      process.kill(-pid, "SIGTERM");
    } catch {
      try { process.kill(pid, "SIGTERM"); } catch { /* gone */ }
    }
    setTimeout(() => {
      if (isProcessAlive(pid)) {
        try { process.kill(-pid, "SIGKILL"); } catch {
          try { process.kill(pid, "SIGKILL"); } catch { /* gone */ }
        }
      }
    }, 3000);
    // runJob's close handler will see cancelledIds and write the final status.
  } else {
    // Orphaned: state says "running" but this Node isn't tracking it (server
    // was restarted, or status.json is stale). Just mark as error, no signal.
    await updateStatus(id, {
      state: "error",
      error: { exitCode: null, stderrTail: "Cancelled by user (job was orphaned)." },
      finishedAt: new Date().toISOString(),
      pid: null,
    });
  }
  return true;
}

export function startOrQueue(id: string): void {
  if (activeJobId) {
    if (!queue.includes(id)) queue.push(id);
    return;
  }
  void runJob(id).catch(async (err) => {
    console.error("Job execution error", err);
    await updateStatus(id, {
      state: "error",
      error: { exitCode: null, stderrTail: String(err) },
      finishedAt: new Date().toISOString(),
    });
    activeJobId = null;
    activeChild = null;
    void pumpQueue();
  });
}

async function pumpQueue(): Promise<void> {
  const next = queue.shift();
  if (next) startOrQueue(next);
}

async function runJob(id: string): Promise<void> {
  const status = await readStatus(id);
  if (!status) return;

  activeJobId = id;

  const inputPath = path.join(jobDir(id), status.filename);
  const outDir = outputDir(id);
  await fs.mkdir(outDir, { recursive: true });

  const args = settingsToArgs(status.settings, outDir, inputPath);
  const stderrStream = createWriteStream(stderrPath(id), { flags: "a" });

  const pythonExe = resolvePythonExecutable();
  const child = spawn(pythonExe, args, {
    cwd: REPO_ROOT,
    env: {
      ...process.env,
      NO_COLOR: "1",
      PYTHON_COLORS: "0",
      FORCE_COLOR: "0",
    },
    stdio: ["ignore", "pipe", "pipe"],
    // Put the child in its own process group so we can SIGTERM the whole
    // tree (madmom subprocess, Demucs workers) with `kill(-pgid, ...)`.
    detached: true,
  }) as SpawnedChild;
  activeChild = child;
  activeChildPid = child.pid ?? null;

  await updateStatus(id, {
    state: "running",
    pct: 0,
    stage: "start",
    pid: child.pid ?? null,
    lastHeartbeatAt: new Date().toISOString(),
  });
  startHeartbeat(id);

  // Stderr → log file
  child.stderr.pipe(stderrStream);

  // Stdout → parse PROGRESS + EARLY_STOP lines.
  // EARLY_STOP is the pipeline's structured way of saying "I bailed and here's
  // why" — currently used for tempo-change detection in beat_stabilizer.py.
  // We capture the payload here and surface it on the non-zero exit path
  // below, so the UI shows a dedicated error message instead of stderr tail.
  type EarlyStop = { kind: "tempo_change"; details: Record<string, unknown> };
  // TS narrows `let x: T | null = null` to `null` outside of closures because
  // assignments inside the readline callback don't flow into the outer scope's
  // type analysis. Use a holder object so the narrowing happens on the field
  // (which TS can't narrow across writes via closure).
  const stopRef: { value: EarlyStop | null } = { value: null };
  const rl = readline.createInterface({ input: child.stdout });
  rl.on("line", (line) => {
    stderrStream.write(line + "\n");
    if (line.startsWith("PROGRESS ")) {
      try {
        const payload = JSON.parse(line.slice("PROGRESS ".length)) as {
          stage?: string;
          pct?: number;
          msg?: string;
        };
        if (typeof payload.pct === "number") {
          const patch: Partial<JobStatus> = {
            pct: clamp(payload.pct, 0, 100),
            lastHeartbeatAt: new Date().toISOString(),
          };
          if (payload.stage) patch.stage = payload.stage;
          void updateStatus(id, patch);
        }
      } catch {
        // ignore malformed
      }
    } else if (line.startsWith("EARLY_STOP ")) {
      try {
        const payload = JSON.parse(line.slice("EARLY_STOP ".length)) as {
          reason?: string;
          [k: string]: unknown;
        };
        if (payload.reason === "tempo_change") {
          const { reason, ...details } = payload;
          void reason;
          stopRef.value = { kind: "tempo_change", details };
        }
      } catch {
        // ignore malformed
      }
    }
  });

  const exit: number = await new Promise((resolve) => {
    child.on("close", (code) => resolve(code ?? 1));
  });
  stderrStream.end();
  stopHeartbeat();
  activeChild = null;
  activeChildPid = null;

  const wasCancelled = cancelledIds.delete(id);

  // Clear the pid and refresh the heartbeat immediately: between here and the
  // final "done" write we still run zipDirectory (can be several seconds for
  // 150 MB+). Without this, the GET liveness check sees state="running" with
  // a now-dead pid and flips the job to error, briefly flashing a false-
  // positive crash screen right before "Processing complete".
  //
  // The heartbeat is then restarted so the UI doesn't drift into "Quiet for
  // Xs" warnings during a long zip — finalize is a known sparse phase but
  // it's still actively progressing.
  if (exit === 0 && !wasCancelled) {
    await updateStatus(id, {
      stage: "finalize",
      pct: 99,
      pid: null,
      lastHeartbeatAt: new Date().toISOString(),
    });
    startHeartbeat(id);
  }

  if (wasCancelled) {
    await updateStatus(id, {
      state: "error",
      error: { exitCode: null, stderrTail: "Cancelled by user." },
      finishedAt: new Date().toISOString(),
      pid: null,
    });
  } else if (exit === 0) {
    try {
      await fs.mkdir(outDir, { recursive: true });
      await zipDirectory(outDir, zipPath(id));
      await updateStatus(id, {
        state: "done",
        pct: 100,
        stage: "done",
        finishedAt: new Date().toISOString(),
        pid: null,
      });
    } catch (err) {
      await updateStatus(id, {
        state: "error",
        error: { exitCode: 0, stderrTail: `ZIP failed: ${err}` },
        finishedAt: new Date().toISOString(),
        pid: null,
      });
    }
  } else {
    const tail = await tailFile(stderrPath(id), 40);
    const es = stopRef.value;
    await updateStatus(id, {
      state: "error",
      error: es
        ? { exitCode: exit, stderrTail: tail, kind: es.kind, details: es.details }
        : { exitCode: exit, stderrTail: tail },
      finishedAt: new Date().toISOString(),
      pid: null,
    });
  }

  stopHeartbeat();
  activeJobId = null;
  void pumpQueue();
}

// Strip ANSI escape sequences (color codes, cursor moves) so they don't
// leak into the error view as literal "[35m..." text.
// eslint-disable-next-line no-control-regex
const ANSI_RE = /\[[0-9;]*[A-Za-z]/g;

async function tailFile(filePath: string, lines: number): Promise<string> {
  try {
    const raw = await fs.readFile(filePath, "utf8");
    const all = raw.replace(ANSI_RE, "").split("\n");
    return all.slice(Math.max(0, all.length - lines)).join("\n");
  } catch {
    return "";
  }
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

export function isRunning(id: string): boolean {
  return activeJobId === id;
}

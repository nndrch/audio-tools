export async function register() {
  // Only run in the Node.js runtime (not Edge), and only server-side.
  if (process.env.NEXT_RUNTIME !== "nodejs") return;

  const { default: fsp } = await import("node:fs/promises");
  const { JOBS_ROOT } = await import("@/lib/jobs");

  try {
    const entries = await fsp.readdir(JOBS_ROOT);
    await Promise.all(
      entries.map((entry) =>
        fsp.rm(`${JOBS_ROOT}/${entry}`, { recursive: true, force: true })
      )
    );
    if (entries.length > 0) {
      console.log(`[audio-tools] Cleared ${entries.length} stale job(s) from ${JOBS_ROOT}`);
    }
  } catch {
    // JOBS_ROOT doesn't exist yet — that's fine, first run.
  }
}

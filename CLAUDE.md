# audio-tools — Project Instructions

Project-specific preferences for Claude Code working in this repo. These complement the global `~/.claude/CLAUDE.md`. **Store project-related preferences here, in this file — not in global memory.** When the user states a new project preference, add it here.

## Repository structure & versions

This repo holds **two parallel versions of the app** so their outputs can be compared:

- **`master` — v1 (Stage 5), frozen baseline.** The detection-accuracy-levers release; it stays working and untouched. Do **not** land features here. Immutable reference: tag **`v1.0-stage5`**.
- **`develop` — v2 (Stage 6+), the active line.** All new work (code *and* docs) lands here. Branch features as `feat/<name>` off `develop` and merge them back into `develop`.

Running both side by side: v1 is checked out as a git worktree at **`../audio-tools-v1`** (detached at `v1.0-stage5`), sharing this checkout's venvs via symlink. To compare, run each with a distinct `--output-dir` (or `AUDIO_TOOLS_JOBS_DIR` for the web UI); the v2-only eval harness (`eval/run.py` + `eval/score.py`) can score v1 vs v2 over the same dataset.

Only fast-forward `master` to `develop` (promoting v2 → v1) on an explicit decision — never silently.

## Markdown styling

- Write prose as **one line per paragraph**. Never hard-wrap or fill prose at a fixed column — the editor soft-wraps for display.
- Keep on their own lines (never merge into a paragraph): table rows, fenced/indented code, headings, bold colon-label fields (`**When to change:**`, `**Default:**`, header metadata such as `**Status:**` / `**Project:**`), and hard line breaks (lines ending in two spaces or a backslash).

## Project documentation

- The roadmap lives in [`docs/roadmap.md`](docs/roadmap.md). **Whenever a phase/stage concludes, update the roadmap in the same change**: move the finished stage to "concluded" with a one-line summary of what was built, advance the "current stage," and adjust the "next stages" list.
- Keep each doc at its own altitude: the roadmap stays at one-paragraph-per-stage; detailed design/status lives in the stage-specific doc it links to.

## Working in phases

- Work **one phase at a time**. After a phase is done, **stop and report** — never start the next phase without an explicit command from the user.
- Follow the milestone routine: small, measurable, testable milestones; test new logic in isolation; everything default-off until a measured win. Stop after each completed milestone and report before starting the next.

## Local app — start it after each milestone

The "application" here is the local **web UI**: a Next.js dev server at `http://localhost:3000`. The CLI/pipeline is not a persistent service, so this routine is about the web UI only.

After a milestone is finished **entirely** (built, tested in isolation, and reported per **Working in phases** above — not after each intermediate step), make sure that web UI is running on the local environment so the result can be tried immediately — don't leave the user to start it by hand:

1. **Check** whether it's already up — `lsof -ti tcp:3000` (a PID means yes) or `curl -sf -o /dev/null http://localhost:3000`.
2. **If it's up, leave it** — never restart it; a pipeline job may be in flight.
3. **If it's not, start it in the background** (it's a long-running server), wait for it to come up, then report the URL:

```bash
cd web && npm run dev      # serves http://localhost:3000
```

Override the Python interpreter with `AUDIO_TOOLS_PYTHON` if the default isn't right (see [`docs/local-setup.md`](docs/local-setup.md)).

## New feature requests mid-development

- If the user proposes a **new feature during development**, do not silently fold it into the current phase. Pause and weigh it explicitly, then decide *with the user* where it belongs:
  - **On the roadmap** — if it's in-scope for the MVP; sequence it as its own stage/phase rather than expanding the current one.
  - **In the post-MVP backlog** — if it's out of scope for now; capture it in [`docs/post-mvp.md`](docs/post-mvp.md) (create the file if it doesn't exist yet) so it isn't lost.
- Surface the trade-off (scope, sequence, effort, risk to the current phase) and let the user choose via a clear question. Default to *not* growing the in-flight phase.

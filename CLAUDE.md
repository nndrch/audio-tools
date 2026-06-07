# audio-tools — Project Instructions

Project-specific preferences for Claude Code working in this repo. These complement the global `~/.claude/CLAUDE.md`. **Store project-related preferences here, in this file — not in global memory.** When the user states a new project preference, add it here.

## Markdown styling

- Write prose as **one line per paragraph**. Never hard-wrap or fill prose at a fixed column — the editor soft-wraps for display.
- Keep on their own lines (never merge into a paragraph): table rows, fenced/indented code, headings, bold colon-label fields (`**When to change:**`, `**Default:**`, header metadata such as `**Status:**` / `**Project:**`), and hard line breaks (lines ending in two spaces or a backslash).

## Project documentation

- The roadmap lives in [`docs/roadmap.md`](docs/roadmap.md). **Whenever a phase/stage concludes, update the roadmap in the same change**: move the finished stage to "concluded" with a one-line summary of what was built, advance the "current stage," and adjust the "next stages" list.
- Keep each doc at its own altitude: the roadmap stays at one-paragraph-per-stage; detailed design/status lives in the stage-specific doc it links to.

## Working in phases

- Work **one phase at a time**. After a phase is done, **stop and report** — never start the next phase without an explicit command from the user.
- Follow the milestone routine: small, measurable, testable milestones; test new logic in isolation; everything default-off until a measured win. Stop after each completed milestone and report before starting the next.

## New feature requests mid-development

- If the user proposes a **new feature during development**, do not silently fold it into the current phase. Pause and weigh it explicitly, then decide *with the user* where it belongs:
  - **On the roadmap** — if it's in-scope for the MVP; sequence it as its own stage/phase rather than expanding the current one.
  - **In the post-MVP backlog** — if it's out of scope for now; capture it in [`docs/post-mvp.md`](docs/post-mvp.md) (create the file if it doesn't exist yet) so it isn't lost.
- Surface the trade-off (scope, sequence, effort, risk to the current phase) and let the user choose via a clear question. Default to *not* growing the in-flight phase.

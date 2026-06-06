# Chord Chart MusicXML — Improvement Proposal

Proposed improvements to the MusicXML export, measured against two references:

- **MusicXML 4.0 (W3C):** <https://www.w3.org/2021/06/musicxml40/> — element structure, child order, enumerations (normative).
- **Lead-sheet conventions (Berklee Today, "The Lead Sheet"):** <https://www.berklee.edu/berklee-today/summer-2018/lead-sheet> — chord-symbol spelling, road map, legibility.

> Status: **proposal only** — no code changed. The full reference lives in
> `chord-chart-generation-reference.md` (authoring guide). Section numbers
> below (§4.x) refer to that guide.

## How this was assessed

The findings are empirical, not theoretical: the real `bar_chords_to_musicxml()`
([`chord_chart_render.py:848`](../chord_chart_render.py#L848)) was run through
`venv_crema` with a synthetic 4-bar chart (incl. a slash chord, a `maj7`, an `N`
no-chord bar, and two sections) and the emitted XML was inspected directly.

All MusicXML generation lives in three places:

- `bar_chords_to_musicxml()` — [`chord_chart_render.py:848`](../chord_chart_render.py#L848)
- `_crema_to_m21_figure()` — [`chord_chart_render.py:820`](../chord_chart_render.py#L820)
- `_QUALITY_TO_M21` map — [`chord_chart_render.py:808`](../chord_chart_render.py#L808)
- Call site (where `bpm` etc. are in scope) — [`chord_chart_render.py:2206`](../chord_chart_render.py#L2206)

The LilyPond/PDF path is separate (`generate_lilypond()`,
[`chord_chart_render.py:936`](../chord_chart_render.py#L936)) and is **not**
touched by any P0/P1 item below.

## What the generator already does right ✅

- Emits the XML prolog **and** the MusicXML 4.0 `<!DOCTYPE>` (§2/§3 — many tools drop these).
- `score-partwise version="4.0"`; correct `key/fifths`+`mode`, `time`, and `harmony` child order (`root → kind → bass`).
- **One `harmony` per chord change** (segment-based, not per-beat), with mid-bar offsets preserved (§4.7).
- Per-note `<notehead>slash</notehead>` — the portable form the reference prefers over `measure-style/slash` (§4.8).
- `N` → no `harmony`, just slashes (§4.7).
- `rehearsal` marks at section starts (§4.10); enharmonic spelling follows the detected key (§7).

---

## P0 — correctness & functional gaps

### 1. No tempo is emitted at all

Confirmed: **zero** `<metronome>` and **zero** `<sound tempo>` in the output.
The reference (§4.9) says write both, and the BPM is already known at the call
site ([`chord_chart_render.py:2240`](../chord_chart_render.py#L2240)). Today
MuseScore plays every chart back at a default 120 and prints no tempo mark.

**Fix:** pass `bpm` into `bar_chords_to_musicxml()` and add a
`tempo.MetronomeMark(number=bpm, referent=...)` to measure 1 (referent =
dotted-quarter for 6/8, quarter otherwise).

### 2. `<creator type="composer">Music21</creator>` is written

The reference explicitly warns against this in §4.2 ("avoid writing a
meaningless creator… a renderer will print it"). music21 stamps *itself* as
composer, so MuseScore prints "Music21" on the chart.

**Fix:** after `metadata.Metadata(title=title)`
([`chord_chart_render.py:873`](../chord_chart_render.py#L873)), clear the
composer (or set the real artist if available). Also dedupes the redundant
`<movement-title>`.

### 3. Quality mapping silently corrupts non-triads

`_QUALITY_TO_M21.get(quality, "")`
([`chord_chart_render.py:808`](../chord_chart_render.py#L808)) falls back to an
**empty suffix = major triad** for anything unmapped, and the half-diminished
mapping mis-round-trips through music21. Measured output:

| Detector label | exported `<kind>` | correct? |
|---|---|---|
| `B:hdim7` | `minor-seventh` | ✗ should be `half-diminished` |
| `C:min9` | `major` | ✗ quality lost entirely |
| `D:13` | `major` | ✗ quality lost entirely |
| `E:dim7` | `diminished-seventh` | ✓ |
| `F:aug` | `augmented` | ✓ |
| `C:sus4` | `suspended-fourth` | ✓ |

The reference (§7) says unmapped qualities should fall back to the *nearest*
enum value, never silently to major.

**Fix:** map `hdim7 → "ø7"` figure (music21 parses ø to `half-diminished`) and
make the fallback degrade to the nearest base of the right mode (e.g.
`min9 → m`, `13 → 7`) instead of major. Mostly bites when `--add-7th` is on, but
`hdim7` is real.

---

## P1 — chart quality & cross-renderer portability

### 4. `<kind>` has no `text` attribute

Output is `<kind>minor</kind>` with no `text`, so each renderer invents its own
symbol (and half-dim shows `m7b5`, not `ø7`). The reference (§4.7, §6) wants
`kind text="…"` with the Berklee-conventional symbol (`mi`, `Maj7`, `ø7`, `°`)
so the printed chart reads consistently everywhere.

**Fix:** set the displayed text per chord when building each `ChordSymbol`.

### 5. No clef and no final barline

Confirmed: clef count = 0, barline count = 0. Reference wants a treble clef
(§4.6) and a `light-heavy` final barline (§4.11).

**Fix:** prepend `clef.TrebleClef()` to the part and set the last measure's
`rightBarline = bar.Barline('final')`.

### 6. No system breaks → MusicXML doesn't match the PDF's 4-bars/line

The LilyPond path breaks every `bars_per_line`
([`chord_chart_render.py:971`](../chord_chart_render.py#L971)) but the MusicXML
carries no layout, so MuseScore auto-packs bars by density. Reference §4.12 says
force ~4 bars/line via `print new-system="yes"`.

**Fix:** insert `layout.SystemLayout(isNew=True)` at the start of every Nth
measure, reusing the same `bars_per_line` the PDF uses.

---

## P2 — nice-to-have / larger

- **7. Road map (repeats/endings).** Biggest *content* win for the "one page"
  goal (§4.11, §8): collapse identical repeated sections into `repeat`/`ending`
  barlines instead of writing every bar linearly. The section data +
  `--section-consistency` voting already exist to detect repeats and could feed
  this. Higher effort; good follow-up.
- **8. `divisions=10080`** (music21's default LCM). Reference §4.3 notes a slash
  chart needs only `1`–`4`. Cosmetic — shrinks the file and improves round-trip
  readability.
- **9. Style/feel + BPM/key as `words`.** The subtitle (BPM·key·meter) currently
  lives only in the PDF; a `words` direction (§4.10) would carry it into
  MusicXML too.
- **10. 6/8 slash type.** The PDF renders eighth-note slashes for 6/8
  ([`chord_chart_render.py:953`](../chord_chart_render.py#L953)) but the MusicXML
  emits `type=quarter`. Durations sum correctly so it's valid, but the *notated*
  value disagrees with the PDF — worth reconciling (§4.8).
- **11. Pickup/anacrusis** as `<measure implicit="yes">` (§8.6) — only if the
  detector ever surfaces one.

---

## Effort summary

| Item | Tier | Effort | Touches |
|---|---|---|---|
| 1. Tempo (metronome + sound) | P0 | Small | `bar_chords_to_musicxml` + call site |
| 2. Drop "Music21" composer | P0 | Trivial | `bar_chords_to_musicxml` |
| 3. Quality-map corruption | P0 | Small | `_QUALITY_TO_M21`, `_crema_to_m21_figure` |
| 4. `kind` text symbols | P1 | Small | `bar_chords_to_musicxml` |
| 5. Clef + final barline | P1 | Trivial | `bar_chords_to_musicxml` |
| 6. System breaks (4 bars/line) | P1 | Small | `bar_chords_to_musicxml` + call site |
| 7. Road map repeats/endings | P2 | Large | new logic + section data |
| 8. Smaller `divisions` | P2 | Trivial | `bar_chords_to_musicxml` |
| 9. Feel/tempo `words` | P2 | Small | `bar_chords_to_musicxml` |
| 10. 6/8 slash type | P2 | Small | `bar_chords_to_musicxml` |
| 11. Pickup/anacrusis | P2 | Medium | grid + `bar_chords_to_musicxml` |

P0 + P1 are all small, localized edits to `bar_chords_to_musicxml()` and its two
helpers, and none touch the PDF path.

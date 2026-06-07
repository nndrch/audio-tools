# How to Map a Song's Chords

*A short guide for the musicians helping us build our chord reference set.*

## What we're asking you to do

We're building software that listens to a song and writes out its chords. To check how good it is, we need a small set of songs where the chords are **known to be correct** — mapped out by a real musician's ear. That's where you come in.

For each song, you'll fill in a simple list: **when each chord starts, and what that chord is.** That's it. We turn your list into the format our software needs — you never have to touch anything technical.

**Use whatever you already work in.** Logic Pro, Pro Tools, or any other DAW — it doesn't matter. We only need the times and the chord names, and every program can show you the playhead time.

---

## What you'll send back

Two things, per song:

1. The **song file** we sent you (unchanged).
2. A filled-in **chord sheet** — the template we provide (`annotation-template.csv`), which you can open in Numbers, Excel, Google Sheets, or any text editor.

The chord sheet has just two columns:

| start | chord |
|---|---|
| `0:00` | `C:maj` |
| `0:08` | `A:min` |
| `0:17` | `F:maj` |
| `0:25` | `G:7` |

One row each time the chord changes: the **time it starts** and **what it is**. You don't mark where a chord *ends* — it simply runs until the next one begins, and the last chord runs to the end of the song. We work the rest out.

---

## Step by step

**1. Open the song in your DAW** (Logic, Pro Tools, whatever you use).

**2. Switch the timeline to show minutes and seconds.** Every DAW has a time display option (often labelled "Time", "Min:Sec", or a clock icon) as opposed to bars and beats. Use minutes:seconds so the numbers match what goes in the sheet.
- *Logic Pro:* click the small triangle on the LCD/time display and choose **Time** (or set the ruler's secondary display to time).
- *Pro Tools:* set the **Main Timebase** to **Min:Secs** (the ruler dropdown).

**3. Find where each chord changes.** Play through and listen. A handy trick in any DAW: **drop a marker at each chord change** and name the marker with the chord. Markers let you jump back and read the exact start time of each one. (Markers are optional — if you'd rather just scrub the playhead and read the time off the display, that works too.)

**4. Write each chord into the sheet.** For every chord, add a row: the **time it starts** and the **chord name** (see the cheat sheet below). Start your first row at `0:00`. Keep them in order.

**5. Save and send** the chord sheet plus the song file back to us.

---

## How to write the chord names

Write each chord as the **note name**, a **colon**, then the **type**:

| What you hear | What to type |
|---|---|
| Major (e.g. just "C", "G") | `C:maj` |
| Minor (e.g. "Am", "Em") | `A:min` |
| Dominant 7 (e.g. "G7") | `G:7` |
| Major 7 (e.g. "Cmaj7") | `C:maj7` |
| Minor 7 (e.g. "Dm7") | `D:min7` |
| Diminished | `B:dim` |
| Augmented | `C:aug` |
| Suspended (sus2 / sus4) | `C:sus2` / `C:sus4` |
| **No chord** (silence, drums only, unclear) | `N` |

**Note names:** use `A B C D E F G`. For sharps add `#` (e.g. `F#:maj`), for flats add `b` (e.g. `Bb:min`, `Eb:maj`). Sharp or flat spelling is fine as long as it's the right pitch.

**A few simple rules to keep it easy:**

- **The two things that matter most** are the **note** (the letter) and whether it's **major or minor**. Nail those and you've done 90% of the value.
- **Hearing something fancier?** (a 9th, an 11th, an altered chord) — just write the closest basic chord from the list. Root plus major / minor / 7 is plenty.
- **Slash chords** (like "C/E") — ignore the part after the slash and just write the main chord (`C:maj`). No need to capture the bass note.
- **No spaces** inside a chord name.

---

## How to write the times

Whatever your DAW shows is fine, in any of these forms:

- `0:08` or `1:23` — minutes:seconds
- `0:08.5` or `1:23.250` — with decimals if you want to be precise
- `8.5` — plain seconds also works

**How precise?** Mark where you **hear** each chord change. Landing within about **a quarter of a second** is plenty — you don't need to be perfect to the millisecond. Getting the chord *names* right matters far more than razor-sharp timing.

If the song ends with a stretch that has no chord (a fade-out, noise, silence), add one last row with `N` at the time that stretch begins. Otherwise just stop at the final chord — we know how long the song is.

---

## What the finished sheet looks like

```
start,chord
0:00,C:maj
0:08,A:min
0:17,F:maj
0:25,G:7
0:34,N
```

That's the whole deliverable. We convert it into the format our software reads — you never see or edit that part.

---

## Quick checklist before you send it back

- [ ] Used the exact song file we sent you
- [ ] First row starts at `0:00`
- [ ] One row per chord change, in order, all the way through the song
- [ ] Used `N` for any stretch with no clear chord
- [ ] Wrote chord names in the `note:type` style from the cheat sheet
- [ ] Sending back **both** the song and the filled-in chord sheet

That's it — thank you. Your trained ear is the standard we measure everything against.

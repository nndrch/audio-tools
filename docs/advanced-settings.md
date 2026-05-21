# Advanced settings reference

Every knob in the **Advanced settings** panel, explained in plain English.

The panel is organised by pipeline stage:

1. [Beat Detection](#1-beat-detection) — finding the beat and time signature
2. [Beat Stabilizer](#2-beat-stabilizer) — warping the audio onto an even tempo grid
3. [Chord Detection](#3-chord-detection) — finding chords and rendering the lead sheet
4. [Stem Splitting](#4-stem-splitting) — separating the song into vocals, drums, bass, etc.

Each section has a **Primary** list (controls you might actually want to touch) and a **Library tuning** expand for the long-tail knobs that pass straight through to the underlying audio tools. The defaults are tuned for typical pop/folk/rock material — start by leaving everything alone, then tweak only what the output asks for.

---

## 1. Beat Detection

Finds where the beats are in your audio, plus an estimate of the time signature.

### Beats per bar
How many beats are in one bar. Most pop and rock is 4. Waltzes and many folk tunes are 3. Some pieces in compound time read as 6.
**When to change:** if the detected time signature is wrong on your song. The chord chart will lay bars out using this number.
**Default:** Auto-detect. **Range:** 2, 3, 4, 6 (or Auto).

### Detector backend
Which algorithm hunts for the beats. *Auto* uses the more accurate detector first (which also identifies downbeats) and falls back to the faster one if it fails. *Force madmom* runs only the slower-but-better one. *Force librosa* runs only the faster one (no downbeat info).
**When to change:** force the fast detector if you're iterating quickly on a long file; force the slow one if downbeats are coming out wrong.
**Default:** Auto.

### Candidate beats-per-bar (CSV)
A comma-separated list of bar lengths the downbeat detector should try when picking the most likely meter. For example `3,4` lets it choose between waltz and 4/4; `3,4,5,7` would also consider 5/4 and 7/4.
**When to change:** if your piece is in an odd meter the default list doesn't cover.
**Default:** `3,4`.

### Library tuning — madmom & librosa

#### madmom fps (Hz)
How finely the slow-but-accurate detector samples the audio. Higher = slightly better timing precision, but more memory and time.
**When to change:** almost never. Bumping to 200 can help on very fast material.
**Default:** 100.

#### madmom timeout (s)
How long to wait for the slow detector before giving up and falling back to the fast one. Long files on slow machines may need more.
**Default:** 240 seconds.

#### librosa start BPM
The initial tempo guess fed to the fast detector. It's not locked to this — it's just a starting point. A bad guess can lead the tracker astray on tempo-ambiguous tracks.
**When to change:** if the fast detector keeps choosing half- or double-time, set this near the tempo you actually want.
**Default:** 120.

#### librosa tightness
How insistently the fast detector pulls beats toward an even spacing. Higher = stiffer grid (good for electronic music). Lower = more flexible (better for live takes with rubato).
**Default:** 100.

#### librosa hop length
Window stride in samples for spectral analysis. Smaller = finer timing detail, more CPU. Larger = coarser.
**Default:** 512.

#### Time-sig autocorr window
A smoothing width used when scoring candidate bar lengths. Larger = more tolerant of timing drift; smaller = sharper, but noisier.
**Default:** 0.15.

---

## 2. Beat Stabilizer

Warps the audio so every beat lands on an even tempo grid. Like Ableton's "warp to grid" — the file becomes trivially loopable and lines up with a click track at the target BPM.

### Warp strength
How aggressively each beat is pulled toward the ideal grid position. Full lock makes every beat perfectly even (loses human feel). Light touch only fixes the worst offenders. No warp leaves the timing untouched.
**When to change:** dial down if the stabilised audio sounds robotic or has stretching artifacts on transient-heavy material like drums.
**Default:** Full lock (1.0).

### Trim intro to one bar before beat 1
After stabilising, cut the file so it starts exactly one bar before the first detected beat. Drop the WAV at bar 1 of a DAW and everything lines up.
**Default:** On.

### Skip stabilization entirely
Don't warp at all. Use this if the file is already a click-tight render (a programmed track, a quantised stem) and you just want the chord chart and stems.
**Default:** Off.

### Allow tempo change (skip the multi-tempo guard)
Normally the stabilizer stops with an error if it detects a sustained tempo change between sections — a single-tempo warp would mangle audio across the boundary. Enable this only if you accept that the warp will be musically wrong at the tempo change.

**Note:** the guard already ignores pure octave flips. If the beat tracker hears the first half in quarter notes (say 105 BPM) and the second half in eighth notes (~210 BPM), that's not a real tempo change — it's the same tempo at a different metrical level — and the guard will let the file through. You only need this checkbox for *genuine* arrangement-level tempo shifts (a ballad section at 70 BPM followed by a chorus at 90 BPM, say).

**Default:** Off (guard on).

### Library tuning — intro trim, tempo guard, beat octaves, rubberband

#### Beat-octave normalization
On by default. After beat detection, the stabilizer walks the beat list and looks for sections where the tracker locked onto eighth notes (gap ≈ ½ the running quarter-note interval) or half notes (gap ≈ 2×) — common mid-song flips. Doubled beats get thinned, halved gaps get a midpoint inserted, so the warper sees a consistent quarter-note grid throughout. Without this, an octave flip mid-song would stretch the doubled-density section to 2× its real length.

Tolerance is ±18 % of the target ratio, so legitimate 1.3× / 1.5× tempo changes still trip the regular tempo-change guard.

**When to change:** turn off only if you suspect this is misfiring on a genuinely variable-tempo song.
**CLI flag (no UI toggle yet):** `--no-beat-octave-normalize`.
**Default:** On.


#### Intro trim (bars)
How many bars to keep before the first beat when trimming. 1 is one bar; 2 gives you a 2-bar pickup, etc. 0 trims right up to the first beat.
**Default:** 1.

#### rubberband crispness 0–6
Sharpness preset for the time-stretching engine. 0 = smoothest (best for sustained tones, may smear transients). 6 = sharpest (preserves transients, may sound a touch grainy on pads). Blank = library default.
**When to change:** dial up if drums smear; dial down if vocals sound chattery.
**Default:** blank (library default, around 5).

#### Tempo-change window (bars)
The scanner that catches arrangement-level tempo shifts averages tempo over this many bars before comparing. Larger = ignores brief slowdowns; smaller = catches subtle drift.
**Default:** 8 bars.

#### Tempo-change persistence (bars)
The new tempo has to stick around this many bars before the guard trips. Larger = more conservative (will let through brief tempo bumps that resolve).
**Default:** 4 bars.

#### Tempo-change threshold (fraction)
How big a tempo step counts as a "change," as a fraction of the current tempo. 0.06 means a 6% jump.
**Default:** 0.06.

#### Tempo-change minimum step (BPM)
A floor in absolute BPM so the percentage threshold doesn't fire on tiny absolute changes at slow tempos.
**Default:** 6.

---

## 3. Chord Detection

Listens to the stabilised audio, finds the chords on each beat, and renders a PDF + MusicXML lead sheet with section markers (A/B/C boxes).

### Chord input cleaning (HPSS)
Pre-processes the audio with **Harmonic-Percussive Source Separation** before the chord model sees it, so the model isn't distracted by drum hits. Three modes:

- **Off** — the chord model gets the full mix.
- **HPSS** *(default)* — strip drum transients via `librosa.effects.hpss`. Near-free quality bump on any mix with drums; key and beat detection still use the raw signal.
- **HPSS + drum removal** — also subtract the drums stem, then HPSS the residual. Best quality on percussive or dense mixes, but Demucs has to run before chord detection (~30 s – 2 min added). Requires Stem Splitting enabled; the option is greyed out otherwise.

**When to change:** drop to **Off** if you're hearing weird chord artefacts that line up with the drum hits and you want a baseline comparison. Move to **HPSS + drum removal** if drums are dominating a busy mix and crowding out the harmony.
**Default:** HPSS.

#### HPSS margin (advanced)
How aggressively to split harmonic from percussive content. 1.0 = gentle, 3.0 = default, 6.0+ = aggressive (may also strip sustained harmonic material). Only adjust if HPSS is misfiring.
**Default:** 3.0.

### Anchor chord roots to bass stem
Uses the isolated bass stem to **fix wrong chord roots**. When the bass clearly plays one note and that pitch disagrees with what the chord model picked, swap the root (keeping the same chord quality). Corrects relative-minor confusions and inversions-labelled-as-the-bass-note's-chord — the two error classes the chord model gets wrong most often.

Requires Stem Splitting (Demucs runs before chord detection, ~30 s – 2 min). The option is greyed out otherwise. Disabling Stem Splitting after this is on auto-clears it.

**When to change:** turn **On** for any song where you suspect root-detection errors (e.g. a clear minor song coming out as the relative major, or a slash-chord progression labelled as plain triads).
**Default:** Off.

#### Bass-anchor margin (advanced)
How clean the bass has to be before it's allowed to override the chord. 0.55 = default; 0.95 = only override on a single, completely unambiguous bass note. Lower it on bass-heavy mixes; raise it if you see spurious overrides on walking bass lines.
**Default:** 0.55.

### Detect slash chords (C/E, G/B, Am/C)
When the bass plays a chord tone that **isn't** the root (the 3rd or 5th), label the chord as an inversion. Pop songs use descending bass lines like `C – G/B – Am – F` everywhere; without this they all read as plain triads.

Reuses the bass stem loaded by *Anchor chord roots to bass stem*, so essentially free if you already have stems running. Requires Stem Splitting enabled.

**When to change:** turn **On** for any song with a noticeable bass line and you want the chart to capture inversion structure.
**Default:** Off.

### Force same-named sections to share progressions
If your song has, say, two Choruses and the chord chart shows slightly different progressions for them, this collapses the differences. For each section label that appears more than once, copies the highest-confidence chord progression to all instances. Pure post-processing — costs nothing.

Requires **Detect song sections** to be on. Skips instances of differing bar length (re-aligning a 6-bar chorus with an 8-bar chorus is too risky to do silently).

**When to change:** turn **On** for radio-friendly pop/rock with clear repeating sections. Leave **Off** for free-form material where each verse is genuinely different.
**Default:** Off.

### Smooth chord sequence with key-aware Viterbi
Replaces the per-bar greedy chord pick with a sequence-level decode that prefers music-theoretic transitions (V→I cadences over a stray Bdim in C major, etc.). Catches one-off misdetections nothing else catches — the chord engine sees each bar independently, so a single wrong bar in an otherwise-consistent progression is invisible to it.

No stems required. Bars with explicit mid-bar splits are left alone — those are explicit fine-grained detections we don't want to flatten.

**When to change:** turn **On** for any tonal song (most pop / rock / folk / classical). Leave **Off** for highly chromatic or experimental material where the key prior would fight against the real harmony.
**Default:** Off.

#### Viterbi stay-prob (advanced)
How "sticky" the smoother is on the current chord. 0.35 = default. Higher = more reluctant to switch chords (cleaner but may miss fast changes); lower = switches more freely.
**Default:** 0.35. **Range:** 0.05–0.95.

#### Viterbi cadence boost (advanced)
How much extra weight is given to classical cadences (V→I, IV→I, ii→V in major; v→i, iv→i in minor). 4.0 = default. Higher = stronger pull toward expected cadences; lower = transitions are judged purely on their in-key-ness.
**Default:** 4.0. **Range:** 1.0–20.0.

### Keep 7th qualities (maj7, m7, dom7)
Normally the chart simplifies seventh chords to plain major/minor for readability. Tick this to preserve the seventh quality.
**When to change:** on jazz, R&B, or any material where the 7ths are essential to the harmony.
**Default:** Off.

### Secondary model for low-confidence bars
When the primary chord model isn't sure about a bar, re-analyse it with a second, slower model and pick the more confident answer.
**Default:** On.

### Refine key using chord frequencies
After picking a key from the audio, count which chord roots appear most often and use that to break ties between closely related keys (e.g. F minor vs Ab major).
**When to change:** if the detected key is consistently the relative major/minor of the actual key.
**Default:** Off.

### Snap out-of-key chords to diatonic
For low-confidence bars whose chord doesn't belong to the detected key, snap to the nearest in-key chord.
**When to change:** if the chart shows oddly out-of-key chords on a clearly diatonic song.
**Default:** Off.

### Force half-time
Tells the chord engine that the detected tempo is twice the musical tempo (the beat tracker locked onto eighth-notes). The chart then treats every other beat as the downbeat.
**When to change:** when slow ballads or half-time grooves come out at 2× tempo with chords changing too fast.
**Default:** Off (auto-detected in many cases).

### Force 6/8 compound feel
Render in 6/8 even if the detector picks 3/4. They sound similar but feel different on the page.
**Default:** Off.

### Detect song sections (A/B/C marks)
Run structural analysis (allin1) on the audio and label sections — Intro/A/B/C boxes appear above the chart.
**When to change:** turn on for clear pop/rock structure. Turn off if section detection misfires and the rehearsal marks distract.
**Default:** Off.

#### Section boundary threshold (0–1)
Minimum boundary-strength score for allin1 to accept a section cut. **Blank** (or 0) accepts every local peak (more sections, sometimes too many); around **0.3–0.5** keeps only confident splits. Only adjust if sections are appearing in obviously wrong places — usually the default behaviour is what you want.
**Default:** blank (use allin1's internal default).

### Bars per line
Layout — how many bars to print on each line of the chart.
**Default:** 4.

### Hide BPM / Hide key / Hide meter
Suppress those bits of metadata from the chart's subtitle.
**Default:** all shown.

### Quantize melody on lead sheets
When a melody line is on the chart, snap its rhythms to the beat grid. Off = preserve the performance's exact timing.
**Default:** On.

### Expert tuning — confidence thresholds

#### Mid-bar split threshold (0–1)
A bar can only contain a mid-bar chord change if that change's confidence is at least this. Higher = fewer mid-bar splits (cleaner chart, may miss real changes).
**Default:** 0.80.

#### Secondary-model threshold (0–1)
The bar's average confidence has to drop below this before the secondary model is consulted. Higher = the secondary model runs more often (slower, sometimes more accurate).
**Default:** 0.70.

#### Key-snap threshold (0–1)
Only bars below this average confidence are eligible for snapping to the key. Higher = more bars get snapped.
**Default:** 0.65.

### Library tuning — confidence, bar phase

#### Low-confidence flag (0–1)
Chords below this confidence are marked with a `?` in the JSON report and tallied in the warning summary. Doesn't affect what's shown on the PDF.
**Default:** 0.45.

#### Phase-align chord grid to bar downbeats
After detecting chord changes, slide the bar grid 0–N beats so chord changes line up with bar starts as much as possible. Off = use the raw beat-1 anchor without realignment.
**When to change:** turn off if the chart's bar starts are visibly drifting from the audio.
**Default:** On.

---

## 4. Stem Splitting

Separates the song into individual instrument tracks (vocals, drums, bass, etc.) using Demucs.

### Demucs model
Which separation model to use.

- **htdemucs_6s** — 6 stems (vocals, drums, bass, guitar, piano, other). Best coverage. Default.
- **htdemucs** — 4 stems (vocals, drums, bass, other). Faster.
- **htdemucs_ft** — 4 stems, fine-tuned. Often higher quality.
- **mdx_extra** — alternative 4-stem model.

**Default:** htdemucs_6s.

### Skip stems entirely
Skip stem splitting. Much faster — useful if you only need the chord chart.
**Default:** Off.

### Stems to include in ZIP
Untick a stem to leave it out of the downloadable ZIP. (The stem is still produced, just not bundled.)
**Default:** all 6 included.

### Library tuning — Demucs, presence detector, backing track

#### Demucs shifts (more = better, slower)
How many randomly-shifted passes to average together for better separation. Each shift roughly doubles processing time.
**Default:** 1. Try 2–5 for slightly better separation on critical material.

#### Demucs overlap 0–0.99
How much consecutive processing chunks overlap. Higher = smoother transitions but slower.
**Default:** 0.25.

#### Demucs jobs (0 = auto)
Number of parallel worker processes. 0 lets Demucs pick.
**Default:** 0.

#### Demucs segment seconds (0 = full)
Process the song in fixed-length chunks of this many seconds. 0 = use the full file as one segment. Smaller chunks use less memory.
**Default:** 0.

#### Demucs device
Where to run inference. Auto picks the best available.
**Default:** Auto.

#### 24-bit WAV stems
Write stems as 24-bit instead of 16-bit. Larger files; minor quality benefit only relevant if you'll process them further.
**Default:** Off (16-bit).

#### MP3 stems
Save stems as MP3 instead of WAV. Much smaller files; lossy.
**Default:** Off.

#### Presence dBFS threshold
A stem is flagged as "actually present" only if it has sustained energy above this level. Lower (more negative) = more sensitive (more stems counted as present). Higher = stricter (only loud parts count).
**When to change:** lower it if a quiet but present part is being marked absent.
**Default:** −30 dBFS.

#### Presence window (s)
How long each RMS measurement window is. Smaller = catches brief loud moments. Larger = needs sustained loudness to register.
**Default:** 1.0 s.

#### Presence min run (s)
A stem has to be loud for at least this many consecutive seconds before it counts as present. Stops short bursts of bleed from being misread as a real part.
**Default:** 2.0 s.

#### Backing track peak ceiling (dBFS)
When mixing the backing track (all stems except the session instrument), normalise so the loudest sample sits this far below clipping.
**Default:** −1 dBFS.

#### Backing track bit depth
WAV bit depth for the mixed backing track.
**Default:** 24-bit.

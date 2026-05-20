"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

// ---------------------------------------------------------------------------
// Advanced settings — behavioural knobs most users never need to touch.
// AdvancedState carries the whole settings payload (including SongInfo fields)
// because they share localStorage and submit together.
//
// Organised by pipeline stage:
//   1. Beat Detection
//   2. Beat Stabilizer
//   3. Chord Detection
//   4. Stem Splitting
//
// Each subsection has a Primary block (always visible inside the section) and
// a "Library tuning" expand for the long-tail knobs that pass straight through
// to the underlying library (madmom / librosa / pyrubberband / allin1 / demucs).
// ---------------------------------------------------------------------------

export type AdvancedState = {
  sessionType: string;

  // Song info (edited by <SongInfo>)
  title: string;
  subtitle: string;
  bpm: string;
  key: string;
  timeSig: string;
  genre: string;

  // Beat Detection
  beatsPerBar: string;
  detectorBackend: string;
  madmomBpbOptions: string;
  madmomFps: string;
  madmomTimeoutS: string;
  librosaStartBpm: string;
  librosaTightness: string;
  librosaHopLength: string;
  tsWindowFactor: string;

  // Beat Stabilizer
  strength: string;
  trimIntro: boolean;
  skipStabilize: boolean;
  allowTempoChange: boolean;
  introTrimBars: string;
  tempoChangeWindowBars: string;
  tempoChangePersistBars: string;
  tempoChangeThresholdPct: string;
  tempoChangeThresholdFloor: string;
  pyrbCrispness: string;

  // Chord Detection
  barsPerLine: string;
  noBpm: boolean;
  noKey: boolean;
  noMeter: boolean;
  add7th: boolean;
  midBarThreshold: string;
  madmomFallback: boolean;
  madmomThreshold: string;
  keyTiebreak: boolean;
  keySnap: boolean;
  keySnapThreshold: string;
  halfTime: boolean;
  compound: boolean;
  detectSections: boolean;
  sectionThreshold: string;
  confidenceWarn: string;
  barPhase: boolean;
  // HPSS preprocessing for chord detection. "off" | "hpss" | "hpss-no-drums".
  // Mode 3 is only valid when stems are enabled — UI disables it otherwise and
  // skipStems-onChange auto-downgrades to "hpss".
  hpssMode: string;
  hpssMargin: string;
  deleteOnDownload: boolean;

  // Stem Splitting
  skipStems: boolean;
  stemVocals: boolean;
  stemDrums: boolean;
  stemBass: boolean;
  stemGuitar: boolean;
  stemPiano: boolean;
  stemOther: boolean;
  stemModel: string;
  demucsShifts: string;
  demucsOverlap: string;
  demucsJobs: string;
  demucsSegment: string;
  demucsDevice: string;
  demucsInt24: boolean;
  demucsMp3: boolean;
  presenceDb: string;
  presenceWindowS: string;
  presenceRunS: string;
  backingPeakDbfs: string;
  backingBitDepth: string;

  // Melody (lead sheets)
  quantizeMelody: boolean;
};

export const DEFAULT_ADVANCED: AdvancedState = {
  sessionType: "",

  title: "",
  subtitle: "",
  bpm: "",
  key: "auto",
  timeSig: "",
  genre: "auto",

  // Beat Detection
  beatsPerBar: "auto",
  detectorBackend: "auto",
  madmomBpbOptions: "3,4",
  madmomFps: "100",
  madmomTimeoutS: "240",
  librosaStartBpm: "120",
  librosaTightness: "100",
  librosaHopLength: "512",
  tsWindowFactor: "0.15",

  // Beat Stabilizer
  strength: "1.0",
  trimIntro: true,
  skipStabilize: false,
  allowTempoChange: false,
  introTrimBars: "1",
  tempoChangeWindowBars: "8",
  tempoChangePersistBars: "4",
  tempoChangeThresholdPct: "0.06",
  tempoChangeThresholdFloor: "6",
  pyrbCrispness: "",

  // Chord Detection
  barsPerLine: "4",
  noBpm: false,
  noKey: false,
  noMeter: false,
  add7th: false,
  midBarThreshold: "0.80",
  madmomFallback: false,
  madmomThreshold: "0.70",
  keyTiebreak: false,
  keySnap: false,
  keySnapThreshold: "0.65",
  halfTime: false,
  compound: false,
  detectSections: false,
  sectionThreshold: "",
  confidenceWarn: "0.45",
  barPhase: true,
  hpssMode: "hpss",
  hpssMargin: "3.0",
  deleteOnDownload: false,

  // Stem Splitting
  skipStems: false,
  stemVocals: true,
  stemDrums: true,
  stemBass: true,
  stemGuitar: true,
  stemPiano: true,
  stemOther: true,
  stemModel: "htdemucs_6s",
  demucsShifts: "1",
  demucsOverlap: "0.25",
  demucsJobs: "0",
  demucsSegment: "0",
  demucsDevice: "auto",
  demucsInt24: false,
  demucsMp3: false,
  presenceDb: "-30",
  presenceWindowS: "1.0",
  presenceRunS: "2.0",
  backingPeakDbfs: "-1.0",
  backingBitDepth: "24",

  quantizeMelody: true,
};

const STRENGTH_OPTIONS: Array<[string, string]> = [
  ["1.0",  "Full lock — every beat snaps to grid"],
  ["0.75", "Mostly locked"],
  ["0.5",  "Half"],
  ["0.25", "Light touch"],
  ["0",    "No warp — leave timing as-is"],
];

const BEATS_PER_BAR_OPTIONS: Array<[string, string]> = [
  ["auto", "Auto-detect"],
  ["2",    "2"],
  ["3",    "3"],
  ["4",    "4"],
  ["6",    "6"],
];

const BARS_PER_LINE_OPTIONS: Array<[string, string]> = [
  ["2", "2"],
  ["3", "3"],
  ["4", "4 — default"],
  ["6", "6"],
  ["8", "8"],
];

const STEM_MODEL_OPTIONS: Array<[string, string]> = [
  ["htdemucs_6s", "htdemucs_6s — 6 stems (default)"],
  ["htdemucs",    "htdemucs — 4 stems, fastest"],
  ["htdemucs_ft", "htdemucs_ft — 4 stems, fine-tuned"],
  ["mdx_extra",   "mdx_extra — alternative"],
];

const DETECTOR_BACKEND_OPTIONS: Array<[string, string]> = [
  ["auto",    "Auto (madmom, then librosa)"],
  ["madmom",  "Force madmom"],
  ["librosa", "Force librosa"],
];

const DEMUCS_DEVICE_OPTIONS: Array<[string, string]> = [
  ["auto", "Auto"],
  ["cpu",  "CPU"],
  ["cuda", "CUDA (NVIDIA GPU)"],
  ["mps",  "MPS (Apple silicon)"],
];

const BACKING_BIT_DEPTH_OPTIONS: Array<[string, string]> = [
  ["16", "16-bit"],
  ["24", "24-bit — default"],
  ["32", "32-bit float"],
];

type Props = {
  value: AdvancedState;
  onChange: (next: AdvancedState) => void;
  disabled?: boolean;
};

export function AdvancedSettings({ value, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false);

  function patch<K extends keyof AdvancedState>(key: K, v: AdvancedState[K]) {
    onChange({ ...value, [key]: v });
  }

  return (
    <div className="bg-ivory border border-warm-100">
      {/* Toggle header */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-5 py-3 text-left"
      >
        <span className="font-inter text-[10px] font-medium uppercase tracking-[0.12em] text-[#888888]">
          Advanced settings
        </span>
        <span className="flex items-center gap-1.5 font-inter text-xs text-[#888888]">
          {open ? "Hide" : "Defaults work for most songs"}
          {open
            ? <ChevronDown size={14} strokeWidth={2} />
            : <ChevronRight size={14} strokeWidth={2} />}
        </span>
      </button>

      {open && (
        <fieldset disabled={disabled} className="px-5 pb-2 divide-y divide-warm-200 border-t border-warm-100">

          {/* ─── 1. Beat Detection ─── */}
          <Section
            title="Beat Detection"
            intro="Finds beats, downbeats, and the time signature in the source audio."
          >
            <Row>
              <SelectField
                label="Beats per bar"
                value={value.beatsPerBar}
                onChange={(v) => patch("beatsPerBar", v)}
                options={BEATS_PER_BAR_OPTIONS}
              />
              <SelectField
                label="Detector backend"
                value={value.detectorBackend}
                onChange={(v) => patch("detectorBackend", v)}
                options={DETECTOR_BACKEND_OPTIONS}
              />
            </Row>
            <Row>
              <TextField
                label="Candidate beats-per-bar (CSV)"
                value={value.madmomBpbOptions}
                onChange={(v) => patch("madmomBpbOptions", v)}
                placeholder="3,4"
              />
            </Row>
            <Hint>
              Auto tries madmom first (better downbeats), then falls back to librosa. Forcing
              one is useful when madmom mistracks or you need a faster run.
            </Hint>

            <LibraryKnobs label="Library tuning — madmom &amp; librosa">
              <Row>
                <NumberField label="madmom fps (Hz)"           value={value.madmomFps}          onChange={(v) => patch("madmomFps", v)} step="1" />
                <NumberField label="madmom timeout (s)"        value={value.madmomTimeoutS}     onChange={(v) => patch("madmomTimeoutS", v)} step="10" />
              </Row>
              <Row>
                <NumberField label="librosa start BPM"         value={value.librosaStartBpm}    onChange={(v) => patch("librosaStartBpm", v)} step="1" />
                <NumberField label="librosa tightness"         value={value.librosaTightness}   onChange={(v) => patch("librosaTightness", v)} step="1" />
                <NumberField label="librosa hop length"        value={value.librosaHopLength}   onChange={(v) => patch("librosaHopLength", v)} step="64" />
              </Row>
              <Row>
                <NumberField label="Time-sig autocorr window"  value={value.tsWindowFactor}     onChange={(v) => patch("tsWindowFactor", v)} step="0.01" />
              </Row>
            </LibraryKnobs>
          </Section>

          {/* ─── 2. Beat Stabilizer ─── */}
          <Section
            title="Beat Stabilizer"
            intro="Warps the audio so every beat lands on an even tempo grid, then trims the intro to bar 1."
          >
            <Row>
              <SelectField
                label="Warp strength"
                value={value.strength}
                onChange={(v) => patch("strength", v)}
                options={STRENGTH_OPTIONS}
              />
            </Row>
            <Row>
              <Check label="Trim intro to one bar before beat 1" checked={value.trimIntro}        onChange={(v) => patch("trimIntro", v)} />
              <Check label="Skip stabilization entirely"          checked={value.skipStabilize}    onChange={(v) => patch("skipStabilize", v)} />
            </Row>
            <Row>
              <Check
                label="Allow tempo change (skip the multi-tempo guard)"
                checked={value.allowTempoChange}
                onChange={(v) => patch("allowTempoChange", v)}
              />
            </Row>
            <Hint>
              By default, processing stops if a sustained tempo change is detected — a single-tempo
              warp would mangle audio across the boundary. Enable the guard override to proceed anyway.
            </Hint>

            <LibraryKnobs label="Library tuning — intro trim, tempo guard, rubberband">
              <Row>
                <NumberField label="Intro trim (bars)" value={value.introTrimBars} onChange={(v) => patch("introTrimBars", v)} step="1" />
                <NumberField label="rubberband crispness 0–6 (blank = library default)" value={value.pyrbCrispness} onChange={(v) => patch("pyrbCrispness", v)} step="1" />
              </Row>
              <Row>
                <NumberField label="Tempo-change window (bars)"       value={value.tempoChangeWindowBars}    onChange={(v) => patch("tempoChangeWindowBars", v)} step="1" />
                <NumberField label="Tempo-change persistence (bars)"  value={value.tempoChangePersistBars}   onChange={(v) => patch("tempoChangePersistBars", v)} step="1" />
              </Row>
              <Row>
                <NumberField label="Tempo-change threshold (fraction)" value={value.tempoChangeThresholdPct}   onChange={(v) => patch("tempoChangeThresholdPct", v)} step="0.01" />
                <NumberField label="Tempo-change minimum step (BPM)"   value={value.tempoChangeThresholdFloor} onChange={(v) => patch("tempoChangeThresholdFloor", v)} step="0.5" />
              </Row>
            </LibraryKnobs>
          </Section>

          {/* ─── 3. Chord Detection ─── */}
          <Section
            title="Chord Detection"
            intro="Detects chords on the beat grid and renders a PDF + MusicXML lead sheet."
          >
            <Row>
              <SelectField
                label="Chord input cleaning (HPSS)"
                value={value.hpssMode}
                onChange={(v) => patch("hpssMode", v)}
                options={[
                  ["off",  "Off — full mix"],
                  ["hpss", "HPSS — strip drum transients (default)"],
                  ["hpss-no-drums",
                    value.skipStems
                      ? "HPSS + drum removal — enable Stem Splitting first"
                      : "HPSS + drum removal (best quality, adds Demucs time)",
                    value.skipStems],
                ]}
              />
            </Row>
            <Hint>
              HPSS = Harmonic-Percussive Source Separation. Operates on the time-aligned audio
              before crema sees it. Key and beat detection still use the raw signal.
            </Hint>
            <Row>
              <Check label="Keep 7th qualities (maj7, m7, dom7)"     checked={value.add7th}          onChange={(v) => patch("add7th", v)} />
              <Check label="Secondary model for low-confidence bars (slower)" checked={value.madmomFallback}  onChange={(v) => patch("madmomFallback", v)} />
            </Row>
            <Row>
              <Check label="Refine key using chord frequencies" checked={value.keyTiebreak} onChange={(v) => patch("keyTiebreak", v)} />
              <Check label="Snap out-of-key chords to diatonic" checked={value.keySnap}     onChange={(v) => patch("keySnap", v)} />
            </Row>

            <SubLabel>Rhythm overrides</SubLabel>
            <Row>
              <Check label="Force half-time"             checked={value.halfTime} onChange={(v) => patch("halfTime", v)} />
              <Check label="Force 6/8 compound feel"     checked={value.compound} onChange={(v) => patch("compound", v)} />
            </Row>
            <Row>
              <Check label="Detect song sections (Intro/Verse/Chorus…)" checked={value.detectSections} onChange={(v) => patch("detectSections", v)} />
            </Row>
            {value.detectSections && (
              <>
                <Row>
                  <NumberField
                    label="Section boundary threshold (0–1, blank = 0)"
                    value={value.sectionThreshold}
                    onChange={(v) => patch("sectionThreshold", v)}
                    step="0.05"
                  />
                </Row>
                <Hint>
                  Minimum boundary-strength score to accept a section cut. 0 = every local peak (more sections). Raise toward 0.5 for fewer, more confident splits. Only adjust if sections appear in the wrong places.
                </Hint>
              </>
            )}

            <SubLabel>Chart appearance</SubLabel>
            <Row>
              <SelectField
                label="Bars per line"
                value={value.barsPerLine}
                onChange={(v) => patch("barsPerLine", v)}
                options={BARS_PER_LINE_OPTIONS}
              />
            </Row>
            <Row>
              <Check label="Hide BPM"   checked={value.noBpm}   onChange={(v) => patch("noBpm", v)} />
              <Check label="Hide key"   checked={value.noKey}   onChange={(v) => patch("noKey", v)} />
              <Check label="Hide meter" checked={value.noMeter} onChange={(v) => patch("noMeter", v)} />
            </Row>
            <Row>
              <Check label="Quantize melody on lead sheets" checked={value.quantizeMelody} onChange={(v) => patch("quantizeMelody", v)} />
            </Row>

            {/* Existing confidence thresholds */}
            <ExpertKnobs>
              <Row>
                <NumberField label="Mid-bar split threshold (0–1)"     value={value.midBarThreshold}   onChange={(v) => patch("midBarThreshold", v)} />
                <NumberField label="Secondary-model threshold (0–1)"   value={value.madmomThreshold}   onChange={(v) => patch("madmomThreshold", v)} />
                <NumberField label="Key-snap threshold (0–1)"          value={value.keySnapThreshold}  onChange={(v) => patch("keySnapThreshold", v)} />
              </Row>
              {value.hpssMode !== "off" && (
                <Row>
                  <NumberField
                    label="HPSS margin (≥1, higher = stronger harmonic/percussive split)"
                    value={value.hpssMargin}
                    onChange={(v) => patch("hpssMargin", v)}
                    step="0.5"
                  />
                </Row>
              )}
              <Hint>
                Confidence thresholds. Only adjust if the chart consistently misfires on your music.
              </Hint>
            </ExpertKnobs>

            <LibraryKnobs label="Library tuning — confidence, bar phase">
              <Row>
                <NumberField label="Low-confidence flag (0–1)" value={value.confidenceWarn} onChange={(v) => patch("confidenceWarn", v)} />
                <Check label="Phase-align chord grid to bar downbeats" checked={value.barPhase} onChange={(v) => patch("barPhase", v)} />
              </Row>
            </LibraryKnobs>
          </Section>

          {/* ─── 4. Stem Splitting ─── */}
          <Section
            title="Stem Splitting"
            intro="Splits the song into separate WAV tracks using Demucs."
          >
            <Row>
              <SelectField
                label="Demucs model"
                value={value.stemModel}
                onChange={(v) => patch("stemModel", v)}
                options={STEM_MODEL_OPTIONS}
              />
            </Row>
            <Row>
              <Check
                label="Skip stems entirely (much faster)"
                checked={value.skipStems}
                onChange={(v) => {
                  // hpss-no-drums depends on stems; downgrade to plain HPSS
                  // when the user takes stems away so the run won't error out.
                  if (v && value.hpssMode === "hpss-no-drums") {
                    onChange({ ...value, skipStems: true, hpssMode: "hpss" });
                  } else {
                    patch("skipStems", v);
                  }
                }}
              />
            </Row>

            <SubLabel>Stems to include in ZIP</SubLabel>
            <Row>
              <Check label="Vocals" checked={value.stemVocals} onChange={(v) => patch("stemVocals", v)} />
              <Check label="Drums"  checked={value.stemDrums}  onChange={(v) => patch("stemDrums", v)} />
              <Check label="Bass"   checked={value.stemBass}   onChange={(v) => patch("stemBass", v)} />
              <Check label="Guitar" checked={value.stemGuitar} onChange={(v) => patch("stemGuitar", v)} />
              <Check label="Piano"  checked={value.stemPiano}  onChange={(v) => patch("stemPiano", v)} />
              <Check label="Other"  checked={value.stemOther}  onChange={(v) => patch("stemOther", v)} />
            </Row>
            <Hint>
              All stems are produced regardless; unchecking one removes it from the ZIP only.
            </Hint>

            <LibraryKnobs label="Library tuning — Demucs, presence detector, backing track">
              <Row>
                <NumberField label="Demucs shifts (more = better, slower)" value={value.demucsShifts}  onChange={(v) => patch("demucsShifts", v)} step="1" />
                <NumberField label="Demucs overlap 0–0.99"                 value={value.demucsOverlap} onChange={(v) => patch("demucsOverlap", v)} step="0.05" />
              </Row>
              <Row>
                <NumberField label="Demucs jobs (0 = auto)"                value={value.demucsJobs}    onChange={(v) => patch("demucsJobs", v)} step="1" />
                <NumberField label="Demucs segment seconds (0 = full)"     value={value.demucsSegment} onChange={(v) => patch("demucsSegment", v)} step="1" />
                <SelectField label="Demucs device"                         value={value.demucsDevice}  onChange={(v) => patch("demucsDevice", v)} options={DEMUCS_DEVICE_OPTIONS} />
              </Row>
              <Row>
                <Check label="24-bit WAV stems" checked={value.demucsInt24} onChange={(v) => patch("demucsInt24", v)} />
                <Check label="MP3 stems"        checked={value.demucsMp3}   onChange={(v) => patch("demucsMp3", v)} />
              </Row>

              <SubLabel>Stem presence detector</SubLabel>
              <Row>
                <NumberField label="Presence dBFS threshold"  value={value.presenceDb}      onChange={(v) => patch("presenceDb", v)} step="1" />
                <NumberField label="Presence window (s)"      value={value.presenceWindowS} onChange={(v) => patch("presenceWindowS", v)} step="0.1" />
                <NumberField label="Presence min run (s)"     value={value.presenceRunS}    onChange={(v) => patch("presenceRunS", v)} step="0.1" />
              </Row>

              <SubLabel>Backing track</SubLabel>
              <Row>
                <NumberField label="Peak ceiling (dBFS)"      value={value.backingPeakDbfs} onChange={(v) => patch("backingPeakDbfs", v)} step="0.5" />
                <SelectField label="Bit depth"                value={value.backingBitDepth} onChange={(v) => patch("backingBitDepth", v)} options={BACKING_BIT_DEPTH_OPTIONS} />
              </Row>
            </LibraryKnobs>
          </Section>

          {/* ─── 5. Storage ─── */}
          <Section
            title="Storage"
            intro="Controls how generated files are handled after a job completes."
          >
            <Row>
              <Check
                label="Delete files after download"
                checked={value.deleteOnDownload}
                onChange={(v) => patch("deleteOnDownload", v)}
              />
            </Row>
            <Hint>
              When enabled, all generated files for a job — stabilised audio, chord chart,
              stems, and ZIP — are permanently deleted from the server as soon as you
              download the ZIP. Useful when running locally to avoid filling up disk space.
              Without this, files are kept for 24 hours and then auto-deleted.
            </Hint>
          </Section>

        </fieldset>
      )}
    </div>
  );
}

// ---------- layout primitives ----------

function Section({ title, intro, children }: { title: string; intro: string; children: React.ReactNode }) {
  return (
    <div className="space-y-4 py-6">
      <div className="space-y-1">
        <p className="font-inter text-sm font-semibold uppercase tracking-[0.10em] text-ebony">{title}</p>
        <p className="font-inter text-xs text-[#6D6D6D] leading-relaxed">{intro}</p>
      </div>
      <div className="space-y-3">{children}</div>
    </div>
  );
}

function SubLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="font-inter text-[10px] font-medium uppercase tracking-[0.12em] text-[#B0B0B0] pt-1">
      {children}
    </p>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-wrap gap-x-5 gap-y-2 items-end">{children}</div>;
}

function Hint({ children }: { children: React.ReactNode }) {
  return <p className="font-inter text-xs text-[#888888] leading-relaxed">{children}</p>;
}

function ExpertKnobs({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="pt-1">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="font-inter text-xs text-[#888888] hover:text-[#454545] flex items-center gap-1 transition-colors"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        Expert tuning — confidence thresholds
      </button>
      {open && <div className="pt-3 space-y-2">{children}</div>}
    </div>
  );
}

function LibraryKnobs({ label, children }: { label: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="pt-1">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="font-inter text-xs text-[#888888] hover:text-[#454545] flex items-center gap-1 transition-colors"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {label}
      </button>
      {open && <div className="pt-3 space-y-3">{children}</div>}
    </div>
  );
}

// ---------- field primitives ----------

function NumberField({ label, value, onChange, step }: { label: string; value: string; onChange: (v: string) => void; step?: string }) {
  return (
    <label className="flex flex-col gap-1 min-w-[160px] flex-1">
      <span className="font-inter text-[10px] font-medium uppercase tracking-[0.12em] text-[#888888]">{label}</span>
      <input
        type="number"
        step={step ?? "0.05"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="font-season text-sm text-ebony bg-white border border-warm-200 px-2.5 py-2 outline-none focus:border-ebony transition-colors"
      />
    </label>
  );
}

function TextField({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <label className="flex flex-col gap-1 min-w-[160px] flex-1">
      <span className="font-inter text-[10px] font-medium uppercase tracking-[0.12em] text-[#888888]">{label}</span>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="font-season text-sm text-ebony bg-white border border-warm-200 px-2.5 py-2 outline-none focus:border-ebony transition-colors"
      />
    </label>
  );
}

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="inline-flex items-center gap-2 cursor-pointer font-season text-sm text-[#454545]">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="accent-ebony w-3.5 h-3.5"
      />
      <span>{label}</span>
    </label>
  );
}

// Each option is [value, label] or [value, label, disabled]. The third element
// (when true) renders the <option disabled> so the dropdown shows it greyed
// out — used by the HPSS dropdown to communicate "enable Stem Splitting first".
type SelectOption = [string, string] | [string, string, boolean];

function SelectField({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: Array<SelectOption> }) {
  return (
    <label className="flex flex-col gap-1 min-w-[200px] flex-1">
      <span className="font-inter text-[10px] font-medium uppercase tracking-[0.12em] text-[#888888]">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="font-season text-sm text-ebony bg-white border border-warm-200 px-2.5 py-2 outline-none focus:border-ebony transition-colors appearance-none"
      >
        {options.map(([v, l, disabled]) => (
          <option key={v} value={v} disabled={disabled === true}>{l}</option>
        ))}
      </select>
    </label>
  );
}

// ---------- payload mapping ----------

export function toSettingsPayload(a: AdvancedState) {
  const stems: string[] = [];
  if (a.stemVocals) stems.push("vocals");
  if (a.stemDrums)  stems.push("drums");
  if (a.stemBass)   stems.push("bass");
  if (a.stemGuitar) stems.push("guitar");
  if (a.stemPiano)  stems.push("piano");
  if (a.stemOther)  stems.push("other");
  const allStems = stems.length === 6;

  function num(v: string): number | undefined {
    const f = parseFloat(v);
    return Number.isFinite(f) ? f : undefined;
  }
  function int(v: string): number | undefined {
    const n = parseInt(v, 10);
    return Number.isFinite(n) ? n : undefined;
  }

  return {
    title:    a.title    || undefined,
    subtitle: a.subtitle || undefined,
    genre:    a.genre && a.genre !== "auto" ? a.genre : undefined,

    bpm:              num(a.bpm),
    strength:         num(a.strength),
    trimIntro:        a.trimIntro,
    beatsPerBar:      a.beatsPerBar === "auto" ? undefined : int(a.beatsPerBar),
    skipStabilize:    a.skipStabilize    || undefined,
    allowTempoChange: a.allowTempoChange || undefined,

    key:               a.key && a.key !== "auto" ? a.key : undefined,
    timeSig:           int(a.timeSig),
    barsPerLine:       int(a.barsPerLine),
    noBpm:             a.noBpm    || undefined,
    noKey:             a.noKey    || undefined,
    noMeter:           a.noMeter  || undefined,
    add7th:            a.add7th   || undefined,
    midBarThreshold:   num(a.midBarThreshold),
    madmomFallback:    a.madmomFallback,
    madmomThreshold:   num(a.madmomThreshold),
    keyTiebreak:       a.keyTiebreak  || undefined,
    keySnap:           a.keySnap      || undefined,
    keySnapThreshold:  num(a.keySnapThreshold),
    halfTime:          a.halfTime  || undefined,
    compound:          a.compound  || undefined,
    skipSections:      a.detectSections ? undefined : true,
    sectionThreshold:  num(a.sectionThreshold),
    deleteOnDownload:  a.deleteOnDownload || undefined,

    skipStems:    a.skipStems || undefined,
    stems:        a.skipStems || allStems ? undefined : stems,
    stemModel:    a.stemModel,
    sessionType:  a.sessionType || undefined,

    quantizeMelody: a.quantizeMelody ? undefined : false,

    // ── Beat-detector library knobs ──
    detectorBackend:  a.detectorBackend && a.detectorBackend !== "auto" ? a.detectorBackend : undefined,
    madmomBpbOptions: a.madmomBpbOptions && a.madmomBpbOptions !== "3,4" ? a.madmomBpbOptions : undefined,
    madmomFps:        int(a.madmomFps),
    madmomTimeoutS:   int(a.madmomTimeoutS),
    librosaStartBpm:  num(a.librosaStartBpm),
    librosaTightness: num(a.librosaTightness),
    librosaHopLength: int(a.librosaHopLength),
    tsWindowFactor:   num(a.tsWindowFactor),

    // ── Beat-stabilizer library knobs ──
    introTrimBars:                int(a.introTrimBars),
    tempoChangeWindowBars:        int(a.tempoChangeWindowBars),
    tempoChangePersistBars:       int(a.tempoChangePersistBars),
    tempoChangeThresholdPct:      num(a.tempoChangeThresholdPct),
    tempoChangeThresholdFloor:    num(a.tempoChangeThresholdFloor),
    pyrbCrispness:                a.pyrbCrispness === "" ? undefined : int(a.pyrbCrispness),

    // ── Chord-detection library knobs ──
    barPhase:           a.barPhase ? undefined : false,
    confidenceWarn:     num(a.confidenceWarn),
    // Default in pipeline.py is "hpss"; only forward when the user picked
    // something else.  Mode 3 is filtered out here when stems are disabled —
    // the UI prevents the selection, but be defensive against stale state.
    hpssMode:           (a.hpssMode === "hpss" || !a.hpssMode)
                          ? undefined
                          : (a.hpssMode === "hpss-no-drums" && a.skipStems
                              ? undefined
                              : (a.hpssMode as "off" | "hpss-no-drums")),
    hpssMargin:         num(a.hpssMargin),

    // ── Stem-splitting library knobs ──
    demucsShifts:      int(a.demucsShifts),
    demucsOverlap:     num(a.demucsOverlap),
    demucsJobs:        int(a.demucsJobs),
    demucsSegment:     int(a.demucsSegment),
    demucsDevice:      a.demucsDevice && a.demucsDevice !== "auto" ? a.demucsDevice : undefined,
    demucsInt24:       a.demucsInt24 || undefined,
    demucsMp3:         a.demucsMp3   || undefined,
    presenceDb:        num(a.presenceDb),
    presenceWindowS:   num(a.presenceWindowS),
    presenceRunS:      num(a.presenceRunS),
    backingPeakDbfs:   num(a.backingPeakDbfs),
    backingBitDepth:   int(a.backingBitDepth),
  };
}

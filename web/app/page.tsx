"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ModelWarningBanner } from "@/components/ModelWarningBanner";
import { DropZone } from "@/components/DropZone";
import { SongInfo } from "@/components/SongInfo";
import {
  AdvancedSettings,
  DEFAULT_ADVANCED,
  toSettingsPayload,
  type AdvancedState,
} from "@/components/AdvancedSettings";
import { STRUCTURE_LOCKED } from "@/lib/validation";

const STORAGE_KEY = "audio-tools.advanced.v1";

const SONG_FIELDS: Array<keyof AdvancedState> = [
  "title", "subtitle", "bpm", "key", "timeSig", "genre",
];

type AnalyzeResult = {
  bpm: number | null;
  key: string | null;
  timeSig: string | null;
  durationSeconds: number;
  filename: string;
  error?: string;
};

export default function UploadPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [advanced, setAdvanced] = useState<AdvancedState>(DEFAULT_ADVANCED);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState<AnalyzeResult | null>(null);
  const lastAnalyzedFileRef = useRef<File | null>(null);

  useEffect(() => {
    // Locked test-arm build: ignore saved advanced settings so the non-structural
    // knobs sit at their defaults (the structural settings are forced server-side
    // in pipeline.ts). Restores normal hydration when STRUCTURE_LOCKED is false.
    if (STRUCTURE_LOCKED) return;
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as Partial<AdvancedState>;
      setAdvanced((current) => ({ ...current, ...parsed }));
    } catch { /* bad JSON — keep defaults */ }
  }, []);

  useEffect(() => {
    try {
      const persisted: Partial<AdvancedState> = { ...advanced };
      for (const k of SONG_FIELDS) delete persisted[k];
      localStorage.setItem(STORAGE_KEY, JSON.stringify(persisted));
    } catch { /* quota / private mode */ }
  }, [advanced]);

  useEffect(() => {
    if (!file) {
      lastAnalyzedFileRef.current = null;
      setAnalysis(null);
      setAnalyzing(false);
      return;
    }
    if (lastAnalyzedFileRef.current === file) return;
    lastAnalyzedFileRef.current = file;

    let cancelled = false;
    setAnalyzing(true);
    setAnalysis(null);
    setError(null);

    void (async () => {
      const form = new FormData();
      form.append("file", file);
      try {
        const res = await fetch("/api/analyze", { method: "POST", body: form });
        if (cancelled) return;
        if (!res.ok) {
          const text = await res.text().catch(() => "");
          setError(`Quick analysis failed (${res.status}). ${text}`);
          setAnalysis({ bpm: null, key: null, timeSig: null, durationSeconds: 0, filename: file.name });
        } else {
          const data = (await res.json()) as AnalyzeResult;
          setAnalysis(data);
          const baseTitle = file.name.replace(/\.[^.]+$/, "");
          setAdvanced((current) => ({
            ...current,
            title:   baseTitle,
            key:     data.key     ?? "auto",
            bpm:     data.bpm     ? String(data.bpm) : "",
            timeSig: timeSigToDropdownValue(data.timeSig),
          }));
        }
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Network error during analysis.");
        setAnalysis({ bpm: null, key: null, timeSig: null, durationSeconds: 0, filename: file.name });
      } finally {
        if (!cancelled) setAnalyzing(false);
      }
    })();

    return () => { cancelled = true; };
  }, [file]);

  async function onSubmit() {
    if (!file) return;
    setBusy(true);
    setError(null);

    const form = new FormData();
    form.append("file", file);
    form.append("settings", JSON.stringify(toSettingsPayload(advanced)));

    try {
      const res = await fetch("/api/jobs", { method: "POST", body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(typeof data.error === "string" ? data.error : `Upload failed (${res.status}).`);
        setBusy(false);
        return;
      }
      router.push(`/jobs/${data.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error.");
      setBusy(false);
    }
  }

  const ready = file !== null && analysis !== null;
  const formDisabled = busy || analyzing;

  return (
    <main className="min-h-screen bg-white flex justify-center px-4 py-12">
      <div className="w-full max-w-xl space-y-5">

        {/* Model availability warning */}
        <ModelWarningBanner />

        {/* Header */}
        <header className="space-y-3">
          <h1 className="font-display text-[36px] font-bold text-ebony leading-none">
            Session Materials Creator
          </h1>
          <p className="font-inter text-sm text-[#6D6D6D] leading-relaxed">
            Drop an audio file. Get back a beat-stabilized WAV, a chord chart PDF, and isolated stems — packaged as a ZIP.
          </p>
        </header>

        {/* Drop zone */}
        <DropZone file={file} onFile={setFile} disabled={busy} />

        {/* Session type selector */}
        {file && (
          <SessionTypeCard
            value={advanced.sessionType}
            onChange={(v) => setAdvanced((a) => ({ ...a, sessionType: v }))}
            disabled={formDisabled}
          />
        )}

        {/* Analyzing state */}
        {analyzing && <AnalyzingCard filename={file?.name ?? ""} />}

        {/* Analysis warning (partial failure) */}
        {ready && analysis?.error && (
          <Callout variant="warning">
            Auto-detection didn&apos;t complete — fill in the song info manually below.
          </Callout>
        )}

        {/* Settings panels */}
        {ready && (
          <>
            <SongInfo value={advanced} onChange={setAdvanced} disabled={formDisabled} />
            <AdvancedSettings value={advanced} onChange={setAdvanced} disabled={formDisabled} />
          </>
        )}

        {/* Submit error */}
        {error && (
          <Callout variant="error">{error}</Callout>
        )}

        {/* CTA */}
        {file && (
          <button
            onClick={onSubmit}
            disabled={!ready || busy}
            className={[
              "w-full font-season text-base font-semibold px-6 py-3.5 rounded-full transition-colors",
              !ready || busy
                ? "bg-[#E7E5E0] text-[#999682] cursor-not-allowed"
                : "bg-ebony text-ivory hover:bg-[#222222]",
            ].join(" ")}
          >
            {busy
              ? "Uploading…"
              : analyzing
                ? "Analyzing file…"
                : "Process audio"}
          </button>
        )}

        {/* Footer */}
        <p className="font-inter text-xs text-[#B0B0B0] text-center pb-4">
          Files are processed locally. Job artifacts are deleted after 24 hours.
        </p>
      </div>
    </main>
  );
}

// ---------- session type ----------

const SESSION_TYPES: Array<{ value: string; label: string }> = [
  { value: "vocals", label: "Vocals" },
  { value: "guitar", label: "Guitar" },
  { value: "bass",   label: "Bass"   },
  { value: "piano",  label: "Piano"  },
  { value: "other",  label: "Other"  },
];

function SessionTypeCard({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="bg-ivory border border-warm-100 px-5 py-4 space-y-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-inter text-[10px] font-medium uppercase tracking-[0.12em] text-[#888888]">
          Session type
        </span>
        <span className="font-inter text-xs text-[#888888]">
          {value
            ? `Backing track = all stems minus ${value}`
            : "Select to generate a backing track"}
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        {SESSION_TYPES.map(({ value: v, label }) => (
          <button
            key={v}
            type="button"
            disabled={disabled}
            onClick={() => onChange(value === v ? "" : v)}
            className={[
              "font-season text-sm font-medium px-4 py-1.5 rounded-full border transition-colors",
              value === v
                ? "bg-ebony text-ivory border-ebony"
                : "bg-white text-[#454545] border-[#D1CFC5] hover:border-[#999682]",
              disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer",
            ].join(" ")}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}

// ---------- helpers ----------

function timeSigToDropdownValue(s: string | null): string {
  if (!s) return "";
  const m = /^(\d+)\/(\d+)$/.exec(s);
  if (!m) return "";
  return m[1] === "6" && m[2] === "8" ? "6" : m[1];
}

function AnalyzingCard({ filename }: { filename: string }) {
  return (
    <div className="bg-ivory border border-warm-100 border-l-4 border-l-brand-yellow px-4 py-4 flex items-center gap-3">
      <Spinner />
      <div className="min-w-0 flex-1">
        <p className="font-season text-sm font-semibold text-ebony">Analyzing file…</p>
        <p className="font-inter text-xs text-[#888888] truncate mt-0.5">
          BPM, key, and meter detection on {filename}
        </p>
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <span
      aria-hidden
      className="flex-shrink-0 inline-block w-4 h-4 rounded-full border-2 border-[#D1CFC5] border-t-brand-yellow animate-spin"
    />
  );
}

type CalloutVariant = "warning" | "error" | "info";

function Callout({ variant, children }: { variant: CalloutVariant; children: React.ReactNode }) {
  const styles: Record<CalloutVariant, string> = {
    warning: "bg-brand-yellow-50 border-l-[#F3A00D] text-[#774310]",
    error:   "bg-brand-pink-50   border-l-brand-pink  text-[#78293A]",
    info:    "bg-brand-blue-50   border-l-[#29B3C3]   text-[#22505B]",
  };
  return (
    <div className={`border border-[#E7E5E0] border-l-4 px-4 py-3 font-inter text-sm ${styles[variant]}`}>
      {children}
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";

type ModelStatus = {
  id: string;
  label: string;
  present: boolean;
  note?: string;
};

export function ModelWarningBanner() {
  const [missing, setMissing] = useState<ModelStatus[]>([]);

  useEffect(() => {
    fetch("/api/models")
      .then((r) => r.json())
      .then((data) => {
        const absent = (data.models as ModelStatus[]).filter((m) => !m.present);
        setMissing(absent);
      })
      .catch(() => {});
  }, []);

  if (missing.length === 0) return null;

  return (
    <div className="rounded border border-amber-300 bg-amber-50 px-4 py-3 text-sm font-inter text-amber-900 space-y-1">
      <p className="font-semibold">Some model weights are not downloaded yet:</p>
      <ul className="list-disc list-inside space-y-0.5 text-amber-800">
        {missing.map((m) => (
          <li key={m.id}>
            <span className="font-medium">{m.label}</span>
            {m.note && <span className="text-amber-700"> — {m.note}</span>}
          </li>
        ))}
      </ul>
      <p className="text-amber-700 text-xs pt-1">
        Missing models download automatically on first use but will add time to that job.
      </p>
    </div>
  );
}

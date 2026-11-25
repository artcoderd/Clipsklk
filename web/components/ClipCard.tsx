"use client";

import { useState } from "react";

import { fmtTime, REASON_LABELS, type Clip } from "@/lib/api";

async function shareClip(clip: Clip, title: string) {
  // Native share sheet (Instagram, TikTok, WhatsApp...) with the actual file when supported.
  try {
    const res = await fetch(`/api/clips/${clip.id}/video`);
    const blob = await res.blob();
    const file = new File([blob], `clipeo-clip${clip.index}.mp4`, { type: "video/mp4" });
    if (navigator.canShare?.({ files: [file] })) {
      await navigator.share({ files: [file], title });
      return true;
    }
  } catch (e) {
    if ((e as Error).name === "AbortError") return true; // user closed the sheet
  }
  return false;
}

export function ClipCard({ clip, title }: { clip: Clip; title: string }) {
  const [sharing, setSharing] = useState(false);
  const canShare = typeof navigator !== "undefined" && "share" in navigator;

  return (
    <article className="glass overflow-hidden rounded-3xl">
      <video
        src={`/api/clips/${clip.id}/video`}
        poster={`/api/clips/${clip.id}/thumbnail`}
        controls
        playsInline
        preload="none"
        className="aspect-video w-full bg-black object-contain"
      />
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="rounded-full bg-white/10 px-2 py-0.5 text-xs font-semibold">#{clip.index}</span>
          {clip.reasons.map((r) => (
            <span key={r} className="rounded-full bg-fuchsia-500/15 px-2 py-0.5 text-xs text-fuchsia-200">
              {REASON_LABELS[r] ?? r}
            </span>
          ))}
          <span className="ml-auto text-xs text-white/50">
            {fmtTime(clip.start)}–{fmtTime(clip.end)} · {Math.round(clip.duration)}s
          </span>
        </div>
        {clip.text && <p className="line-clamp-3 text-sm text-white/70">“{clip.text}”</p>}
        <div className="flex gap-2">
          <a
            href={`/api/clips/${clip.id}/video?download=1`}
            className="flex min-h-11 flex-1 items-center justify-center rounded-2xl bg-white/10 text-sm font-semibold hover:bg-white/15"
          >
            Descargar
          </a>
          {canShare && (
            <button
              disabled={sharing}
              onClick={async () => {
                setSharing(true);
                await shareClip(clip, title);
                setSharing(false);
              }}
              className="accent-gradient min-h-11 flex-1 rounded-2xl text-sm font-semibold disabled:opacity-60"
            >
              {sharing ? "Preparando…" : "Compartir"}
            </button>
          )}
        </div>
      </div>
    </article>
  );
}

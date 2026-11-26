"use client";

import Link from "next/link";
import { useState } from "react";

import { fmtTime, LAYOUT_LABELS, PLATFORM_ICON, PUB_ERRORS, PUB_STATUS, REASON_LABELS, type Clip, type Publication,
         type SocialAccount } from "@/lib/api";
import { ClipText } from "./ClipText";

async function shareClip(clip: Clip, title: string) {
  // Native share sheet (Instagram, TikTok, WhatsApp...) with the actual file when supported.
  try {
    const res = await fetch(`/api/clips/${clip.id}/video?v=${clip.version}`);
    const blob = await res.blob();
    const file = new File([blob], `clipeo-clip${clip.index}.mp4`, { type: "video/mp4" });
    if (navigator.canShare?.({ files: [file] })) {
      const text = [clip.caption, clip.hashtags.map((t) => `#${t}`).join(" ")].filter(Boolean).join("\n\n");
      await navigator.share({ files: [file], title: clip.title || title, text });
      return true;
    }
  } catch (e) {
    if ((e as Error).name === "AbortError") return true; // user closed the sheet
  }
  return false;
}

const ASPECT: Record<string, string> = { "9:16": "9 / 16", "4:5": "4 / 5", "1:1": "1 / 1", "16:9": "16 / 9" };

export function ClipCard({ clip, title, onEdit, onChange, aiAvailable = false, pubs = [], accounts = [], onPublish }: {
  clip: Clip; title: string; onEdit?: () => void; onChange?: (c: Clip) => void; aiAvailable?: boolean;
  pubs?: Publication[]; accounts?: SocialAccount[]; onPublish?: () => void;
}) {
  const [sharing, setSharing] = useState(false);
  const canShare = typeof navigator !== "undefined" && "share" in navigator;
  const v = `v=${clip.version}`;   // new URL after each re-render (no stale cache)
  const rendering = clip.render_status === "rendering";

  return (
    <article className="glass overflow-hidden rounded-3xl">
      <div className="relative bg-black" style={{ aspectRatio: ASPECT[clip.render?.format ?? "9:16"] ?? "9 / 16" }}>
        {clip.version > 0 && (
          <video
            key={clip.version}
            src={`/api/clips/${clip.id}/video?${v}`}
            poster={clip.cover_rev ? `/api/clips/${clip.id}/cover?c=${clip.cover_rev}` : `/api/clips/${clip.id}/thumbnail?${v}`}
            controls={!rendering}
            playsInline
            preload="none"
            className="absolute inset-0 h-full w-full object-contain"
          />
        )}
        {rendering && (
          <div className="absolute inset-0 grid place-items-center bg-black/60 text-sm text-white/80">
            <span className="animate-pulse">Renderizando…</span>
          </div>
        )}
      </div>
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="rounded-full bg-white/10 px-2 py-0.5 text-xs font-semibold">#{clip.index}</span>
          {clip.reasons.map((r) => (
            <span key={r} className="rounded-full bg-fuchsia-500/15 px-2 py-0.5 text-xs text-fuchsia-200">
              {REASON_LABELS[r] ?? r}
            </span>
          ))}
          <span className="ml-auto text-xs text-white/50">
            {fmtTime(clip.offset + clip.start)}–{fmtTime(clip.offset + clip.end)} · {Math.round(clip.duration)}s
          </span>
        </div>
        {onChange && (clip.title || clip.caption) ? (
          <ClipText key={`${clip.id}-${clip.title}-${clip.caption}`} clip={clip} aiAvailable={aiAvailable} onChange={onChange} />
        ) : (
          clip.text && <p className="line-clamp-3 text-sm text-white/70">“{clip.text}”</p>
        )}
        {clip.render_status === "failed" && (
          <p className="rounded-xl bg-red-500/10 p-2 text-xs text-red-200">Falló el último render. Prueba de nuevo.</p>
        )}
        {clip.layout && (
          <p className="text-xs text-white/40">
            {clip.render?.format} · {LAYOUT_LABELS[clip.layout as keyof typeof LAYOUT_LABELS] ?? clip.layout}
          </p>
        )}
        {pubs.filter((p) => p.status !== "canceled").length > 0 && (
          <ul className="space-y-1 text-xs">
            {pubs.filter((p) => p.status !== "canceled").map((p) => {
              const a = accounts.find((x) => x.id === p.account_id);
              return (
                <li key={p.id} className={p.status === "failed" ? "text-red-300" : p.status === "published" || p.status === "draft_sent" ? "text-emerald-300" : "text-white/60"}>
                  {a ? PLATFORM_ICON[a.platform] : "•"} {PUB_STATUS[p.status]}
                  {p.status === "scheduled" && ` · ${new Date(p.scheduled_at).toLocaleString("es", { dateStyle: "short", timeStyle: "short" })}`}
                  {p.status === "failed" && p.error && ` · ${PUB_ERRORS[p.error] ?? p.error}`}
                  {p.external_url && <a href={p.external_url} target="_blank" rel="noreferrer" className="ml-1 underline">ver</a>}
                </li>
              );
            })}
          </ul>
        )}
        {onPublish && clip.render_status === "ready" && (
          <button onClick={onPublish} className="accent-gradient min-h-11 w-full rounded-2xl text-sm font-semibold">
            📤 Publicar o programar
          </button>
        )}
        {clip.render_status !== "rendering" && clip.version > 0 && (
          <Link href={`/clips/${clip.id}/editar`}
                className="flex min-h-11 items-center justify-center rounded-2xl border border-fuchsia-400/40 bg-fuchsia-500/10 text-sm font-semibold">
            ✂️ Abrir en el editor{clip.edited ? " (editado)" : ""}
          </Link>
        )}
        <div className="flex gap-2">
          {onEdit && (
            <button onClick={onEdit} disabled={rendering}
                    className="min-h-11 rounded-2xl bg-white/10 px-4 text-sm font-semibold hover:bg-white/15 disabled:opacity-50">
              Editar video
            </button>
          )}
          <a
            href={`/api/clips/${clip.id}/video?download=1&${v}`}
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

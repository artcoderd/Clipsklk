"use client";

import { useState } from "react";

import { api, errorMessage, type Clip } from "@/lib/api";
import { ErrorText } from "./ui";

/** Title (for the cover), text to publish and hashtags: copy, edit or ask the AI for another. */
export function ClipText({ clip, aiAvailable, onChange }: {
  clip: Clip; aiAvailable: boolean; onChange: (c: Clip) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(clip.title);
  const [caption, setCaption] = useState(clip.caption);
  const [tags, setTags] = useState(clip.hashtags.map((t) => `#${t}`).join(" "));
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const full = [clip.caption, clip.hashtags.map((t) => `#${t}`).join(" ")].filter(Boolean).join("\n\n");

  async function save() {
    setBusy(true);
    setError("");
    try {
      const updated = await api<Clip>(`/clips/${clip.id}`, {
        method: "PATCH",
        json: { title, caption, hashtags: tags.split(/[\s,]+/).filter(Boolean) },
      });
      onChange(updated);
      setEditing(false);
      if (updated.title !== clip.title) refreshCover();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  // The server redraws the cover in a few seconds; pick up the new one when it's there.
  function refreshCover() {
    for (const ms of [4000, 9000, 15000]) {
      setTimeout(() => api<Clip>(`/clips/${clip.id}`).then(onChange).catch(() => {}), ms);
    }
  }

  async function another() {
    setBusy(true);
    setError("");
    try {
      const c = await api<Clip>(`/clips/${clip.id}/caption`, { method: "POST" });
      onChange(c);
      refreshCover();
      setTitle(c.title);
      setCaption(c.caption);
      setTags(c.hashtags.map((t) => `#${t}`).join(" "));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  if (editing) {
    return (
      <div className="space-y-2 rounded-2xl bg-black/25 p-3">
        <input className="input-glass !py-2 !text-sm font-semibold" value={title} maxLength={120}
               onChange={(e) => setTitle(e.target.value)} aria-label="Título de la portada" />
        <textarea className="input-glass !py-2 !text-sm" rows={3} value={caption} maxLength={2200}
                  onChange={(e) => setCaption(e.target.value)} aria-label="Texto para publicar" />
        <input className="input-glass !py-2 !text-sm" value={tags} onChange={(e) => setTags(e.target.value)}
               aria-label="Hashtags" placeholder="#tema #otro" />
        <ErrorText>{error}</ErrorText>
        <div className="flex gap-2 text-sm">
          <button onClick={save} disabled={busy} className="accent-gradient min-h-10 flex-1 rounded-xl font-semibold">
            {busy ? "Guardando…" : "Guardar"}
          </button>
          <button onClick={() => setEditing(false)} className="min-h-10 rounded-xl bg-white/10 px-4">Cancelar</button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-2xl bg-black/25 p-3">
      {clip.title && <p className="font-bold leading-snug">{clip.title}</p>}
      {full && <p className="whitespace-pre-line text-sm text-white/75">{full}</p>}
      <ErrorText>{error}</ErrorText>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs font-semibold text-fuchsia-300">
        <button onClick={async () => {
          await navigator.clipboard.writeText(full);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}>{copied ? "Copiado ✓" : "Copiar texto"}</button>
        <button onClick={() => setEditing(true)}>Editar texto</button>
        {aiAvailable && <button onClick={another} disabled={busy}>{busy ? "Pensando…" : "✨ Otro texto"}</button>}
        {clip.render_status === "ready" && (
          <a href={`/api/clips/${clip.id}/cover?download=1&v=${clip.version}`}>Portada</a>
        )}
      </div>
    </div>
  );
}

"use client";

import { useRef, useState } from "react";

import { Loading, Shell } from "@/components/Shell";
import { Button, Card, ErrorText } from "@/components/ui";
import { ApiError, errorMessage, type LibraryItem } from "@/lib/api";
import { useLibrary } from "@/lib/useLibrary";
import { useMe } from "@/lib/useMe";

function upload(kind: "logo" | "music", file: File): Promise<LibraryItem> {
  const form = new FormData();
  form.append("kind", kind);
  form.append("file", file);
  return fetch("/api/library", { method: "POST", body: form }).then(async (r) => {
    const body = await r.json().catch(() => null);
    if (!r.ok) throw new ApiError(r.status, body?.detail ?? "error");
    return body;
  });
}

function Section({ kind, title, hint, accept, items, reload }: {
  kind: "logo" | "music"; title: string; hint: string; accept: string; items: LibraryItem[]; reload: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function onFile(f: File | undefined) {
    if (!f) return;
    setBusy(true);
    setError("");
    try {
      await upload(kind, f);
      reload();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <Card className="mb-5 space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{title}</h2>
          <p className="text-xs text-white/50">{hint}</p>
        </div>
        <Button onClick={() => input.current?.click()} disabled={busy} className="shrink-0">
          {busy ? "Subiendo…" : "Subir"}
        </Button>
        <input ref={input} type="file" accept={accept} hidden onChange={(e) => onFile(e.target.files?.[0])} />
      </div>
      <ErrorText>{error}</ErrorText>
      {items.length === 0 ? (
        <p className="text-sm text-white/40">Todavía no hay nada aquí.</p>
      ) : (
        <ul className="space-y-3">
          {items.map((i) => (
            <li key={i.id} className="flex items-center gap-3 rounded-2xl bg-black/20 p-3">
              {kind === "logo" ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={`/api/library/${i.id}/file`} alt="" className="h-12 w-20 rounded-lg bg-white/10 object-contain p-1" />
              ) : (
                <audio src={`/api/library/${i.id}/file`} controls preload="none" className="h-10 w-full max-w-[220px]" />
              )}
              <span className="min-w-0 flex-1 truncate text-sm">{i.name}</span>
              <button
                onClick={() => fetch(`/api/library/${i.id}`, { method: "DELETE" }).then(reload)}
                className="text-sm text-red-300"
              >
                Borrar
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default function LibraryPage() {
  const { user, loading } = useMe();
  const { logos, music, reload } = useLibrary(!!user);
  if (loading || !user) return <Loading />;
  return (
    <Shell user={user}>
      <h1 className="mb-4 text-2xl font-bold">Biblioteca</h1>
      <Section kind="logo" title="Logos" hint="PNG con fondo transparente, hasta 5 MB." accept="image/png,image/jpeg,image/webp"
               items={logos} reload={reload} />
      <Section kind="music" title="Música" hint="MP3, M4A, WAV u OGG, hasta 40 MB. Usa solo música con derechos para publicar."
               accept="audio/*" items={music} reload={reload} />
    </Shell>
  );
}

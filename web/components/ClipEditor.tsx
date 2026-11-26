"use client";

import { useEffect, useState } from "react";

import { api, DEFAULT_RENDER, errorMessage, type Clip, type RenderOptions } from "@/lib/api";
import { useLibrary } from "@/lib/useLibrary";
import { RenderOptionsForm } from "./RenderOptionsForm";
import { Button, ErrorText } from "./ui";

/** Bottom sheet to change a clip's options and render it again on the server. */
export function ClipEditor({ clip, jobId, onClose, onQueued }: {
  clip: Clip; jobId: string; onClose: () => void; onQueued: (c: Clip) => void;
}) {
  const library = useLibrary();
  const [opts, setOpts] = useState<RenderOptions>({ ...DEFAULT_RENDER, ...clip.render } as RenderOptions);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  async function rerender() {
    setBusy(true);
    setError("");
    try {
      onQueued(await api<Clip>(`/clips/${clip.id}/render`, { method: "POST", json: opts }));
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/75 backdrop-blur-sm sm:items-center" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Editar clip ${clip.index}`}
        onClick={(e) => e.stopPropagation()}
        className="max-h-[90dvh] w-full max-w-lg overflow-y-auto rounded-t-3xl border border-white/10 bg-[#15111c] p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] shadow-2xl sm:rounded-3xl"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Editar clip #{clip.index}</h2>
          <button onClick={onClose} aria-label="Cerrar" className="h-10 w-10 rounded-full bg-white/10">✕</button>
        </div>
        <RenderOptionsForm value={opts} onChange={setOpts} styles={library.styles} logos={library.logos}
                           music={library.music} previewSrc={`/api/jobs/${jobId}/preview`} />
        <ErrorText>{error}</ErrorText>
        <Button onClick={rerender} disabled={busy} className="mt-5 w-full">
          {busy ? "Enviando…" : "Volver a renderizar"}
        </Button>
      </div>
    </div>
  );
}

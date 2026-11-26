"use client";

import { useEffect, useState } from "react";

import { enablePush, pushState, type PushState } from "@/lib/push";

const TEXT: Record<PushState, string> = {
  on: "🔔 Notificaciones activadas",
  off: "🔔 Avisarme de cada clip nuevo",
  denied: "Notificaciones bloqueadas en este navegador (actívalas en sus ajustes)",
  unsupported: "Este navegador no admite notificaciones",
  "ios-install": "En iPhone: comparte → «Añadir a pantalla de inicio» para recibir avisos",
  unconfigured: "",
};

export function PushButton() {
  const [state, setState] = useState<PushState | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    pushState().then(setState).catch(() => setState("unsupported"));
  }, []);
  if (!state || state === "unconfigured") return null;
  if (state !== "off") return <p className="text-xs text-white/50">{TEXT[state]}</p>;
  return (
    <button
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        setState(await enablePush().catch(() => "unsupported" as PushState));
        setBusy(false);
      }}
      className="min-h-11 rounded-2xl bg-white/10 px-4 text-sm font-semibold hover:bg-white/15"
    >
      {busy ? "Activando…" : TEXT.off}
    </button>
  );
}

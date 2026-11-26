"use client";

import { useState } from "react";

import { Loading, Shell } from "@/components/Shell";
import { Button, Card, ErrorText } from "@/components/ui";
import { api, errorMessage, type AIPrefs } from "@/lib/api";
import { useAI } from "@/lib/useAI";
import { useMe } from "@/lib/useMe";

export default function AIPage() {
  const { user, loading } = useMe();
  const { status, prefs, setPrefs, available } = useAI(!!user);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  if (loading || !user || !status || !prefs) return <Loading />;
  const set = <K extends keyof AIPrefs>(k: K, v: AIPrefs[K]) => {
    setSaved(false);
    setPrefs({ ...prefs, [k]: v });
  };

  async function save() {
    setError("");
    try {
      setPrefs(await api<AIPrefs>("/ai/prefs", { method: "PUT", json: prefs }));
      setSaved(true);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <Shell user={user}>
      <h1 className="mb-1 text-2xl font-bold">Selección con IA</h1>
      <p className="mb-5 text-sm text-white/60">
        La IA lee la transcripción y elige los momentos con gancho, que se entienden solos y terminan en el remate.
        También escribe el título de la portada y el texto con hashtags.
      </p>

      {!available ? (
        <Card className="mb-5 text-sm text-white/70">
          La IA no está configurada en este servidor. {user.role !== "user" &&
            "Añade ANTHROPIC_API_KEY (u OPENAI_API_KEY / GEMINI_API_KEY) al archivo .env del servidor y reinicia."}
          {" "}Mientras tanto los clips se eligen con el análisis automático (risas, gritos, volumen y chat).
        </Card>
      ) : (
        <Card className="mb-5 space-y-5">
          <label className="flex items-center justify-between gap-3">
            <span>Usar IA para elegir los clips<span className="block text-xs text-white/50">Puedes cambiarlo en cada proyecto</span></span>
            <input type="checkbox" checked={prefs.use_ai} onChange={(e) => set("use_ai", e.target.checked)}
                   className="h-6 w-6 accent-fuchsia-500" />
          </label>
          {status.available.length > 1 && (
            <div className="space-y-2">
              <p className="text-sm text-white/70">Proveedor</p>
              <div className="flex flex-wrap gap-2">
                {status.available.map((p) => {
                  const active = (prefs.provider ?? status.default) === p.id;
                  return (
                    <button key={p.id} onClick={() => set("provider", p.id)} aria-pressed={active}
                            className={`min-h-10 rounded-xl px-3 text-sm ${active ? "accent-gradient" : "bg-white/8 text-white/70"}`}>
                      {p.label} <span className="opacity-60">· {p.model}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
          <label className="block space-y-1.5">
            <span className="text-sm text-white/70">Tus reglas</span>
            <textarea
              value={prefs.rules}
              onChange={(e) => set("rules", e.target.value)}
              maxLength={2000}
              rows={6}
              placeholder={"Ejemplos:\n- Prefiero momentos de humor y anécdotas personales\n- Nada de política\n- Los títulos en tono cercano, sin mayúsculas exageradas"}
              className="input-glass resize-y"
            />
            <span className="text-xs text-white/40">Se suman a las reglas virales y tienen prioridad si chocan. {prefs.rules.length}/2000</span>
          </label>
          <label className="block text-sm text-white/70">
            <span className="flex justify-between">Hashtags por clip <b className="text-white">{prefs.hashtags}</b></span>
            <input type="range" min={0} max={10} value={prefs.hashtags} onChange={(e) => set("hashtags", Number(e.target.value))}
                   className="mt-2 w-full accent-fuchsia-500" />
          </label>
          <ErrorText>{error}</ErrorText>
          <Button onClick={save} className="w-full">{saved ? "Guardado ✓" : "Guardar"}</Button>
        </Card>
      )}

      <Card className="space-y-3 text-sm">
        <h2 className="font-semibold">Privacidad</h2>
        <p className="text-white/60">
          A la IA solo se envía el texto de la transcripción (con marcas como «(risas)»). El video, el audio y tus datos
          no salen del servidor.
        </p>
        <details>
          <summary className="cursor-pointer text-fuchsia-300">Ver las reglas virales que usa</summary>
          <pre className="mt-3 whitespace-pre-wrap rounded-xl bg-black/30 p-3 font-sans text-xs text-white/60">{status.viral_rules}</pre>
        </details>
      </Card>
    </Shell>
  );
}

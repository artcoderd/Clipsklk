"use client";

import { useEffect, useState } from "react";

import { Loading, Shell } from "@/components/Shell";
import { SubtitlePreview } from "@/components/SubtitlePreview";
import { Button, Card, ErrorText, Field } from "@/components/ui";
import { api, errorMessage, type Style, type StyleConfig } from "@/lib/api";
import { useLibrary } from "@/lib/useLibrary";
import { useMe } from "@/lib/useMe";

const FONTS: [StyleConfig["font"], string][] = [
  ["montserrat-black", "Montserrat Black"],
  ["montserrat-extrabold", "Montserrat ExtraBold"],
  ["montserrat-bold", "Montserrat Bold"],
  ["bebas", "Bebas Neue"],
];

function Color({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="flex items-center justify-between gap-3 text-sm text-white/70">
      {label}
      <input type="color" value={value} onChange={(e) => onChange(e.target.value.toUpperCase())}
             className="h-10 w-14 cursor-pointer rounded-lg border border-white/10 bg-transparent" />
    </label>
  );
}

function Slider({ label, value, min, max, onChange }: {
  label: string; value: number; min: number; max: number; onChange: (v: number) => void;
}) {
  return (
    <label className="block text-sm text-white/70">
      <span className="flex justify-between">{label} <b className="text-white">{value}</b></span>
      <input type="range" min={min} max={max} value={value} onChange={(e) => onChange(Number(e.target.value))}
             className="mt-2 w-full accent-fuchsia-500" />
    </label>
  );
}

export default function StylesPage() {
  const { user, loading } = useMe();
  const { styles, reload } = useLibrary(!!user);
  const [editing, setEditing] = useState<Style | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!editing && styles.length) setEditing({ ...styles[0], id: styles[0].preset ? "" : styles[0].id,
                                                name: styles[0].preset ? `${styles[0].name} (copia)` : styles[0].name });
  }, [styles, editing]);

  if (loading || !user || !editing) return <Loading />;
  const c = editing.config;
  const set = <K extends keyof StyleConfig>(k: K, v: StyleConfig[K]) =>
    setEditing({ ...editing, config: { ...c, [k]: v } });

  function pick(s: Style) {
    setError("");
    // Presets can't be changed: editing one starts a new style from it.
    setEditing(s.preset ? { ...s, id: "", preset: false, name: `${s.name} (copia)` } : s);
  }

  async function save() {
    setError("");
    try {
      const body = { name: editing!.name, config: editing!.config };
      const saved = editing!.id
        ? await api<Style>(`/styles/${editing!.id}`, { method: "PUT", json: body })
        : await api<Style>("/styles", { method: "POST", json: body });
      setEditing(saved);
      reload();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function remove() {
    if (!editing?.id) return;
    await api(`/styles/${editing.id}`, { method: "DELETE" }).catch(() => {});
    setEditing(null);
    reload();
  }

  return (
    <Shell user={user}>
      <h1 className="mb-4 text-2xl font-bold">Estilos de subtítulos</h1>
      <div className="mb-5 flex gap-2 overflow-x-auto pb-1">
        {styles.map((s) => (
          <button key={s.id} onClick={() => pick(s)}
                  className={`shrink-0 rounded-xl px-3 py-2 text-sm ${editing.id === s.id ? "accent-gradient" : "bg-white/8 text-white/70"}`}>
            {s.name}
          </button>
        ))}
      </div>
      <div className="grid gap-5 sm:grid-cols-[290px_1fr]">
        <div className="sm:sticky sm:top-4 sm:self-start">
          <SubtitlePreview style={c} />
        </div>
        <Card className="space-y-4">
          <Field label="Nombre" value={editing.name} maxLength={60}
                 onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
          <label className="block space-y-1.5 text-sm text-white/70">
            Letra
            <select className="input-glass" value={c.font} onChange={(e) => set("font", e.target.value as StyleConfig["font"])}>
              {FONTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <Slider label="Tamaño" value={c.size} min={30} max={140} onChange={(v) => set("size", v)} />
          <Slider label="Palabras por línea" value={c.words_per_line} min={1} max={6} onChange={(v) => set("words_per_line", v)} />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Color label="Texto" value={c.text_color} onChange={(v) => set("text_color", v)} />
            <Color label="Palabra activa" value={c.highlight_color} onChange={(v) => set("highlight_color", v)} />
            <Color label="Borde" value={c.outline_color} onChange={(v) => set("outline_color", v)} />
          </div>
          <Slider label="Grosor del borde" value={c.outline} min={0} max={12} onChange={(v) => set("outline", v)} />
          <Slider label="Sombra" value={c.shadow} min={0} max={8} onChange={(v) => set("shadow", v)} />
          {([
            ["animation", "Animación", [["pop", "Salto"], ["highlight", "Color"], ["none", "Sin animación"]]],
            ["position", "Posición", [["bottom", "Abajo"], ["middle", "Centro"], ["top", "Arriba"]]],
          ] as const).map(([key, label, opts]) => (
            <div key={key} className="space-y-2">
              <p className="text-sm text-white/70">{label}</p>
              <div className="flex flex-wrap gap-2">
                {opts.map(([v, l]) => (
                  <button key={v} type="button" onClick={() => set(key, v as never)}
                          className={`min-h-10 rounded-xl px-3 text-sm ${c[key] === v ? "accent-gradient" : "bg-white/8 text-white/70"}`}>
                    {l}
                  </button>
                ))}
              </div>
            </div>
          ))}
          <label className="flex items-center gap-3 text-sm text-white/70">
            <input type="checkbox" checked={c.uppercase} onChange={(e) => set("uppercase", e.target.checked)}
                   className="h-5 w-5 accent-fuchsia-500" />
            Todo en MAYÚSCULAS
          </label>
          <ErrorText>{error}</ErrorText>
          <div className="flex gap-2">
            <Button onClick={save} className="flex-1">{editing.id ? "Guardar cambios" : "Guardar estilo"}</Button>
            {editing.id && <Button variant="danger" onClick={remove}>Borrar</Button>}
          </div>
        </Card>
      </div>
    </Shell>
  );
}

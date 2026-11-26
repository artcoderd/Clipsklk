"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import {
  FORMAT_LABELS, FRAMING_LABELS, LAYOUT_LABELS,
  type Format, type Framing, type Layout, type LibraryItem, type RenderOptions, type Style,
} from "@/lib/api";
import { GameplayEditor } from "./GameplayEditor";

function Chips<T extends string>({ value, options, onChange }: {
  value: T; options: [T, string][]; onChange: (v: T) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map(([v, label]) => (
        <button
          type="button"
          key={v}
          onClick={() => onChange(v)}
          aria-pressed={value === v}
          className={`min-h-10 rounded-xl px-3 text-sm transition ${value === v ? "accent-gradient text-white" : "bg-white/8 text-white/70"}`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-white/70">{label}</p>
      {children}
    </div>
  );
}

const select = "min-h-11 w-full rounded-xl border border-white/10 bg-black/30 px-3 text-sm";

export function RenderOptionsForm({
  value, onChange, styles, logos, music, previewSrc,
}: {
  value: RenderOptions;
  onChange: (v: RenderOptions) => void;
  styles: Style[];
  logos: LibraryItem[];
  music: LibraryItem[];
  previewSrc?: string;
}) {
  const set = <K extends keyof RenderOptions>(k: K, v: RenderOptions[K]) => onChange({ ...value, [k]: v });
  const showFraming = value.layout === "auto" || value.layout === "follow";

  return (
    <div className="space-y-5">
      <Row label="Formato">
        <Chips<Format> value={value.format} onChange={(v) => set("format", v)}
                       options={Object.entries(FORMAT_LABELS) as [Format, string][]} />
      </Row>
      <Row label="Encuadre">
        <Chips<Layout> value={value.layout} onChange={(v) => set("layout", v)}
                       options={(["auto", "follow", "split", "gameplay", "fit"] as Layout[]).map((l) => [l, LAYOUT_LABELS[l]])} />
      </Row>
      {showFraming && (
        <Row label="Cámara">
          <Chips<Framing> value={value.framing} onChange={(v) => set("framing", v)}
                          options={Object.entries(FRAMING_LABELS) as [Framing, string][]} />
        </Row>
      )}
      {value.layout === "gameplay" && (
        previewSrc ? (
          <GameplayEditor src={previewSrc} format={value.format} value={value.gameplay}
                          onChange={(g) => set("gameplay", g)} />
        ) : (
          <p className="rounded-xl bg-white/5 p-3 text-xs text-white/60">
            Pondremos la cámara arriba (donde detectemos la cara) y el juego abajo. Después podrás ajustar
            los recuadros a mano en cada clip.
          </p>
        )
      )}

      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-white/70">Subtítulos palabra por palabra</p>
        <button
          type="button"
          role="switch"
          aria-checked={value.subtitles}
          aria-label="Subtítulos"
          onClick={() => set("subtitles", !value.subtitles)}
          className={`relative h-8 w-14 shrink-0 rounded-full transition ${value.subtitles ? "accent-gradient" : "bg-white/15"}`}
        >
          <span className={`absolute top-1 h-6 w-6 rounded-full bg-white transition-all ${value.subtitles ? "left-7" : "left-1"}`} />
        </button>
      </div>
      {value.subtitles && (
        <Row label="Estilo de subtítulos">
          <div className="flex gap-2">
            <select className={select} value={value.style_id} onChange={(e) => set("style_id", e.target.value)}>
              {styles.map((s) => (
                <option key={s.id} value={s.id}>{s.name}{s.preset ? "" : " (mío)"}</option>
              ))}
            </select>
            <Link href="/estilos" className="flex min-h-11 shrink-0 items-center rounded-xl bg-white/8 px-3 text-sm">
              Editar
            </Link>
          </div>
        </Row>
      )}

      <Row label="Música de fondo">
        <select className={select} value={value.music_id ?? ""}
                onChange={(e) => set("music_id", e.target.value || null)}>
          <option value="">Sin música</option>
          {music.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
        {value.music_id && (
          <label className="flex items-center gap-3 text-xs text-white/60">
            Volumen
            <input type="range" min={0.05} max={0.6} step={0.05} value={value.music_volume}
                   onChange={(e) => set("music_volume", Number(e.target.value))}
                   className="flex-1 accent-fuchsia-500" />
          </label>
        )}
        {music.length === 0 && (
          <p className="text-xs text-white/40">
            Sube tus canciones en la <Link href="/biblioteca" className="text-fuchsia-300">biblioteca</Link>.
            Baja sola cuando alguien habla.
          </p>
        )}
      </Row>

      <Row label="Logo">
        <div className="flex gap-2">
          <select className={select} value={value.logo_id ?? ""}
                  onChange={(e) => set("logo_id", e.target.value || null)}>
            <option value="">Sin logo</option>
            {logos.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
          {value.logo_id && (
            <select className={`${select} w-36`} value={value.logo_position}
                    onChange={(e) => set("logo_position", e.target.value as RenderOptions["logo_position"])}>
              <option value="top-right">Arriba der.</option>
              <option value="top-left">Arriba izq.</option>
            </select>
          )}
        </div>
      </Row>
    </div>
  );
}

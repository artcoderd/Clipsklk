"use client";

import { useRef, type PointerEvent, type RefObject } from "react";

import type { StyleConfig } from "@/lib/api";
import { wordDeleted, type EditDoc, type Word } from "@/lib/editor";

const FAMILY: Record<string, string> = {
  "montserrat-black": "'Montserrat Black'", "montserrat-extrabold": "'Montserrat ExtraBold'",
  "montserrat-bold": "'Montserrat Bold'", bebas: "'Bebas Neue'",
};
const TEXT_LOOK = {
  title: { font: "'Montserrat Black'", box: false },
  label: { font: "'Montserrat ExtraBold'", box: true },
  outline: { font: "'Bebas Neue'", box: false },
};

/** The preview copy plus everything the server will burn in, drawn live. */
export function Preview({
  video, src, aspect, t, doc, words, subtitles, style, selected, onSelect, onChange, onCommit, onLoaded,
}: {
  video: RefObject<HTMLVideoElement | null>; src: string; aspect: string; t: number; doc: EditDoc; words: Word[];
  subtitles: boolean; style: StyleConfig | null; selected: string | null;
  onSelect: (id: string | null) => void; onChange: (d: EditDoc) => void; onCommit: () => void; onLoaded: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: string; list: "texts" | "images"; dx: number; dy: number; moved: boolean } | null>(null);
  const [aw, ah] = aspect.split(":").map(Number);
  const W = box.current?.clientWidth ?? 300;
  const H = box.current?.clientHeight ?? 533;
  const unit = Math.min(W, H) / 1080;   // server sizes are for a 1080 px short side

  // Current subtitle line: chunk of spoken (not deleted) words around t.
  const live = words.filter((w) => !wordDeleted(doc, w));
  const per = style?.words_per_line ?? 3;
  const idx = live.findIndex((w) => t >= w.start - 0.05 && t < w.end + 0.25);
  const chunk = idx >= 0 ? live.slice(Math.floor(idx / per) * per, Math.floor(idx / per) * per + per) : [];

  function down(e: PointerEvent, id: string, list: "texts" | "images", x: number, y: number) {
    e.stopPropagation();
    onSelect(id);
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    const r = box.current!.getBoundingClientRect();
    drag.current = { id, list, dx: (e.clientX - r.left) / r.width - x, dy: (e.clientY - r.top) / r.height - y, moved: false };
  }
  function move(e: PointerEvent) {
    const d = drag.current;
    if (!d) return;
    const r = box.current!.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width - d.dx));
    const y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height - d.dy));
    d.moved = true;
    const next = structuredClone(doc);
    const it = next[d.list].find((v) => v.id === d.id)!;
    it.x = x;
    it.y = y;
    onChange(next);
  }
  function up() {
    if (drag.current?.moved) onCommit();
    drag.current = null;
  }

  return (
    <div ref={box} data-testid="preview" className="relative mx-auto w-full max-w-[340px] touch-none overflow-hidden rounded-3xl bg-black"
         style={{ aspectRatio: `${aw} / ${ah}` }} onPointerMove={move} onPointerUp={up} onPointerDown={() => onSelect(null)}>
      <video ref={video} src={src} playsInline preload="auto" className="absolute inset-0 h-full w-full" onLoadedMetadata={onLoaded} />
      {subtitles && chunk.length > 0 && style && (
        <div className={`pointer-events-none absolute inset-x-[8%] text-center leading-tight ${
          style.position === "top" ? "top-[14%]" : style.position === "middle" ? "top-1/2 -translate-y-1/2" : "bottom-[27%]"}`}>
          {chunk.map((w, i) => {
            const on = t >= w.start && t < w.end + 0.25;
            return (
              <span key={i} className="inline-block px-[0.12em]" style={{
                fontFamily: FAMILY[style.font], fontSize: style.size * unit,
                color: on && style.animation !== "none" ? style.highlight_color : style.text_color,
                WebkitTextStroke: `${style.outline * unit * 1.6}px ${style.outline_color}`, paintOrder: "stroke fill",
                textTransform: style.uppercase ? "uppercase" : "none",
              }}>{w.word}</span>
            );
          })}
        </div>
      )}
      {doc.images.filter((im) => t >= im.start && t < im.end).map((im) => (
        // eslint-disable-next-line @next/next/no-img-element
        <img key={im.id} src={`/api/library/${im.library_id}/file`} alt="" draggable={false}
             onPointerDown={(e) => down(e, im.id, "images", im.x, im.y)}
             className={`absolute -translate-x-1/2 -translate-y-1/2 cursor-move ${selected === im.id ? "outline outline-2 outline-cyan-300" : ""}`}
             style={{ left: `${im.x * 100}%`, top: `${im.y * 100}%`, width: `${im.w * 100}%` }} />
      ))}
      {doc.texts.filter((tx) => t >= tx.start && t < tx.end).map((tx) => {
        const look = TEXT_LOOK[tx.style];
        return (
          <span key={tx.id} onPointerDown={(e) => down(e, tx.id, "texts", tx.x, tx.y)}
                className={`absolute -translate-x-1/2 -translate-y-1/2 cursor-move whitespace-nowrap px-2 ${selected === tx.id ? "outline outline-2 outline-fuchsia-300" : ""}`}
                style={{
                  left: `${tx.x * 100}%`, top: `${tx.y * 100}%`, fontFamily: look.font, fontSize: tx.size * unit, color: tx.color,
                  background: look.box ? tx.box_color : "transparent", borderRadius: look.box ? 6 : 0,
                  WebkitTextStroke: look.box ? "0" : `${6 * unit * 1.6}px #000`, paintOrder: "stroke fill",
                }}>{tx.text}</span>
        );
      })}
    </div>
  );
}

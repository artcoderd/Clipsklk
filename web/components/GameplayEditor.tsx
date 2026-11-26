"use client";

import { useRef, useState, type PointerEvent } from "react";

import type { Format, Rect } from "@/lib/api";

/** Output panel aspect (w/h) for the camera and the game, as the server lays them out. */
export function gameplayAspects(format: Format): { cam: number; game: number } {
  const size = { "9:16": [1080, 1920], "4:5": [1080, 1350], "1:1": [1080, 1080], "16:9": [1920, 1080] }[format];
  const [ow, oh] = size;
  if (oh >= ow) {
    const camH = Math.round(oh * 0.38);
    return { cam: ow / camH, game: ow / (oh - camH) };
  }
  return { cam: (ow * 0.28) / (oh / 3), game: ow / oh };
}

type Box = "cam" | "game";
const COLORS: Record<Box, string> = { cam: "#f0abfc", game: "#67e8f9" };
const LABEL: Record<Box, string> = { cam: "Cámara", game: "Juego" };

function clampRect([x, y, w, h]: Rect): Rect {
  w = Math.min(1, Math.max(0.05, w));
  h = Math.min(1, Math.max(0.05, h));
  return [Math.min(Math.max(0, x), 1 - w), Math.min(Math.max(0, y), 1 - h), w, h];
}

/** Rectangles keep the output's proportions, so what you frame is what you get. */
function fit(r: Rect, aspect: number, srcAspect: number): Rect {
  const [x, y, w] = r;
  let nw = w;
  let nh = (w * srcAspect) / aspect;
  if (nh > 1) {
    nh = 1;
    nw = (aspect / srcAspect) * nh;
  }
  return clampRect([x, y, nw, nh]);
}

export function GameplayEditor({
  src,
  format,
  value,
  onChange,
}: {
  src: string;
  format: Format;
  value: { cam: Rect | null; game: Rect | null } | null;
  onChange: (v: { cam: Rect; game: Rect }) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [srcAspect, setSrcAspect] = useState(16 / 9);
  const drag = useRef<{ which: Box; mode: "move" | "resize"; x: number; y: number; start: Rect } | null>(null);
  const aspects = gameplayAspects(format);
  const cam = fit(value?.cam ?? [0.02, 0.02, 0.3, 0.3], aspects.cam, srcAspect);
  const game = fit(value?.game ?? [0.2, 0, 0.6, 1], aspects.game, srcAspect);
  const rects: Record<Box, Rect> = { cam, game };

  function down(e: PointerEvent, which: Box, mode: "move" | "resize") {
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { which, mode, x: e.clientX, y: e.clientY, start: rects[which] };
  }
  function move(e: PointerEvent) {
    const d = drag.current;
    const el = box.current;
    if (!d || !el) return;
    const bw = el.clientWidth;
    const bh = el.clientHeight;
    const dx = (e.clientX - d.x) / bw;
    const dy = (e.clientY - d.y) / bh;
    const [x, y, w, h] = d.start;
    const next: Rect = d.mode === "move" ? clampRect([x + dx, y + dy, w, h]) : [x, y, w + dx, h];
    const fitted = fit(next, aspects[d.which], srcAspect);
    onChange({ cam, game, [d.which]: fitted });
  }

  return (
    <div className="space-y-2">
      <div
        ref={box}
        className="relative w-full touch-none select-none overflow-hidden rounded-2xl bg-black"
        style={{ aspectRatio: srcAspect }}
        onPointerMove={move}
        onPointerUp={() => (drag.current = null)}
        onPointerCancel={() => (drag.current = null)}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt="Fotograma del video"
          className="absolute inset-0 h-full w-full object-cover opacity-80"
          draggable={false}
          onLoad={(e) => setSrcAspect(e.currentTarget.naturalWidth / e.currentTarget.naturalHeight)}
        />
        {(["game", "cam"] as Box[]).map((k) => {
          const [x, y, w, h] = rects[k];
          return (
            <div
              key={k}
              onPointerDown={(e) => down(e, k, "move")}
              className="absolute cursor-move border-2"
              style={{ left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%`,
                       borderColor: COLORS[k], background: `${COLORS[k]}22` }}
            >
              <span className="absolute left-1 top-1 rounded px-1.5 text-[11px] font-bold text-black" style={{ background: COLORS[k] }}>
                {LABEL[k]}
              </span>
              <span
                onPointerDown={(e) => down(e, k, "resize")}
                aria-label={`Cambiar tamaño de ${LABEL[k]}`}
                className="absolute -bottom-3 -right-3 h-7 w-7 cursor-nwse-resize rounded-full border-2 border-white"
                style={{ background: COLORS[k] }}
              />
            </div>
          );
        })}
      </div>
      <p className="text-xs text-white/50">Arrastra los recuadros. Usa el círculo de la esquina para cambiar el tamaño.</p>
    </div>
  );
}

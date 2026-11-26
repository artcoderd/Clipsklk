"use client";

import { useEffect, useRef, useState, type PointerEvent } from "react";

import { fmt, type EditDoc, type Range } from "@/lib/editor";

type Drag =
  | { kind: "trimL" | "trimR" | "scrub" | "range"; x0: number; t0: number; moved: boolean }
  | { kind: "item" | "itemStart" | "itemEnd"; id: string; list: "texts" | "images"; x0: number; t0: number; orig: [number, number]; moved: boolean }
  | { kind: "sound" | "marker"; id: string; x0: number; t0: number; orig: number; moved: boolean };

export function Timeline({
  bounds, doc, level, playhead, selected, rangeMode, range,
  onSeek, onChange, onCommit, onSelect, onRange,
}: {
  bounds: Range; doc: EditDoc; level: number[]; playhead: number; selected: string | null;
  rangeMode: boolean; range: Range | null;
  onSeek: (t: number) => void;
  onChange: (d: EditDoc) => void;   // live while dragging
  onCommit: () => void;             // drag finished: save + undo point
  onSelect: (id: string | null) => void;
  onRange: (r: Range | null) => void;
}) {
  const [lo, hi] = bounds;
  const span = hi - lo;
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [pps, setPps] = useState(12);       // pixels per second
  const drag = useRef<Drag | null>(null);
  const fitted = useRef(false);

  // Start zoomed so the whole stretch fits the screen.
  useEffect(() => {
    if (fitted.current || !scroller.current) return;
    fitted.current = true;
    setPps(Math.max(4, Math.min(80, (scroller.current.clientWidth - 16) / span)));
  }, [span]);

  // Keep the playhead in view while playing.
  useEffect(() => {
    const el = scroller.current;
    if (!el || drag.current) return;
    const x = (playhead - lo) * pps;
    if (x < el.scrollLeft + 20 || x > el.scrollLeft + el.clientWidth - 20) el.scrollLeft = x - el.clientWidth / 3;
  }, [playhead, pps, lo]);

  const width = Math.max(1, span * pps);
  const X = (t: number) => (t - lo) * pps;
  const tAt = (clientX: number) => {
    const r = content.current!.getBoundingClientRect();
    return Math.min(hi, Math.max(lo, lo + (clientX - r.left) / pps));
  };
  const step = [1, 2, 5, 10, 15, 30, 60].find((s) => s * pps >= 60) ?? 120;

  function start(e: PointerEvent, d: Drag) {
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = d;
  }

  function move(e: PointerEvent) {
    const d = drag.current;
    if (!d) return;
    if (Math.abs(e.clientX - d.x0) > 3) d.moved = true;
    if (!d.moved) return;
    const t = tAt(e.clientX);
    const dt = t - d.t0;
    const next: EditDoc = structuredClone(doc);
    switch (d.kind) {
      case "trimL": next.trim = [Math.min(t, doc.trim[1] - 1), doc.trim[1]]; break;
      case "trimR": next.trim = [doc.trim[0], Math.max(t, doc.trim[0] + 1)]; break;
      case "scrub": onSeek(t); return;
      case "range": onRange([Math.min(d.t0, t), Math.max(d.t0, t)]); return;
      case "item": case "itemStart": case "itemEnd": {
        const it = next[d.list].find((x) => x.id === d.id)!;
        const [a, b] = d.orig;
        if (d.kind === "item") { const sh = Math.min(hi - b, Math.max(lo - a, dt)); it.start = a + sh; it.end = b + sh; }
        if (d.kind === "itemStart") it.start = Math.min(b - 0.3, Math.max(lo, a + dt));
        if (d.kind === "itemEnd") it.end = Math.max(a + 0.3, Math.min(hi, b + dt));
        break;
      }
      case "sound": next.sounds.find((x) => x.id === d.id)!.t = Math.min(hi, Math.max(lo, d.orig + dt)); break;
      case "marker": next.markers.find((x) => x.id === d.id)!.t = Math.min(hi, Math.max(lo, d.orig + dt)); break;
    }
    onChange(next);
  }

  function end(e: PointerEvent) {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if (!d.moved) {
      // A tap: seek (lane), select (item) or clear the range.
      if (d.kind === "range" || d.kind === "scrub") { onSeek(tAt(e.clientX)); if (d.kind === "range") onRange(null); }
      else if ("id" in d) onSelect(d.id);
      return;
    }
    if (d.kind !== "scrub" && d.kind !== "range") onCommit();
  }

  const lane = (e: PointerEvent) => start(e, { kind: rangeMode ? "range" : "scrub", x0: e.clientX, t0: tAt(e.clientX), moved: false });
  const itemBar = (list: "texts" | "images", it: { id: string; start: number; end: number }, color: string, label: string, row: number) => (
    <div
      key={it.id}
      onPointerDown={(e) => start(e, { kind: "item", id: it.id, list, x0: e.clientX, t0: tAt(e.clientX), orig: [it.start, it.end], moved: false })}
      className={`absolute flex h-6 cursor-grab items-center overflow-hidden rounded-md px-2 text-[11px] font-semibold text-black ${selected === it.id ? "ring-2 ring-white" : ""}`}
      style={{ left: X(it.start), width: Math.max(14, (it.end - it.start) * pps), top: row * 28 + 4, background: color }}
    >
      <span
        onPointerDown={(e) => start(e, { kind: "itemStart", id: it.id, list, x0: e.clientX, t0: tAt(e.clientX), orig: [it.start, it.end], moved: false })}
        className="absolute inset-y-0 left-0 w-3 cursor-ew-resize bg-black/20" aria-label="Inicio"
      />
      <span className="truncate pl-1">{label}</span>
      <span
        onPointerDown={(e) => start(e, { kind: "itemEnd", id: it.id, list, x0: e.clientX, t0: tAt(e.clientX), orig: [it.start, it.end], moved: false })}
        className="absolute inset-y-0 right-0 w-3 cursor-ew-resize bg-black/20" aria-label="Fin"
      />
    </div>
  );

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs text-white/50">
        <span>{fmt(playhead)}</span>
        <div className="flex items-center gap-2">
          <button onClick={() => setPps((p) => Math.max(2, p / 1.6))} aria-label="Alejar" className="h-9 w-9 rounded-lg bg-white/10 text-base">−</button>
          <button onClick={() => setPps((p) => Math.min(300, p * 1.6))} aria-label="Acercar" className="h-9 w-9 rounded-lg bg-white/10 text-base">+</button>
        </div>
      </div>
      <div
        ref={scroller}
        className="overflow-x-auto overscroll-x-contain rounded-2xl bg-black/40 pb-2"
        onWheel={(e) => { if (e.ctrlKey) { e.preventDefault(); setPps((p) => Math.min(300, Math.max(2, p * (e.deltaY < 0 ? 1.15 : 0.87)))); } }}
      >
        <div
          ref={content}
          className="relative select-none"
          style={{ width: width + 16, touchAction: rangeMode || drag.current ? "none" : "pan-x" }}
          onPointerMove={move}
          onPointerUp={end}
          onPointerCancel={() => (drag.current = null)}
        >
          {/* ruler + markers (drag the ruler to scrub) */}
          <div className="relative h-7 cursor-col-resize border-b border-white/10" onPointerDown={(e) => start(e, { kind: "scrub", x0: e.clientX, t0: tAt(e.clientX), moved: false })}>
            {Array.from({ length: Math.floor(span / step) + 1 }, (_, i) => {
              const t = Math.ceil(lo / step) * step + i * step;
              return t <= hi ? (
                <span key={t} className="absolute top-1 border-l border-white/20 pl-1 text-[10px] text-white/40" style={{ left: X(t) }}>{fmt(t).replace(/\.\d$/, "")}</span>
              ) : null;
            })}
            {doc.markers.map((m) => (
              <span key={m.id}
                onPointerDown={(e) => start(e, { kind: "marker", id: m.id, x0: e.clientX, t0: tAt(e.clientX), orig: m.t, moved: false })}
                className={`absolute top-0 -ml-2 flex h-7 min-w-4 cursor-grab items-start text-sm ${selected === m.id ? "drop-shadow-[0_0_4px_white]" : ""}`}
                style={{ left: X(m.t) }} title={m.label}>🚩</span>
            ))}
          </div>

          {/* video lane: loudness, trim, cuts, selection */}
          <div className="relative h-16 cursor-pointer" onPointerDown={lane} data-testid="video-lane">
            {level.map((v, i) => (
              <span key={i} className="absolute bottom-1 bg-fuchsia-400/50" style={{ left: X(lo + i) + 1, width: Math.max(1, pps - 2), height: `${8 + v * 80}%` }} />
            ))}
            <span className="absolute inset-y-0 left-0 bg-black/70" style={{ width: X(doc.trim[0]) }} />
            <span className="absolute inset-y-0 bg-black/70" style={{ left: X(doc.trim[1]), right: 0 }} />
            {doc.cuts.map(([a, b], i) => (
              <span key={i} className="absolute inset-y-0 bg-[repeating-linear-gradient(135deg,rgba(239,68,68,.55)_0_6px,rgba(0,0,0,.4)_6px_12px)]"
                    style={{ left: X(a), width: Math.max(2, (b - a) * pps) }} />
            ))}
            {range && <span className="absolute inset-y-0 border-x-2 border-amber-300 bg-amber-300/25" style={{ left: X(range[0]), width: (range[1] - range[0]) * pps }} />}
            {(["trimL", "trimR"] as const).map((k) => (
              <span key={k}
                onPointerDown={(e) => start(e, { kind: k, x0: e.clientX, t0: tAt(e.clientX), moved: false })}
                aria-label={k === "trimL" ? "Recortar inicio" : "Recortar final"}
                className="absolute inset-y-0 z-10 flex w-6 -translate-x-1/2 cursor-ew-resize items-center justify-center"
                style={{ left: X(k === "trimL" ? doc.trim[0] : doc.trim[1]) }}>
                <span className="h-full w-2 rounded-full bg-amber-300 shadow" />
              </span>
            ))}
          </div>

          {/* overlays: texts, images, sounds */}
          <div className="relative h-[88px] border-t border-white/10">
            {doc.texts.map((t) => itemBar("texts", t, "#f0abfc", `T ${t.text}`, 0))}
            {doc.images.map((im) => itemBar("images", im, "#67e8f9", "🖼 imagen", 1))}
            {doc.sounds.map((s) => (
              <span key={s.id}
                onPointerDown={(e) => start(e, { kind: "sound", id: s.id, x0: e.clientX, t0: tAt(e.clientX), orig: s.t, moved: false })}
                className={`absolute top-[60px] -ml-3 flex h-6 w-6 cursor-grab items-center justify-center rounded-full bg-amber-300 text-xs ${selected === s.id ? "ring-2 ring-white" : ""}`}
                style={{ left: X(s.t) }} aria-label="Sonido">🔊</span>
            ))}
          </div>

          <span className="pointer-events-none absolute inset-y-0 w-0.5 bg-white" style={{ left: X(playhead) }} />
        </div>
      </div>
    </div>
  );
}

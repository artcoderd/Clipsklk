"use client";

import { wordDeleted, type EditDoc, type Word } from "@/lib/editor";

/** Edit by text: tap a word to select it, tap another to extend; then delete or restore. */
export function Transcript({ words, doc, t, sel, onSel, onSeek }: {
  words: Word[]; doc: EditDoc; t: number; sel: [number, number] | null;
  onSel: (s: [number, number] | null) => void; onSeek: (t: number) => void;
}) {
  function tap(i: number) {
    if (!sel) onSel([i, i]);
    else if (i >= sel[0] && i <= sel[1] && sel[0] === sel[1]) onSel(null);
    else onSel([Math.min(sel[0], i), Math.max(sel[1], i)]);
    onSeek(words[i].start);
  }
  return (
    <div className="max-h-56 overflow-y-auto rounded-2xl bg-black/30 p-3 text-[15px] leading-8" data-testid="transcript">
      {words.length === 0 && <p className="text-sm text-white/40">Este tramo no tiene palabras habladas.</p>}
      {words.map((w, i) => {
        const gone = wordDeleted(doc, w);
        const picked = sel && i >= sel[0] && i <= sel[1];
        const now = t >= w.start && t < w.end;
        return (
          <button key={i} onClick={() => tap(i)}
            className={`mr-1 rounded px-0.5 transition ${gone ? "text-white/30 line-through decoration-red-400" : "text-white/90"}
              ${picked ? "bg-amber-300/40" : now ? "bg-fuchsia-500/40" : ""}`}>
            {w.word}
          </button>
        );
      })}
    </div>
  );
}

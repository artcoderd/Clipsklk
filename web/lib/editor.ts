/** Edit document shared with the server (app/media/edit.py). Times are source seconds. */
export type Range = [number, number];
export type EditText = {
  id: string; text: string; start: number; end: number; x: number; y: number;
  size: number; color: string; style: "title" | "label" | "outline"; box_color: string;
};
export type EditImage = { id: string; library_id: string; start: number; end: number; x: number; y: number; w: number };
export type EditSound = { id: string; library_id: string; t: number; volume: number };
export type EditMarker = { id: string; t: number; label: string };
export type EditDoc = {
  trim: Range; cuts: Range[]; texts: EditText[]; images: EditImage[]; sounds: EditSound[];
  markers: EditMarker[]; music: { library_id: string; volume: number } | null;
};
export type Word = { start: number; end: number; word: string };

export const uid = () => Math.random().toString(36).slice(2, 10);

export function mergeRanges(ranges: Range[], lo: number, hi: number): Range[] {
  const sorted = ranges
    .map(([a, b]) => [Math.max(lo, Math.min(a, b)), Math.min(hi, Math.max(a, b))] as Range)
    .filter(([a, b]) => b - a > 0.01)
    .sort((x, y) => x[0] - y[0]);
  const out: Range[] = [];
  for (const r of sorted) {
    const last = out[out.length - 1];
    if (last && r[0] <= last[1] + 0.02) last[1] = Math.max(last[1], r[1]);
    else out.push([r[0], r[1]]);
  }
  return out;
}

/** Removes [a, b] from a set of ranges (to "restore" deleted words). */
export function subtractRange(ranges: Range[], a: number, b: number): Range[] {
  const out: Range[] = [];
  for (const [x, y] of ranges) {
    if (y <= a || x >= b) out.push([x, y]);
    else {
      if (x < a) out.push([x, a]);
      if (y > b) out.push([b, y]);
    }
  }
  return out;
}

export function keeps(doc: EditDoc): Range[] {
  const cuts = mergeRanges(doc.cuts, doc.trim[0], doc.trim[1]);
  const out: Range[] = [];
  let t = doc.trim[0];
  for (const [a, b] of cuts) {
    if (a - t >= 0.05) out.push([t, a]);
    t = Math.max(t, b);
  }
  if (doc.trim[1] - t >= 0.05) out.push([t, doc.trim[1]]);
  return out;
}

export const isKept = (ks: Range[], t: number) => ks.some(([a, b]) => t >= a && t < b);
export const totalOf = (ks: Range[]) => ks.reduce((s, [a, b]) => s + (b - a), 0);
export const outT = (ks: Range[], t: number) => ks.reduce((s, [a, b]) => s + Math.max(0, Math.min(b, t) - a), 0);

/** Next playable source time at or after t (skipping cuts), or null past the end. */
export function nextKept(ks: Range[], t: number): number | null {
  for (const [a, b] of ks) {
    if (t < a) return a;
    if (t < b) return t;
  }
  return null;
}

/** A word's span for cutting: from its start to the next word's start (removes the pause too). */
export function wordSpan(words: Word[], i: number): Range {
  const next = words[i + 1];
  return [words[i].start, next && next.start - words[i].end < 0.8 ? next.start : words[i].end + 0.05];
}

export function wordDeleted(doc: EditDoc, w: Word): boolean {
  const mid = (w.start + w.end) / 2;
  return mid < doc.trim[0] || mid >= doc.trim[1] || doc.cuts.some(([a, b]) => mid >= a && mid < b);
}

/** Cuts every pause longer than `min` seconds between spoken words (keeps a little air). */
export function silenceCuts(words: Word[], doc: EditDoc, min = 0.7, pad = 0.15): Range[] {
  const inside = words.filter((w) => w.start >= doc.trim[0] && w.end <= doc.trim[1]);
  const cuts: Range[] = [];
  if (inside.length && inside[0].start - doc.trim[0] > min) cuts.push([doc.trim[0] + pad, inside[0].start - pad]);
  for (let i = 0; i + 1 < inside.length; i++) {
    const gap = inside[i + 1].start - inside[i].end;
    if (gap > min) cuts.push([inside[i].end + pad, inside[i + 1].start - pad]);
  }
  const last = inside[inside.length - 1];
  if (last && doc.trim[1] - last.end > min) cuts.push([last.end + pad, doc.trim[1] - pad]);
  return cuts;
}

export function fmt(t: number): string {
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

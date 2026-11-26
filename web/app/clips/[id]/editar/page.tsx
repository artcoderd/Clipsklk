"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Preview } from "@/components/editor/Preview";
import { Timeline } from "@/components/editor/Timeline";
import { Transcript } from "@/components/editor/Transcript";
import { Loading } from "@/components/Shell";
import { Button, ErrorText } from "@/components/ui";
import { api, DEFAULT_RENDER, errorMessage, type Clip, type Style } from "@/lib/api";
import {
  fmt, isKept, keeps, mergeRanges, nextKept, outT, silenceCuts, subtractRange, totalOf, uid, wordSpan,
  type EditDoc, type Range, type Word,
} from "@/lib/editor";
import { useLibrary } from "@/lib/useLibrary";
import { useMe } from "@/lib/useMe";

type EditorData = {
  clip: Clip; edit: EditDoc; bounds: Range; duration: number; words: Word[]; level: number[];
  proxy: { status: string; start: number | null; url: string | null };
};
type Tool = "texto" | "imagen" | "sonido" | "musica" | "marcador";

export default function EditorPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { user, loading } = useMe();
  const library = useLibrary(!!user);
  const [data, setData] = useState<EditorData | null>(null);
  const [doc, setDoc] = useState<EditDoc | null>(null);
  const [past, setPast] = useState<EditDoc[]>([]);
  const [future, setFuture] = useState<EditDoc[]>([]);
  const [saved, setSaved] = useState<"saved" | "saving" | "dirty">("saved");
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [wordSel, setWordSel] = useState<[number, number] | null>(null);
  const [rangeMode, setRangeMode] = useState(false);
  const [range, setRange] = useState<Range | null>(null);
  const [tool, setTool] = useState<Tool | null>(null);
  const [error, setError] = useState("");
  const video = useRef<HTMLVideoElement>(null);
  const music = useRef<HTMLAudioElement>(null);
  const lastT = useRef(0);
  const committed = useRef<EditDoc | null>(null);

  // Load; ask for the preview copy and wait for it.
  const load = useCallback(async () => {
    const d = await api<EditorData>(`/clips/${id}/editor`);
    setData(d);
    setDoc((cur) => cur ?? d.edit);
    committed.current = committed.current ?? d.edit;
    setT((cur) => cur || d.edit.trim[0]);
    return d;
  }, [id]);
  useEffect(() => {
    if (!user) return;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const d = await load();
        if (d.proxy.status !== "ready") {
          if (d.proxy.status !== "preparing") await api(`/clips/${id}/editor/prepare`, { method: "POST" });
          timer = setTimeout(tick, 2500);
        }
      } catch (e) {
        setError(errorMessage(e));
      }
    };
    api(`/clips/${id}/editor/prepare`, { method: "POST" }).catch(() => {}).finally(tick);
    return () => clearTimeout(timer);
  }, [user, id, load]);

  // Changes: `change` while dragging, `commit` = undo point + autosave.
  const commit = useCallback((next?: EditDoc) => {
    const d = next ?? doc;
    if (!d || !committed.current) return;
    if (JSON.stringify(d) === JSON.stringify(committed.current)) return;
    // Capture now: React runs state updaters later, when the ref already holds `d`.
    const prev = committed.current;
    setPast((p) => [...p.slice(-60), prev]);
    setFuture([]);
    committed.current = d;
    if (next) setDoc(next);
    setSaved("dirty");
  }, [doc]);
  const apply = (fn: (d: EditDoc) => void) => {
    const next = structuredClone(doc!);
    fn(next);
    next.cuts = mergeRanges(next.cuts, next.trim[0], next.trim[1]);
    commit(next);
  };
  const undo = () => {
    if (!past.length || !committed.current) return;
    const cur = committed.current;
    const prev = past[past.length - 1];
    setFuture((f) => [cur, ...f]);
    setPast((p) => p.slice(0, -1));
    committed.current = prev;
    setDoc(prev);
    setSaved("dirty");
  };
  const redo = () => {
    if (!future.length || !committed.current) return;
    const cur = committed.current;
    const next = future[0];
    setPast((p) => [...p, cur]);
    setFuture((f) => f.slice(1));
    committed.current = next;
    setDoc(next);
    setSaved("dirty");
  };

  // Autosave 1.2 s after the last change.
  const save = useCallback(async (): Promise<boolean> => {
    const sent = committed.current;
    if (!sent) return false;
    setSaved("saving");
    try {
      const out = await api<EditDoc>(`/clips/${id}/edit`, { method: "PUT", json: sent });
      if (committed.current === sent) {
        // Nothing changed while saving: adopt the server's normalised version.
        committed.current = out;
        setDoc(out);
        setSaved("saved");
      } else {
        // The user kept editing during the save: keep their changes, save them next.
        setSaved("dirty");
      }
      return true;
    } catch (e) {
      setError(errorMessage(e));
      setSaved("dirty");
      return false;
    }
  }, [id]);
  useEffect(() => {
    if (saved !== "dirty") return;
    const timer = setTimeout(save, 1200);
    return () => clearTimeout(timer);
  }, [saved, save]);

  // Playback: skip cuts, stop at the end of the trim, fire sound effects.
  const ks = useMemo(() => (doc ? keeps(doc) : []), [doc]);
  useEffect(() => {
    if (!playing || !data || !doc) return;
    let raf = 0;
    const base = data.proxy.start ?? data.bounds[0];
    const loop = () => {
      const v = video.current;
      if (!v) return;
      let now = base + v.currentTime;
      const nk = nextKept(ks, now);
      if (nk === null) {
        v.pause();
        setPlaying(false);
        now = doc.trim[0];
        v.currentTime = now - base;
      } else if (nk > now + 0.02) {
        v.currentTime = nk - base;
        now = nk;
      }
      for (const s of doc.sounds) {
        if (lastT.current < s.t && now >= s.t && now - lastT.current < 0.5) {
          const a = new Audio(`/api/library/${s.library_id}/file`);
          a.volume = Math.min(1, s.volume);
          a.play().catch(() => {});
        }
      }
      lastT.current = now;
      setT(now);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [playing, data, doc, ks]);
  useEffect(() => {
    const m = music.current;
    if (!m) return;
    m.volume = Math.min(1, (doc?.music?.volume ?? 0.15) * 2);
    if (playing && doc?.music) m.play().catch(() => {});
    else m.pause();
  }, [playing, doc?.music]);

  function seek(time: number) {
    if (!data) return;
    const base = data.proxy.start ?? data.bounds[0];
    setT(time);
    lastT.current = time;
    if (video.current) video.current.currentTime = Math.max(0, time - base);
  }
  async function toggle() {
    const v = video.current;
    if (!v || !doc) return;
    if (playing) { v.pause(); setPlaying(false); return; }
    if (t >= doc.trim[1] - 0.05 || t < doc.trim[0]) seek(doc.trim[0]);
    await v.play().catch(() => {});
    setPlaying(true);
  }

  if (loading || !user || !data || !doc) {
    return error ? <div className="p-6"><ErrorText>{error}</ErrorText></div> : <Loading />;
  }
  const words = data.words;
  const style = (library.styles.find((s) => s.id === (data.clip.render?.style_id ?? DEFAULT_RENDER.style_id)) ??
                 library.styles[0])?.config ?? null;
  const subtitles = data.clip.render?.subtitles ?? true;
  const total = totalOf(ks);
  const selText = doc.texts.find((x) => x.id === selected);
  const selImage = doc.images.find((x) => x.id === selected);
  const selSound = doc.sounds.find((x) => x.id === selected);
  const selMarker = doc.markers.find((x) => x.id === selected);
  const images = library.logos.concat(library.images ?? []);
  const wordRange = (s: [number, number]): Range => [words[s[0]].start, wordSpan(words, s[1])[1]];
  // New items go where the playhead is, but always inside the trimmed clip.
  const at = Math.min(Math.max(t, doc.trim[0]), doc.trim[1] - 0.5);

  async function renderNow() {
    if (!(await save())) return;
    try {
      await api(`/clips/${id}/render`, { method: "POST", json: { ...DEFAULT_RENDER, ...data!.clip.render } });
      router.push(`/jobs/${data!.clip.job_id}`);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  // Plain elements (not a component defined here): a component created during render would be
  // re-mounted on every re-render, and a tap landing during an autosave would be lost.
  const toolBtn = (k: Tool, label: string) => (
    <button key={k} onClick={() => setTool(tool === k ? null : k)} aria-pressed={tool === k}
            className={`min-h-10 shrink-0 rounded-xl px-3 text-sm ${tool === k ? "accent-gradient" : "bg-white/8 text-white/70"}`}>{label}</button>
  );

  return (
    <div className="mx-auto max-w-5xl px-3 pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-[max(0.75rem,env(safe-area-inset-top))]">
      <header className="mb-3 flex items-center gap-2">
        <Link href={`/jobs/${data.clip.job_id}`} className="rounded-xl px-2 py-2 text-sm text-white/60">← Salir</Link>
        <p className="min-w-0 flex-1 truncate text-sm font-semibold">Editor · clip #{data.clip.index}</p>
        <button onClick={undo} disabled={!past.length} aria-label="Deshacer" className="h-10 w-10 rounded-xl bg-white/8 disabled:opacity-30">↶</button>
        <button onClick={redo} disabled={!future.length} aria-label="Rehacer" className="h-10 w-10 rounded-xl bg-white/8 disabled:opacity-30">↷</button>
        <span className="w-20 text-right text-xs text-white/50" aria-live="polite">
          {saved === "saved" ? "Guardado ✓" : saved === "saving" ? "Guardando…" : "Sin guardar"}
        </span>
      </header>
      <ErrorText>{error}</ErrorText>

      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <div className="space-y-3">
          {data.proxy.status === "ready" && data.proxy.url ? (
            <Preview video={video} src={data.proxy.url} aspect={data.clip.render?.format ?? "9:16"} t={t} doc={doc}
                     words={words} subtitles={subtitles} style={style} selected={selected} onSelect={setSelected}
                     onChange={setDoc} onCommit={() => commit()} onLoaded={() => seek(t)} />
          ) : (
            <div className="mx-auto grid aspect-[9/16] w-full max-w-[340px] place-items-center rounded-3xl bg-black/50 p-6 text-center text-sm text-white/60">
              {data.proxy.status === "failed" ? "No se pudo preparar la vista previa." : "Preparando la vista previa…"}
            </div>
          )}
          {doc.music && <audio ref={music} src={`/api/library/${doc.music.library_id}/file`} loop />}
          <div className="flex items-center justify-center gap-3">
            <button onClick={toggle} aria-label={playing ? "Pausa" : "Reproducir"}
                    className="accent-gradient h-14 w-14 rounded-full text-xl">{playing ? "❚❚" : "▶"}</button>
            <span className="text-sm text-white/60">{fmt(outT(ks, t))} / {fmt(total)}</span>
          </div>
        </div>

        <div className="min-w-0 space-y-3">
          <Timeline bounds={data.bounds} doc={doc} level={data.level} playhead={t} selected={selected}
                    rangeMode={rangeMode} range={range} onSeek={seek} onChange={setDoc} onCommit={() => commit()}
                    onSelect={setSelected} onRange={setRange} />

          <div className="flex flex-wrap gap-2">
            <button onClick={() => { setRangeMode(!rangeMode); setRange(null); }} aria-pressed={rangeMode}
                    className={`min-h-10 rounded-xl px-3 text-sm ${rangeMode ? "bg-amber-300 text-black" : "bg-white/8"}`}>
              {rangeMode ? "Arrastra sobre el video…" : "Seleccionar tramo"}
            </button>
            {range && (
              <>
                <Button variant="danger" className="!min-h-10 !px-3 text-sm" onClick={() => { apply((d) => d.cuts.push(range)); setRange(null); }}>Cortar tramo</Button>
                <Button variant="ghost" className="!min-h-10 !px-3 text-sm" onClick={() => { apply((d) => { d.cuts = subtractRange(d.cuts, range[0], range[1]); }); setRange(null); }}>Restaurar tramo</Button>
              </>
            )}
            <Button variant="ghost" className="!min-h-10 !px-3 text-sm" onClick={() => apply((d) => { d.cuts.push(...silenceCuts(words, d)); })}>
              Quitar silencios
            </Button>
          </div>

          <Transcript words={words} doc={doc} t={t} sel={wordSel} onSel={setWordSel} onSeek={seek} />
          {wordSel && (
            <div className="flex flex-wrap gap-2">
              <Button variant="danger" className="!min-h-10 !px-3 text-sm" onClick={() => { const r = wordRange(wordSel); apply((d) => d.cuts.push(r)); setWordSel(null); }}>
                Borrar {wordSel[1] - wordSel[0] + 1} palabra{wordSel[1] > wordSel[0] ? "s" : ""}
              </Button>
              <Button variant="ghost" className="!min-h-10 !px-3 text-sm" onClick={() => { const r = wordRange(wordSel); apply((d) => { d.cuts = subtractRange(d.cuts, r[0], r[1]); if (r[0] < d.trim[0]) d.trim[0] = r[0]; if (r[1] > d.trim[1]) d.trim[1] = r[1]; }); setWordSel(null); }}>
                Restaurar
              </Button>
              <Button variant="ghost" className="!min-h-10 !px-3 text-sm" onClick={() => setWordSel(null)}>Cancelar</Button>
            </div>
          )}

          <div className="flex gap-2 overflow-x-auto pb-1">
            {toolBtn("texto", "＋ Texto")}
            {toolBtn("imagen", "＋ Imagen")}
            {toolBtn("sonido", "＋ Sonido")}
            {toolBtn("musica", "♪ Música")}
            {toolBtn("marcador", "🚩 Marcador")}
          </div>

          {tool === "texto" && (
            <Button className="w-full" onClick={() => { const it = { id: uid(), text: "Escribe aquí", start: at, end: Math.min(doc.trim[1], at + 3), x: 0.5, y: 0.2, size: 90, color: "#FFFFFF", style: "title" as const, box_color: "#000000" }; apply((d) => d.texts.push(it)); setSelected(it.id); setTool(null); seek(at); }}>
              Añadir texto en {fmt(at)}
            </Button>
          )}
          {tool === "imagen" && (
            <Picker items={images} empty="Sube imágenes o logos en la biblioteca." onPick={(lid) => { const it = { id: uid(), library_id: lid, start: at, end: Math.min(doc.trim[1], at + 3), x: 0.5, y: 0.5, w: 0.35 }; apply((d) => d.images.push(it)); setSelected(it.id); setTool(null); }} image />
          )}
          {tool === "sonido" && (
            <Picker items={(library.sounds ?? []).concat(library.music)} empty="Sube efectos de sonido en la biblioteca." onPick={(lid) => { const it = { id: uid(), library_id: lid, t: at, volume: 1 }; apply((d) => d.sounds.push(it)); setSelected(it.id); setTool(null); }} />
          )}
          {tool === "musica" && (
            <div className="space-y-2 rounded-2xl bg-black/25 p-3">
              <Picker items={library.music} empty="Sube música en la biblioteca." onPick={(lid) => apply((d) => { d.music = { library_id: lid, volume: d.music?.volume ?? 0.15 }; })} selectedId={doc.music?.library_id} />
              {doc.music && (
                <>
                  <label className="flex items-center gap-3 text-xs text-white/60">Volumen
                    <input type="range" min={0.03} max={0.6} step={0.01} value={doc.music.volume} className="flex-1 accent-fuchsia-500"
                           onChange={(e) => setDoc({ ...doc, music: { ...doc.music!, volume: Number(e.target.value) } })} onPointerUp={() => commit()} />
                  </label>
                  <p className="text-xs text-white/40">En el video final la música baja sola cuando alguien habla.</p>
                  <button className="text-xs text-red-300" onClick={() => apply((d) => { d.music = null; })}>Quitar música</button>
                </>
              )}
            </div>
          )}
          {tool === "marcador" && (
            <Button className="w-full" onClick={() => { const it = { id: uid(), t: at, label: "" }; apply((d) => d.markers.push(it)); setSelected(it.id); setTool(null); }}>
              Poner marcador en {fmt(at)}
            </Button>
          )}

          {/* Properties of the selected item */}
          {selText && (
            <div className="space-y-2 rounded-2xl bg-black/25 p-3">
              <input className="input-glass !py-2" value={selText.text} maxLength={120} aria-label="Texto"
                     onChange={(e) => setDoc({ ...doc, texts: doc.texts.map((x) => x.id === selText.id ? { ...x, text: e.target.value || " " } : x) })}
                     onBlur={() => commit()} />
              <div className="flex flex-wrap items-center gap-2 text-sm">
                {(["title", "label", "outline"] as const).map((s) => (
                  <button key={s} onClick={() => apply((d) => { d.texts.find((x) => x.id === selText.id)!.style = s; })}
                          className={`min-h-9 rounded-lg px-3 ${selText.style === s ? "accent-gradient" : "bg-white/8"}`}>
                    {{ title: "Título", label: "Etiqueta", outline: "Bebas" }[s]}
                  </button>
                ))}
                <input type="color" aria-label="Color" value={selText.color} className="h-9 w-12 rounded"
                       onChange={(e) => apply((d) => { d.texts.find((x) => x.id === selText.id)!.color = e.target.value.toUpperCase(); })} />
                {selText.style === "label" && (
                  <input type="color" aria-label="Fondo" value={selText.box_color} className="h-9 w-12 rounded"
                         onChange={(e) => apply((d) => { d.texts.find((x) => x.id === selText.id)!.box_color = e.target.value.toUpperCase(); })} />
                )}
              </div>
              <label className="flex items-center gap-3 text-xs text-white/60">Tamaño
                <input type="range" min={30} max={200} value={selText.size} className="flex-1 accent-fuchsia-500"
                       onChange={(e) => setDoc({ ...doc, texts: doc.texts.map((x) => x.id === selText.id ? { ...x, size: Number(e.target.value) } : x) })}
                       onPointerUp={() => commit()} />
              </label>
              <p className="text-xs text-white/40">Arrástralo en la vista previa para moverlo; en la línea de tiempo cambia cuándo aparece.</p>
              <button className="text-xs text-red-300" onClick={() => { apply((d) => { d.texts = d.texts.filter((x) => x.id !== selText.id); }); setSelected(null); }}>Borrar texto</button>
            </div>
          )}
          {selImage && (
            <div className="space-y-2 rounded-2xl bg-black/25 p-3">
              <label className="flex items-center gap-3 text-xs text-white/60">Tamaño
                <input type="range" min={0.05} max={1} step={0.01} value={selImage.w} className="flex-1 accent-fuchsia-500"
                       onChange={(e) => setDoc({ ...doc, images: doc.images.map((x) => x.id === selImage.id ? { ...x, w: Number(e.target.value) } : x) })}
                       onPointerUp={() => commit()} />
              </label>
              <button className="text-xs text-red-300" onClick={() => { apply((d) => { d.images = d.images.filter((x) => x.id !== selImage.id); }); setSelected(null); }}>Borrar imagen</button>
            </div>
          )}
          {selSound && (
            <div className="space-y-2 rounded-2xl bg-black/25 p-3">
              <label className="flex items-center gap-3 text-xs text-white/60">Volumen
                <input type="range" min={0} max={2} step={0.05} value={selSound.volume} className="flex-1 accent-fuchsia-500"
                       onChange={(e) => setDoc({ ...doc, sounds: doc.sounds.map((x) => x.id === selSound.id ? { ...x, volume: Number(e.target.value) } : x) })}
                       onPointerUp={() => commit()} />
              </label>
              <button className="text-xs text-red-300" onClick={() => { apply((d) => { d.sounds = d.sounds.filter((x) => x.id !== selSound.id); }); setSelected(null); }}>Borrar sonido</button>
            </div>
          )}
          {selMarker && (
            <div className="flex items-center gap-2 rounded-2xl bg-black/25 p-3">
              <input className="input-glass !py-2" placeholder="Nombre del marcador" value={selMarker.label} maxLength={40}
                     onChange={(e) => setDoc({ ...doc, markers: doc.markers.map((x) => x.id === selMarker.id ? { ...x, label: e.target.value } : x) })}
                     onBlur={() => commit()} />
              <button className="text-xs text-red-300" onClick={() => { apply((d) => { d.markers = d.markers.filter((x) => x.id !== selMarker.id); }); setSelected(null); }}>Borrar</button>
            </div>
          )}

          <div className="flex gap-2 pt-2">
            <Button variant="ghost" className="flex-1" onClick={() => { if (confirm("¿Volver al clip original?")) { api(`/clips/${id}/edit`, { method: "DELETE" }).then(() => location.reload()); } }}>
              Deshacer todo
            </Button>
            <Button className="flex-[2]" onClick={renderNow} disabled={saved === "saving"}>
              Renderizar video final
            </Button>
          </div>
          <p className="text-xs text-white/40">
            Lo tachado y lo oscurecido no saldrá en el video. Todo se guarda solo; el video final lo genera el servidor
            con subtítulos, textos, imágenes, sonidos y música.
          </p>
        </div>
      </div>
    </div>
  );
}

function Picker({ items, empty, onPick, image, selectedId }: {
  items: { id: string; name: string }[]; empty: string; onPick: (id: string) => void; image?: boolean; selectedId?: string;
}) {
  if (!items.length) {
    return <p className="rounded-xl bg-black/25 p-3 text-sm text-white/50">{empty} <Link href="/biblioteca" className="text-fuchsia-300">Ir a la biblioteca</Link></p>;
  }
  return (
    <div className="flex gap-2 overflow-x-auto pb-1">
      {items.map((it) => (
        <button key={it.id} onClick={() => onPick(it.id)}
                className={`flex shrink-0 items-center gap-2 rounded-xl px-3 py-2 text-sm ${selectedId === it.id ? "accent-gradient" : "bg-white/8"}`}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {image && <img src={`/api/library/${it.id}/file`} alt="" className="h-8 w-8 rounded object-contain" />}
          {it.name}
        </button>
      ))}
    </div>
  );
}

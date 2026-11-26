"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { JobCard } from "@/components/JobCard";
import { RenderOptionsForm } from "@/components/RenderOptionsForm";
import { Loading, Shell } from "@/components/Shell";
import { Button, Card, ErrorText, ProgressBar } from "@/components/ui";
import { api, DEFAULT_RENDER, errorMessage, FORMAT_LABELS, LAYOUT_LABELS, uploadVideo, type Job,
         type RenderOptions } from "@/lib/api";
import { useLibrary } from "@/lib/useLibrary";
import { useMe } from "@/lib/useMe";

const OPTIONS_KEY = "clipeo.render";

function loadOptions(): RenderOptions {
  try {
    const saved = JSON.parse(localStorage.getItem(OPTIONS_KEY) ?? "null");
    // Gameplay rectangles belong to one video; don't carry them over.
    if (saved) return { ...DEFAULT_RENDER, ...saved, gameplay: null };
  } catch {}
  return DEFAULT_RENDER;
}

const DURATIONS = [15, 30, 45, 60, 90];

export default function Home() {
  const router = useRouter();
  const { user, loading } = useMe();
  const [tab, setTab] = useState<"link" | "file">("link");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [clipCount, setClipCount] = useState(5);
  const [clipDuration, setClipDuration] = useState(45);
  const [busy, setBusy] = useState(false);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const [render, setRender] = useState<RenderOptions>(DEFAULT_RENDER);
  const [showOptions, setShowOptions] = useState(false);
  const library = useLibrary(!!user);

  useEffect(() => setRender(loadOptions()), []);
  function changeRender(r: RenderOptions) {
    setRender(r);
    try {
      localStorage.setItem(OPTIONS_KEY, JSON.stringify(r));
    } catch {}
  }

  const loadJobs = useCallback(() => api<Job[]>("/jobs").then(setJobs).catch(() => {}), []);

  useEffect(() => {
    if (!user) return;
    loadJobs();
    const t = setInterval(loadJobs, 5000);
    return () => clearInterval(t);
  }, [user, loadJobs]);

  async function pasteFromClipboard() {
    try {
      setUrl((await navigator.clipboard.readText()).trim());
    } catch {}
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    // Drop library items that no longer exist (deleted on another device).
    const r = {
      ...render,
      music_id: library.music.some((m) => m.id === render.music_id) ? render.music_id : null,
      logo_id: library.logos.some((l) => l.id === render.logo_id) ? render.logo_id : null,
      style_id: library.styles.some((s) => s.id === render.style_id) ? render.style_id : DEFAULT_RENDER.style_id,
    };
    const opts = { clip_count: clipCount, clip_duration: clipDuration, render: r };
    try {
      let job: Job;
      if (tab === "link") {
        job = await api<Job>("/jobs", { method: "POST", json: { url, ...opts } });
      } else {
        if (!file) throw new Error("no file");
        setUploadPct(0);
        job = await uploadVideo(file, opts, setUploadPct);
      }
      router.push(`/jobs/${job.id}`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
      setUploadPct(null);
    }
  }

  if (loading || !user) return <Loading />;

  return (
    <Shell user={user}>
      <section className="mb-6">
        <h1 className="text-3xl font-bold leading-tight sm:text-4xl">
          Convierte tus videos en <span className="bg-gradient-to-r from-fuchsia-400 to-orange-300 bg-clip-text text-transparent">clips virales</span>
        </h1>
        <p className="mt-2 text-white/60">Pega un link o sube un video. Te devolvemos clips verticales con subtítulos.</p>
      </section>

      <Card>
        <div role="tablist" className="mb-5 grid grid-cols-2 gap-1 rounded-2xl bg-black/30 p-1">
          {(["link", "file"] as const).map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
              className={`min-h-11 rounded-xl text-sm font-semibold transition ${tab === t ? "bg-white/12 text-white" : "text-white/50"}`}
            >
              {t === "link" ? "Pegar link" : "Subir archivo"}
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="space-y-5">
          {tab === "link" ? (
            <div className="flex gap-2">
              <input
                className="input-glass"
                type="url"
                inputMode="url"
                required
                placeholder="https://youtube.com/watch?v=…"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
              <Button type="button" variant="ghost" onClick={pasteFromClipboard} className="shrink-0 px-4">
                Pegar
              </Button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              className="flex min-h-32 w-full flex-col items-center justify-center rounded-2xl border-2 border-dashed border-white/15 bg-black/20 p-4 text-center text-white/60 transition hover:border-fuchsia-400/60"
            >
              <span className="text-3xl">⬆</span>
              <span className="mt-2 text-sm">{file ? file.name : "Toca para elegir un video de tu teléfono o computadora"}</span>
              {file && <span className="text-xs text-white/40">{(file.size / 1024 / 1024).toFixed(1)} MB</span>}
              <input
                ref={fileInput}
                type="file"
                accept="video/*,audio/*"
                hidden
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </button>
          )}

          <p className="text-xs text-white/40">YouTube · Twitch · Kick · TikTok · Facebook</p>

          <div className="grid gap-5 sm:grid-cols-2">
            <label className="block">
              <span className="flex justify-between text-sm text-white/70">
                Cuántos clips <b className="text-white">{clipCount}</b>
              </span>
              <input
                type="range"
                min={1}
                max={30}
                value={clipCount}
                onChange={(e) => setClipCount(Number(e.target.value))}
                className="mt-3 w-full accent-fuchsia-500"
              />
            </label>
            <div>
              <span className="text-sm text-white/70">Duración de cada clip</span>
              <div className="mt-2 flex flex-wrap gap-2">
                {DURATIONS.map((d) => (
                  <button
                    type="button"
                    key={d}
                    onClick={() => setClipDuration(d)}
                    className={`min-h-10 rounded-xl px-3 text-sm transition ${clipDuration === d ? "accent-gradient text-white" : "bg-white/8 text-white/70"}`}
                  >
                    {d}s
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="rounded-2xl border border-white/10 bg-black/20">
            <button
              type="button"
              onClick={() => setShowOptions((v) => !v)}
              aria-expanded={showOptions}
              className="flex min-h-12 w-full items-center justify-between px-4 text-left text-sm"
            >
              <span>
                <b>Opciones del video</b>
                <span className="block text-xs text-white/50">
                  {FORMAT_LABELS[render.format].split(" ")[0]} · {LAYOUT_LABELS[render.layout]} ·{" "}
                  {render.subtitles ? "con subtítulos" : "sin subtítulos"}
                </span>
              </span>
              <span className={`transition ${showOptions ? "rotate-180" : ""}`}>⌄</span>
            </button>
            {showOptions && (
              <div className="border-t border-white/10 p-4">
                <RenderOptionsForm value={render} onChange={changeRender} styles={library.styles}
                                   logos={library.logos} music={library.music} />
              </div>
            )}
          </div>

          {uploadPct !== null && (
            <div className="space-y-1">
              <ProgressBar value={uploadPct} />
              <p className="text-xs text-white/50">Subiendo… {uploadPct}%</p>
            </div>
          )}
          <ErrorText>{error}</ErrorText>
          <Button type="submit" disabled={busy || (tab === "file" && !file)} className="w-full text-lg">
            {busy ? "Enviando…" : "Crear clips ✨"}
          </Button>
        </form>
      </Card>

      {jobs.length > 0 && (
        <section className="mt-8">
          <h2 className="mb-3 text-lg font-semibold">Tus proyectos</h2>
          <div className="space-y-3">
            {jobs.map((j) => (
              <JobCard key={j.id} job={j} />
            ))}
          </div>
        </section>
      )}
    </Shell>
  );
}

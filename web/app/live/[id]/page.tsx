"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ActivityChart } from "@/components/ActivityChart";
import { ClipCard } from "@/components/ClipCard";
import { ClipEditor } from "@/components/ClipEditor";
import { PushButton } from "@/components/PushButton";
import { Loading, Shell } from "@/components/Shell";
import { Button, Card, ErrorText } from "@/components/ui";
import {
  api, errorMessage, fmtTime, LIVE_STATUS, PLATFORM_LABELS,
  type Clip, type Live, type LiveActivity, type LiveEvent,
} from "@/lib/api";
import { useMe } from "@/lib/useMe";

const ACTIVE = new Set(["starting", "waiting", "recording", "reconnecting"]);
const SENS = [["low", "Baja"], ["normal", "Normal"], ["high", "Alta"]] as const;

export default function LivePage() {
  const { id } = useParams<{ id: string }>();
  const { user, loading } = useMe();
  const [live, setLive] = useState<Live | null>(null);
  const [activity, setActivity] = useState<LiveActivity | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [seconds, setSeconds] = useState(60);
  const [flash, setFlash] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Clip | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const upsert = useCallback((c: Clip) => {
    setClips((prev) => [c, ...prev.filter((p) => p.id !== c.id)].sort((a, b) => b.index - a.index));
  }, []);

  useEffect(() => {
    if (!user) return;
    api<Live>(`/lives/${id}`).then((l) => {
      setLive(l);
      api<Clip[]>(`/jobs/${l.job_id}/clips`).then((cs) => cs.forEach(upsert)).catch(() => {});
    }).catch(() => {});
    const es = new EventSource(`/api/lives/${id}/events`);
    es.onmessage = (e) => {
      const ev = JSON.parse(e.data) as LiveEvent;
      if (ev.type === "live") setLive(ev.live);
      else if (ev.type === "activity") setActivity(ev.activity);
      else if (ev.type === "clip") upsert(ev.clip);
    };
    return () => es.close();
  }, [id, user, upsert]);

  // Clips still rendering: check on them (their events may arrive on another channel).
  const rendering = clips.filter((c) => c.render_status === "rendering").map((c) => c.id).join(",");
  useEffect(() => {
    if (!rendering) return;
    const t = setInterval(() => rendering.split(",").forEach((cid) => api<Clip>(`/clips/${cid}`).then(upsert).catch(() => {})), 3000);
    return () => clearInterval(t);
  }, [rendering, upsert]);

  // A ticking clock between server updates.
  useEffect(() => {
    const base = activity?.recorded ?? live?.recorded_seconds ?? 0;
    setElapsed(base);
    if (live?.status !== "recording") return;
    const start = Date.now();
    const t = setInterval(() => setElapsed(base + (Date.now() - start) / 1000), 1000);
    return () => clearInterval(t);
  }, [activity?.recorded, live?.recorded_seconds, live?.status]);

  async function clipNow() {
    setError("");
    try {
      await api(`/lives/${id}/clip`, { method: "POST", json: { seconds } });
      setFlash(true);
      navigator.vibrate?.(60);
      setTimeout(() => setFlash(false), 1800);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function update(patch: Partial<Pick<Live, "auto_clips" | "sensitivity">>) {
    try {
      setLive(await api<Live>(`/lives/${id}`, { method: "PATCH", json: patch }));
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function stop() {
    if (!confirm("¿Terminar la grabación del directo?")) return;
    try {
      setLive(await api<Live>(`/lives/${id}/stop`, { method: "POST" }));
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  if (loading || !user) return <Loading />;
  if (!live) return <Shell user={user}><Card>Conectando…</Card></Shell>;
  const active = ACTIVE.has(live.status);
  const dot = live.status === "recording" ? "bg-red-500 animate-pulse" : active ? "bg-amber-400" : "bg-white/30";

  return (
    <Shell user={user}>
      <Link href="/" className="mb-4 inline-block text-sm text-white/60 hover:text-white">← Volver</Link>
      <Card className="mb-5 space-y-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-sm font-semibold">
              <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${dot}`} />
              {LIVE_STATUS[live.status]}
              {live.status === "recording" && <span className="font-mono text-white/70">{fmtTime(elapsed)}</span>}
            </p>
            <p className="mt-1 truncate text-xs text-white/50">
              {live.platform ? PLATFORM_LABELS[live.platform] ?? live.platform : ""} · {live.url}
            </p>
            {active && (
              <p className="mt-1 text-xs text-white/50">
                Chat: {live.chat_status === "connected" ? "conectado ✓"
                       : live.chat_status === "reconnecting" ? "reconectando…"
                       : ["twitch", "youtube", "kick"].includes(live.platform ?? "") ? "conectando…"
                       : "no disponible aquí (detectamos solo por el audio)"}
              </p>
            )}
          </div>
          {active && <Button variant="danger" onClick={stop} className="!min-h-10 shrink-0 !px-3 text-sm">Terminar</Button>}
        </div>

        {active && (
          <>
            <button
              onClick={clipNow}
              disabled={live.status !== "recording" && live.status !== "reconnecting"}
              className={`accent-gradient relative min-h-24 w-full rounded-3xl text-2xl font-black shadow-xl shadow-pink-500/30 transition active:scale-[0.97] disabled:opacity-40 ${flash ? "ring-4 ring-white/70" : ""}`}
            >
              {flash ? "¡Guardado! ✓" : "¡Clip ahora!"}
              <span className="block text-xs font-medium opacity-80">guarda el último {seconds === 60 ? "minuto" : `${seconds} s`}</span>
            </button>
            <div className="flex justify-center gap-2" role="group" aria-label="Duración del clip">
              {[30, 60, 90].map((s) => (
                <button key={s} onClick={() => setSeconds(s)} aria-pressed={seconds === s}
                        className={`min-h-9 rounded-xl px-3 text-sm ${seconds === s ? "bg-white/15" : "text-white/50"}`}>
                  {s}s
                </button>
              ))}
            </div>
            {activity && activity.interest.length > 1 && <ActivityChart interest={activity.interest} chat={activity.chat} />}
            <div className="space-y-3 rounded-2xl bg-black/20 p-4">
              <label className="flex items-center justify-between gap-3 text-sm">
                <span>Clips automáticos <span className="block text-xs text-white/50">risas, gritos, volumen y chat</span></span>
                <input type="checkbox" checked={live.auto_clips} onChange={(e) => update({ auto_clips: e.target.checked })}
                       className="h-6 w-6 accent-fuchsia-500" />
              </label>
              {live.auto_clips && (
                <div className="flex items-center gap-2 text-sm">
                  <span className="text-white/60">Sensibilidad</span>
                  {SENS.map(([v, l]) => (
                    <button key={v} onClick={() => update({ sensitivity: v })} aria-pressed={live.sensitivity === v}
                            className={`min-h-9 rounded-xl px-3 ${live.sensitivity === v ? "accent-gradient" : "bg-white/8 text-white/60"}`}>
                      {l}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <PushButton />
          </>
        )}
        {live.final_job_id && (
          <Link href={`/jobs/${live.final_job_id}`} className="accent-gradient flex min-h-12 items-center justify-center rounded-2xl font-semibold">
            Ver lo mejor del directo →
          </Link>
        )}
        {live.status === "finishing" && <p className="text-sm text-white/60">Juntando la grabación completa…</p>}
        <ErrorText>{error}</ErrorText>
      </Card>

      <h2 className="mb-3 text-lg font-semibold">Clips del directo {clips.length > 0 && <span className="text-white/40">({clips.length})</span>}</h2>
      {clips.length === 0 ? (
        <p className="text-sm text-white/50">
          {active ? "Aparecerán aquí en cuanto pase algo (o toca «¡Clip ahora!»)." : "Este directo no tiene clips."}
        </p>
      ) : (
        <section className="grid gap-4 sm:grid-cols-2">
          {clips.map((c) => <ClipCard key={c.id} clip={c} title="Directo" onEdit={() => setEditing(c)} />)}
        </section>
      )}
      {editing && <ClipEditor clip={editing} jobId={live.job_id} onClose={() => setEditing(null)} onQueued={upsert} />}
    </Shell>
  );
}

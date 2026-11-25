"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ClipCard } from "@/components/ClipCard";
import { jobTitle } from "@/components/JobCard";
import { Loading, Shell } from "@/components/Shell";
import { Button, Card, ErrorText, ProgressBar } from "@/components/ui";
import {
  api, errorMessage, JOB_ERRORS, PLATFORM_LABELS, STAGE_LABELS,
  type Clip, type Job, type JobEvent,
} from "@/lib/api";
import { useMe } from "@/lib/useMe";

const FINAL = new Set(["done", "failed", "canceled"]);

function byScore(a: Clip, b: Clip) {
  return b.score - a.score || a.index - b.index;
}

export default function JobPage() {
  const { id } = useParams<{ id: string }>();
  const { user, loading } = useMe();
  const [job, setJob] = useState<Job | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [missing, setMissing] = useState(false);
  const [moreCount, setMoreCount] = useState(3);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0); // bump to reconnect after "more clips"

  const addClip = useCallback((c: Clip) => {
    setClips((prev) => (prev.some((p) => p.id === c.id) ? prev : [...prev, c].sort(byScore)));
  }, []);

  useEffect(() => {
    if (!user) return;
    api<Clip[]>(`/jobs/${id}/clips`).then((cs) => setClips(cs.sort(byScore))).catch(() => {});
    let poll: ReturnType<typeof setInterval> | undefined;
    // Live progress + new clips via Server-Sent Events; polling as a fallback.
    const es = new EventSource(`/api/jobs/${id}/events`);
    es.onmessage = (e) => {
      const ev = JSON.parse(e.data) as JobEvent;
      if (ev.type === "clip") addClip(ev.clip);
      else {
        setJob(ev.job);
        if (FINAL.has(ev.job.status)) {
          es.close();
          api<Clip[]>(`/jobs/${id}/clips`).then((cs) => setClips(cs.sort(byScore))).catch(() => {});
        }
      }
    };
    es.onerror = () => {
      es.close();
      if (poll) return;
      const step = () =>
        Promise.all([api<Job>(`/jobs/${id}`), api<Clip[]>(`/jobs/${id}/clips`)])
          .then(([j, cs]) => {
            setJob(j);
            setClips(cs.sort(byScore));
            if (FINAL.has(j.status)) clearInterval(poll);
          })
          .catch(() => {
            setMissing(true);
            clearInterval(poll);
          });
      step();
      poll = setInterval(step, 3000);
    };
    return () => {
      es.close();
      clearInterval(poll);
    };
  }, [id, user, tick, addClip]);

  async function more() {
    setError("");
    try {
      setJob(await api<Job>(`/jobs/${id}/more`, { method: "POST", json: { count: moreCount } }));
      setTick((t) => t + 1);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  if (loading || !user) return <Loading />;
  const busy = job && !FINAL.has(job.status);
  const title = job ? jobTitle(job) : "";

  return (
    <Shell user={user}>
      <Link href="/" className="mb-4 inline-block text-sm text-white/60 hover:text-white">
        ← Volver
      </Link>
      {missing ? (
        <Card>No encontramos este proyecto.</Card>
      ) : !job ? (
        <Card>Conectando…</Card>
      ) : (
        <>
          <Card className="mb-6 space-y-4">
            <div>
              <p className="text-xs uppercase tracking-wider text-white/40">
                {job.platform ? PLATFORM_LABELS[job.platform] : "Archivo subido"}
              </p>
              <h1 className="mt-1 break-words text-lg font-semibold">{title}</h1>
              <p className="mt-1 text-sm text-white/50">
                {job.clip_count} clips · {job.clip_duration}s cada uno
              </p>
            </div>
            {job.status !== "done" && (
              <div className="space-y-2">
                <ProgressBar value={job.progress} />
                <div className="flex justify-between text-sm">
                  <span className={job.status === "failed" ? "text-red-300" : "text-white/80"}>
                    {STAGE_LABELS[job.stage] ?? job.stage}
                  </span>
                  <span className="text-white/50">{job.progress}%</span>
                </div>
              </div>
            )}
            {job.error && (
              <p className="rounded-xl bg-red-500/10 p-3 text-sm text-red-200">
                {JOB_ERRORS[job.error] ?? JOB_ERRORS.processing_failed}
              </p>
            )}
            {job.status === "done" && (
              <div className="flex items-center gap-2">
                <select
                  aria-label="Cuántos clips más"
                  value={moreCount}
                  onChange={(e) => setMoreCount(Number(e.target.value))}
                  className="min-h-12 rounded-2xl border border-white/10 bg-black/30 px-3"
                >
                  {[1, 2, 3, 5, 10].map((n) => (
                    <option key={n} value={n}>+{n}</option>
                  ))}
                </select>
                <Button onClick={more} variant="ghost" className="flex-1">
                  Más clips de este video
                </Button>
              </div>
            )}
            <ErrorText>{error}</ErrorText>
          </Card>

          {clips.length > 0 && (
            <section className="grid gap-4 sm:grid-cols-2">
              {clips.map((c) => (
                <ClipCard key={c.id} clip={c} title={title} />
              ))}
            </section>
          )}
          {busy && clips.length === 0 && job.stage === "cutting" && (
            <p className="text-center text-sm text-white/50">Los clips aparecerán aquí a medida que estén listos…</p>
          )}
        </>
      )}
    </Shell>
  );
}

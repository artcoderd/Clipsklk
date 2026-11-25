"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { jobTitle } from "@/components/JobCard";
import { Loading, Shell } from "@/components/Shell";
import { Card, ProgressBar } from "@/components/ui";
import { api, PLATFORM_LABELS, STAGE_LABELS, type Job } from "@/lib/api";
import { useMe } from "@/lib/useMe";

const FINAL = new Set(["done", "failed", "canceled"]);

export default function JobPage() {
  const { id } = useParams<{ id: string }>();
  const { user, loading } = useMe();
  const [job, setJob] = useState<Job | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (!user) return;
    let poll: ReturnType<typeof setInterval> | undefined;
    // Live progress via Server-Sent Events, with polling as a fallback.
    const es = new EventSource(`/api/jobs/${id}/events`);
    es.onmessage = (e) => {
      const data = JSON.parse(e.data) as Job;
      setJob(data);
      if (FINAL.has(data.status)) es.close();
    };
    es.onerror = () => {
      es.close();
      if (poll) return;
      const tick = () =>
        api<Job>(`/jobs/${id}`)
          .then((j) => {
            setJob(j);
            if (FINAL.has(j.status)) clearInterval(poll);
          })
          .catch(() => {
            setMissing(true);
            clearInterval(poll);
          });
      tick();
      poll = setInterval(tick, 3000);
    };
    return () => {
      es.close();
      clearInterval(poll);
    };
  }, [id, user]);

  if (loading || !user) return <Loading />;

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
        <Card className="space-y-5">
          <div>
            <p className="text-xs uppercase tracking-wider text-white/40">
              {job.platform ? PLATFORM_LABELS[job.platform] : "Archivo subido"}
            </p>
            <h1 className="mt-1 break-all text-lg font-semibold">{jobTitle(job)}</h1>
            <p className="mt-1 text-sm text-white/50">
              {job.clip_count} clips · {job.clip_duration}s cada uno
            </p>
          </div>
          <div className="space-y-2">
            <ProgressBar value={job.progress} />
            <div className="flex justify-between text-sm">
              <span className={job.status === "failed" ? "text-red-300" : "text-white/80"}>
                {STAGE_LABELS[job.stage] ?? job.stage}
              </span>
              <span className="text-white/50">{job.progress}%</span>
            </div>
          </div>
          {job.error && <p className="rounded-xl bg-red-500/10 p-3 text-sm text-red-200">{job.error}</p>}
          {job.status === "done" && (
            <p className="rounded-xl bg-emerald-500/10 p-3 text-sm text-emerald-200">
              Procesamiento terminado. Los clips aparecerán aquí cuando se active el motor de video (fase 2).
            </p>
          )}
        </Card>
      )}
    </Shell>
  );
}

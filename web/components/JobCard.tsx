import Link from "next/link";

import { PLATFORM_LABELS, STAGE_LABELS, type Job } from "@/lib/api";
import { ProgressBar } from "./ui";

export function jobTitle(job: Job) {
  if (job.source === "upload") return job.original_filename ?? "Archivo";
  return job.source_url ?? "Video";
}

export function JobCard({ job }: { job: Job }) {
  const active = job.status === "queued" || job.status === "running";
  return (
    <Link href={`/jobs/${job.id}`} className="glass block rounded-2xl p-4 transition hover:bg-white/10">
      <div className="flex items-center justify-between gap-3">
        <p className="min-w-0 truncate text-sm font-medium">{jobTitle(job)}</p>
        <span className="shrink-0 rounded-full bg-white/10 px-2.5 py-1 text-xs text-white/70">
          {job.platform ? PLATFORM_LABELS[job.platform] : "Archivo"}
        </span>
      </div>
      <div className="mt-3 flex items-center gap-3">
        <ProgressBar value={job.progress} />
        <span className={`shrink-0 text-xs ${job.status === "failed" ? "text-red-300" : "text-white/60"}`}>
          {active ? `${job.progress}%` : STAGE_LABELS[job.status] ?? job.status}
        </span>
      </div>
    </Link>
  );
}

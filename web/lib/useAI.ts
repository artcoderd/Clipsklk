"use client";

import { useEffect, useState } from "react";

import { api, type AIPrefs, type AIStatus } from "./api";

export function useAI(enabled = true) {
  const [status, setStatus] = useState<AIStatus | null>(null);
  const [prefs, setPrefs] = useState<AIPrefs | null>(null);
  useEffect(() => {
    if (!enabled) return;
    api<AIStatus>("/ai/status").then(setStatus).catch(() => {});
    api<AIPrefs>("/ai/prefs").then(setPrefs).catch(() => {});
  }, [enabled]);
  return { status, prefs, setPrefs, available: (status?.available.length ?? 0) > 0 };
}

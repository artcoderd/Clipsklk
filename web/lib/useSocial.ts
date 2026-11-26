"use client";

import { useCallback, useEffect, useState } from "react";

import { api, type Publication, type SocialAccount, type SocialStatus } from "./api";

export function useSocial(enabled = true) {
  const [status, setStatus] = useState<SocialStatus | null>(null);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [pubs, setPubs] = useState<Publication[]>([]);
  const reload = useCallback(() => {
    api<SocialStatus>("/social/status").then(setStatus).catch(() => {});
    api<SocialAccount[]>("/social/accounts").then(setAccounts).catch(() => {});
    api<Publication[]>("/publications").then(setPubs).catch(() => {});
  }, []);
  useEffect(() => {
    if (enabled) reload();
  }, [enabled, reload]);
  // While something is uploading (or due within a minute), check back every few seconds.
  const active = pubs.some((p) => p.status === "publishing" ||
    (p.status === "scheduled" && new Date(p.scheduled_at).getTime() - Date.now() < 60_000));
  useEffect(() => {
    if (!enabled || !active) return;
    const t = setInterval(reload, 4000);
    return () => clearInterval(t);
  }, [enabled, active, reload]);
  return { status, accounts, pubs, reload };
}

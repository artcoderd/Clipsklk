"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  api, errorMessage, PLATFORM_ICON, PLATFORM_NAMES, type Clip, type Publication, type SocialAccount, type SocialStatus,
} from "@/lib/api";
import { Button, ErrorText } from "./ui";

type TikTokInfo = { nickname: string | null; privacy_options: string[]; mode: "inbox" | "direct";
                    comment_disabled: boolean; duet_disabled: boolean; stitch_disabled: boolean };
const PRIVACY: Record<string, string> = {
  PUBLIC_TO_EVERYONE: "Todo el mundo", MUTUAL_FOLLOW_FRIENDS: "Amigos", FOLLOWER_OF_CREATOR: "Seguidores", SELF_ONLY: "Solo yo",
};
const LIMIT: Record<string, number> = { instagram: 2200, tiktok: 2200, telegram: 1024, facebook: 5000 };

function localInput(d: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function PublishSheet({ clip, accounts, status, onClose, onDone }: {
  clip: Clip; accounts: SocialAccount[]; status: SocialStatus | null; onClose: () => void; onDone: (p: Publication[]) => void;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [caption, setCaption] = useState(
    [clip.caption, clip.hashtags.map((t) => `#${t}`).join(" ")].filter(Boolean).join("\n\n"));
  const [when, setWhen] = useState<"now" | "later">("now");
  const [at, setAt] = useState(localInput(new Date(Date.now() + 3600_000)));
  const [tt, setTt] = useState<TikTokInfo | null>(null);
  const [privacy, setPrivacy] = useState("");
  const [allow, setAllow] = useState({ comment: true, duet: true, stitch: true });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const tiktok = accounts.find((a) => a.platform === "tiktok" && picked.includes(a.id));
  const direct = status?.tiktok_mode === "direct";

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = ""; };
  }, [onClose]);
  useEffect(() => {
    if (!tiktok) { setTt(null); return; }
    api<TikTokInfo>(`/social/accounts/${tiktok.id}/tiktok`).then(setTt).catch((e) => setError(errorMessage(e)));
  }, [tiktok]);

  const tightest = Math.min(...picked.map((id) => LIMIT[accounts.find((a) => a.id === id)?.platform ?? "facebook"] ?? 2200), 2200);

  async function send() {
    setBusy(true);
    setError("");
    try {
      const pubs = await api<Publication[]>(`/clips/${clip.id}/publish`, {
        method: "POST",
        json: {
          account_ids: picked, caption,
          scheduled_at: when === "later" ? new Date(at).toISOString() : null,
          options: tiktok ? { privacy_level: privacy || undefined, allow_comment: allow.comment, allow_duet: allow.duet, allow_stitch: allow.stitch } : {},
        },
      });
      onDone(pubs);
      onClose();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const ready = picked.length > 0 && caption.length <= tightest && (!tiktok || !direct || !!privacy);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/75 backdrop-blur-sm sm:items-center" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Publicar clip" onClick={(e) => e.stopPropagation()}
           className="max-h-[90dvh] w-full max-w-lg space-y-4 overflow-y-auto rounded-t-3xl border border-white/10 bg-[#15111c] p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] shadow-2xl sm:rounded-3xl">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Publicar clip #{clip.index}</h2>
          <button onClick={onClose} aria-label="Cerrar" className="h-10 w-10 rounded-full bg-white/10">✕</button>
        </div>

        {accounts.length === 0 ? (
          <p className="rounded-xl bg-black/25 p-3 text-sm text-white/60">
            Todavía no conectaste ninguna red. <Link href="/publicar" className="text-fuchsia-300">Conectar redes</Link>
          </p>
        ) : (
          <div className="space-y-2">
            {accounts.map((a) => (
              <label key={a.id} className={`flex items-center gap-3 rounded-xl bg-black/25 p-3 ${a.status !== "ok" ? "opacity-50" : ""}`}>
                <input type="checkbox" className="h-5 w-5 accent-fuchsia-500" disabled={a.status !== "ok"}
                       checked={picked.includes(a.id)}
                       onChange={(e) => setPicked((p) => e.target.checked ? [...p, a.id] : p.filter((x) => x !== a.id))} />
                <span className="text-lg">{PLATFORM_ICON[a.platform]}</span>
                <span className="min-w-0 flex-1 truncate text-sm">{a.name}
                  <span className="block text-xs text-white/40">{a.status === "ok" ? PLATFORM_NAMES[a.platform] : "Vuelve a conectarla en Publicar"}</span>
                </span>
              </label>
            ))}
          </div>
        )}

        <label className="block space-y-1.5">
          <span className="text-sm text-white/70">Texto</span>
          <textarea className="input-glass" rows={4} value={caption} onChange={(e) => setCaption(e.target.value)} />
          <span className={`text-xs ${caption.length > tightest ? "text-red-300" : "text-white/40"}`}>{caption.length}/{tightest}</span>
        </label>

        {tiktok && tt && (
          <div className="space-y-3 rounded-2xl border border-white/10 p-3 text-sm">
            <p>🎵 TikTok · <b>{tt.nickname ?? tiktok.name}</b></p>
            {direct ? (
              <>
                <label className="block space-y-1">
                  <span className="text-white/70">¿Quién puede verlo?</span>
                  <select className="input-glass" value={privacy} onChange={(e) => setPrivacy(e.target.value)} required>
                    <option value="">Elige una opción</option>
                    {tt.privacy_options.map((o) => <option key={o} value={o}>{PRIVACY[o] ?? o}</option>)}
                  </select>
                </label>
                <div className="flex flex-wrap gap-4 text-white/80">
                  {(["comment", "duet", "stitch"] as const).map((k) => (
                    <label key={k} className="flex items-center gap-2">
                      <input type="checkbox" className="h-4 w-4 accent-fuchsia-500" checked={allow[k]} disabled={tt[`${k}_disabled`]}
                             onChange={(e) => setAllow({ ...allow, [k]: e.target.checked })} />
                      {{ comment: "Comentarios", duet: "Dúo", stitch: "Stitch" }[k]}
                    </label>
                  ))}
                </div>
                <p className="text-xs text-white/40">Al publicar aceptas la Confirmación de uso de música de TikTok. Puede tardar unos minutos en aparecer en tu perfil.</p>
              </>
            ) : (
              <p className="text-xs text-white/50">Llegará a tu bandeja de TikTok: ábrelo en la app para terminar de publicarlo.</p>
            )}
          </div>
        )}

        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2">
            {(["now", "later"] as const).map((w) => (
              <button key={w} onClick={() => setWhen(w)} aria-pressed={when === w}
                      className={`min-h-11 rounded-xl text-sm ${when === w ? "accent-gradient" : "bg-white/8 text-white/70"}`}>
                {w === "now" ? "Ahora" : "Programar"}
              </button>
            ))}
          </div>
          {when === "later" && (
            <input type="datetime-local" className="input-glass" value={at} min={localInput(new Date())}
                   onChange={(e) => setAt(e.target.value)} aria-label="Fecha y hora" />
          )}
        </div>

        <ErrorText>{error}</ErrorText>
        <Button className="w-full" disabled={!ready || busy} onClick={send}>
          {busy ? "Enviando…" : when === "now" ? `Publicar en ${picked.length || ""} cuenta${picked.length === 1 ? "" : "s"}` : "Programar"}
        </Button>
      </div>
    </div>
  );
}

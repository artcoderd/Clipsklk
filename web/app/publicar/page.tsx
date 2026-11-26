"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";

import { Loading, Shell } from "@/components/Shell";
import { Button, Card, ErrorText, Field } from "@/components/ui";
import {
  api, errorMessage, PLATFORM_ICON, PLATFORM_NAMES, PUB_ERRORS, PUB_STATUS,
  type Platform, type SocialAccount,
} from "@/lib/api";
import { useMe } from "@/lib/useMe";
import { useSocial } from "@/lib/useSocial";

const fmt = (d: string) => new Date(d).toLocaleString("es", { dateStyle: "medium", timeStyle: "short" });
const CONNECT_ERRORS: Record<string, string> = {
  state: "La conexión caducó o no era tuya. Inténtalo de nuevo.",
  denied: "Cancelaste el permiso.",
  meta_no_pages: "No elegiste ninguna Página de Facebook. Instagram se publica a través de la Página vinculada.",
};

function Content() {
  const { user, loading } = useMe();
  const social = useSocial(!!user);
  const params = useSearchParams();
  const [tgToken, setTgToken] = useState("");
  const [tgChat, setTgChat] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (loading || !user || !social.status) return <Loading />;
  const accName = (id: string) => social.accounts.find((a) => a.id === id);
  const upcoming = social.pubs.filter((p) => p.status === "scheduled" || p.status === "publishing");
  const history = social.pubs.filter((p) => !(p.status === "scheduled" || p.status === "publishing"));

  async function connect(provider: "meta" | "tiktok") {
    setError("");
    try {
      const { url } = await api<{ url: string }>(`/social/connect/${provider}`);
      location.href = url;
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  async function addTelegram(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/social/telegram", { method: "POST", json: { bot_token: tgToken, chat_id: tgChat } });
      setTgToken("");
      setTgChat("");
      social.reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  const row = (a: SocialAccount) => (
    <li key={a.id} className="flex items-center gap-3 py-2">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {a.picture ? <img src={a.picture} alt="" className="h-9 w-9 rounded-full object-cover" /> :
        <span className="grid h-9 w-9 place-items-center rounded-full bg-white/10">{PLATFORM_ICON[a.platform]}</span>}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm">{a.name}</span>
        <span className={`text-xs ${a.status === "ok" ? "text-white/40" : "text-amber-300"}`}>
          {PLATFORM_NAMES[a.platform]}{a.status !== "ok" && " · hay que volver a conectarla"}
        </span>
      </span>
      <button className="text-xs text-red-300" onClick={() => api(`/social/accounts/${a.id}`, { method: "DELETE" }).then(social.reload)}>
        Desconectar
      </button>
    </li>
  );
  const group = (platforms: Platform[]) => social.accounts.filter((a) => platforms.includes(a.platform));

  return (
    <Shell user={user}>
      <h1 className="mb-1 text-2xl font-bold">Publicar</h1>
      <p className="mb-5 text-sm text-white/60">Conecta tus redes para publicar o programar clips desde Clipeo.</p>
      {params.get("connected") && (
        <p className="mb-4 rounded-xl bg-emerald-500/10 p-3 text-sm text-emerald-200">
          ¡Conectado! {params.get("n")} cuenta{params.get("n") === "1" ? "" : "s"} añadida{params.get("n") === "1" ? "" : "s"}.
        </p>
      )}
      {params.get("error") && (
        <p className="mb-4 rounded-xl bg-red-500/10 p-3 text-sm text-red-200">
          {CONNECT_ERRORS[params.get("error")!] ?? "No se pudo conectar. Inténtalo de nuevo."}
        </p>
      )}
      <ErrorText>{error}</ErrorText>

      <Card className="mb-4 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold">📘 Facebook y 📸 Instagram</h2>
            <p className="text-xs text-white/50">Reels en tus Páginas y en la cuenta profesional de Instagram vinculada.</p>
          </div>
          {social.status.meta ? <Button className="shrink-0 !min-h-10 text-sm" onClick={() => connect("meta")}>Conectar</Button>
            : <span className="shrink-0 text-xs text-white/40">No configurado</span>}
        </div>
        <ul className="divide-y divide-white/5">{group(["facebook", "instagram"]).map(row)}</ul>
      </Card>

      <Card className="mb-4 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold">🎵 TikTok</h2>
            <p className="text-xs text-white/50">
              {social.status.tiktok_mode === "inbox" ? "Los videos llegan a tu bandeja de TikTok para terminar de publicarlos en la app."
                : "Publicación directa con la privacidad que elijas."}
            </p>
          </div>
          {social.status.tiktok ? <Button className="shrink-0 !min-h-10 text-sm" onClick={() => connect("tiktok")}>Conectar</Button>
            : <span className="shrink-0 text-xs text-white/40">No configurado</span>}
        </div>
        <ul className="divide-y divide-white/5">{group(["tiktok"]).map(row)}</ul>
      </Card>

      <Card className="mb-6 space-y-3">
        <h2 className="font-semibold">✈️ Telegram</h2>
        <ul className="divide-y divide-white/5">{group(["telegram"]).map(row)}</ul>
        <details>
          <summary className="cursor-pointer text-sm text-fuchsia-300">Añadir un bot</summary>
          <form onSubmit={addTelegram} className="mt-3 space-y-3">
            <p className="text-xs text-white/50">
              1) Crea un bot con @BotFather y copia su token. 2) Añádelo como administrador a tu canal o grupo.
              3) Escribe el @usuario del canal o el ID del chat.
            </p>
            <Field label="Token del bot" value={tgToken} onChange={(e) => setTgToken(e.target.value)} required autoComplete="off" />
            <Field label="Canal o chat (@micanal o ID)" value={tgChat} onChange={(e) => setTgChat(e.target.value)} required />
            <Button type="submit" disabled={busy} className="w-full">{busy ? "Comprobando…" : "Conectar Telegram"}</Button>
          </form>
        </details>
      </Card>

      {[["Programados", upcoming], ["Historial", history]].map(([title, list]) => (list as typeof upcoming).length > 0 && (
        <Card key={title as string} className="mb-4">
          <h2 className="mb-2 font-semibold">{title as string}</h2>
          <ul className="divide-y divide-white/5 text-sm">
            {(list as typeof upcoming).map((p) => {
              const a = accName(p.account_id);
              return (
                <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <span className="min-w-0">
                    {a ? `${PLATFORM_ICON[a.platform]} ${a.name}` : "Cuenta borrada"}
                    <span className="block text-xs text-white/40">
                      {PUB_STATUS[p.status]} · {fmt(p.published_at ?? p.scheduled_at)}
                      {p.error && p.status === "failed" && ` · ${PUB_ERRORS[p.error] ?? p.error}`}
                    </span>
                  </span>
                  <span className="flex gap-3 text-xs">
                    {p.external_url && <a href={p.external_url} target="_blank" rel="noreferrer" className="text-fuchsia-300">Ver</a>}
                    {p.status === "scheduled" && <button className="text-red-300" onClick={() => api(`/publications/${p.id}`, { method: "DELETE" }).then(social.reload)}>Cancelar</button>}
                    {p.status === "failed" && <button className="text-fuchsia-300" onClick={() => api(`/publications/${p.id}/retry`, { method: "POST" }).then(social.reload)}>Reintentar</button>}
                  </span>
                </li>
              );
            })}
          </ul>
        </Card>
      ))}
    </Shell>
  );
}

export default function PublishPage() {
  return <Suspense><Content /></Suspense>;
}

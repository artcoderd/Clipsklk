"use client";

import { useCallback, useEffect, useState } from "react";

import { Loading, Shell } from "@/components/Shell";
import { Button, Card, ErrorText, ProgressBar } from "@/components/ui";
import { api, errorMessage, type MyBilling, type PackInfo, type PlanInfo } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type History = { kind: string; minutes: number; note: string; created_at: string };

const KIND: Record<string, string> = {
  usage: "Uso del plan",
  usage_credits: "Uso de minutos extra",
  purchase: "Compra",
  manual: "Ajuste",
  refund: "Devolución",
};
const SOURCE: Record<MyBilling["source"], string> = { free: "Gratis", stripe: "Tarjeta (Stripe)", manual: "Pago manual" };
const date = (d: string) => new Date(d).toLocaleDateString("es", { day: "numeric", month: "short", year: "numeric" });

export default function PlansPage() {
  const { user, loading } = useMe();
  const [me, setMe] = useState<MyBilling | null>(null);
  const [plans, setPlans] = useState<PlanInfo[]>([]);
  const [packs, setPacks] = useState<PackInfo[]>([]);
  const [history, setHistory] = useState<History[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(() => {
    api<MyBilling>("/billing/me").then(setMe).catch(() => {});
    api<{ plans: PlanInfo[]; packs: PackInfo[] }>("/billing/plans")
      .then((r) => {
        setPlans(r.plans);
        setPacks(r.packs);
      })
      .catch(() => {});
    api<History[]>("/billing/history").then(setHistory).catch(() => {});
  }, []);

  useEffect(() => {
    if (!user) return;
    load();
    const q = new URLSearchParams(location.search).get("pago");
    if (q === "ok") setNotice("¡Pago recibido! Tu plan o tus minutos se activan en unos segundos.");
    if (q === "cancelado") setNotice("Cancelaste el pago. No se cobró nada.");
    if (q === "ok") setTimeout(load, 4000);
  }, [user, load]);

  async function go(path: string, json?: unknown, key = path) {
    setBusy(key);
    setError("");
    try {
      const { url } = await api<{ url: string }>(path, { method: "POST", json: json ?? {} });
      location.href = url;
    } catch (e) {
      setError(errorMessage(e));
      setBusy("");
    }
  }

  if (loading || !user) return <Loading />;
  const current = me?.plan.id;

  function whatsapp(what: string) {
    if (!me?.whatsapp) return null;
    const url = me.whatsapp.replace(/text=[^&]*/, "text=" + encodeURIComponent(
      `Hola, quiero pagar ${what} de Clipeo. Mi cuenta: ${user!.email}`));
    return (
      <a href={url} target="_blank" rel="noopener" className="block rounded-2xl border border-emerald-400/30 px-4 py-2.5 text-center text-sm text-emerald-200 hover:bg-emerald-500/10">
        Pagar por WhatsApp
      </a>
    );
  }

  return (
    <Shell user={user}>
      <h1 className="mb-4 text-2xl font-bold">Tu plan</h1>
      {notice && <p className="mb-4 rounded-2xl bg-emerald-500/10 p-3 text-sm text-emerald-200">{notice}</p>}

      {me && (
        <Card className="mb-6 space-y-3">
          {me.unlimited ? (
            <p>Tu cuenta es de administración: no tiene límites de minutos.</p>
          ) : (
            <>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-lg font-semibold">
                  {me.plan.name} <span className="text-sm font-normal text-white/50">· {SOURCE[me.source]}</span>
                </p>
                <p className="text-sm text-white/60">
                  {me.period_end
                    ? `${me.cancel_at_period_end || me.source === "manual" ? "Termina" : "Se renueva"} el ${date(me.period_end)}`
                    : "Se reinicia cada mes"}
                </p>
              </div>
              <ProgressBar value={me.allowance ? Math.min(100, (me.used / me.allowance) * 100) : 100} />
              <p className="text-sm text-white/70">
                {Math.round(me.used)} de {me.allowance} minutos del plan usados
                {me.credits > 0 && <> · <b>{Math.round(me.credits)}</b> minutos extra</>}
              </p>
              <p className="text-2xl font-bold">
                {Math.floor(me.left)} <span className="text-sm font-normal text-white/50">minutos disponibles</span>
              </p>
              <p className="text-xs text-white/40">
                Se descuenta la duración del video original que procesamos (un video de 40 min usa 40 minutos; volver a
                pedir clips del mismo video no cobra otra vez). En directo, cada minuto grabado cuenta.
              </p>
              {me.status === "past_due" && (
                <p className="text-sm text-amber-200">Stripe no pudo cobrar tu tarjeta. Actualízala en «Administrar pago».</p>
              )}
              {me.can_manage && (
                <Button variant="ghost" disabled={busy === "portal"} onClick={() => go("/billing/portal", {}, "portal")}>
                  Administrar pago, cambiar o cancelar plan
                </Button>
              )}
            </>
          )}
        </Card>
      )}
      <ErrorText>{error}</ErrorText>

      <h2 className="mb-3 mt-2 text-lg font-semibold">Planes</h2>
      <div className="mb-6 grid gap-3 sm:grid-cols-3">
        {plans.map((p) => (
          <Card key={p.id} className={`flex flex-col gap-3 ${p.id === current ? "ring-2 ring-fuchsia-400/60" : ""}`}>
            <div>
              <p className="text-lg font-semibold">{p.name}</p>
              <p className="text-2xl font-bold">{p.price_label}</p>
            </div>
            <ul className="flex-1 space-y-1 text-sm text-white/70">
              <li>{p.monthly_minutes.toLocaleString("es")} minutos al mes</li>
              <li>{p.live ? "Clips de directos en vivo" : "Sin directos en vivo"}</li>
              <li>{p.ai ? "Selección con IA" : "Selección automática sin IA"}</li>
              <li>{p.max_active_jobs} {p.max_active_jobs === 1 ? "video" : "videos"} a la vez</li>
            </ul>
            {p.id === current ? (
              <p className="text-center text-sm text-fuchsia-200">Tu plan actual</p>
            ) : p.id === "free" ? null : (
              <div className="space-y-2">
                {p.buyable && me?.stripe && (
                  me.source === "stripe" && me.can_manage ? (
                    <Button variant="ghost" className="w-full" onClick={() => go("/billing/portal", {}, "portal")}>
                      Cambiar en el portal
                    </Button>
                  ) : (
                    <Button className="w-full" disabled={!!busy} onClick={() => go("/billing/checkout", { plan_id: p.id }, p.id)}>
                      {busy === p.id ? "Abriendo…" : "Pagar con tarjeta"}
                    </Button>
                  )
                )}
                {whatsapp(`el plan ${p.name}`)}
              </div>
            )}
          </Card>
        ))}
      </div>

      {packs.length > 0 && (
        <>
          <h2 className="mb-1 text-lg font-semibold">Minutos extra</h2>
          <p className="mb-3 text-sm text-white/50">No caducan y se usan cuando se acaban los de tu plan.</p>
          <div className="mb-6 grid gap-3 sm:grid-cols-2">
            {packs.map((k) => (
              <Card key={k.id} className="flex flex-col gap-3">
                <p className="flex items-baseline justify-between">
                  <span className="font-semibold">{k.name}</span>
                  <span className="text-xl font-bold">{k.price_label}</span>
                </p>
                {k.buyable && me?.stripe && (
                  <Button disabled={!!busy} onClick={() => go("/billing/checkout", { pack_id: k.id }, k.id)}>
                    {busy === k.id ? "Abriendo…" : "Comprar con tarjeta"}
                  </Button>
                )}
                {whatsapp(`el paquete de ${k.name}`)}
              </Card>
            ))}
          </div>
        </>
      )}
      {me?.whatsapp && (
        <p className="mb-6 text-xs text-white/40">
          Si pagas por WhatsApp (transferencia, depósito…), activamos tu plan o tus minutos a mano en cuanto confirmemos el pago.
        </p>
      )}

      {history.length > 0 && (
        <Card>
          <h2 className="mb-3 text-lg font-semibold">Movimientos</h2>
          <ul className="divide-y divide-white/5 text-sm">
            {history.map((h, i) => (
              <li key={i} className="flex justify-between gap-3 py-2">
                <span className="min-w-0 truncate">
                  {KIND[h.kind] ?? h.kind} <span className="text-white/40">· {h.note}</span>
                </span>
                <span className={`shrink-0 ${h.minutes > 0 ? "text-emerald-300" : "text-white/60"}`}>
                  {h.minutes > 0 ? "+" : ""}
                  {h.minutes} min · {date(h.created_at)}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </Shell>
  );
}

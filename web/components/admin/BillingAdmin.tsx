"use client";

import { useCallback, useEffect, useState } from "react";

import { Button, Card, ErrorText } from "@/components/ui";
import { api, errorMessage, STAGE_LABELS } from "@/lib/api";

type Usage = {
  user_id: string;
  email: string;
  name: string;
  role: string;
  is_active: boolean;
  unlimited: boolean;
  plan_id: string;
  plan_name: string;
  source: "free" | "stripe" | "manual";
  status: string;
  period_end: string | null;
  allowance: number;
  used: number;
  credits: number;
  left: number;
  minutes_30d: number;
  jobs_30d: number;
  active_jobs: number;
};
type ActiveJob = { id: string; email: string; title: string | null; source: string; status: string; stage: string; progress: number; created_at: string };
type PlanRow = {
  id: string; name: string; price_label: string; price_cents: number; monthly_minutes: number; live: boolean; ai: boolean;
  max_active_jobs: number; stripe_price_id: string | null; active: boolean; sort: number;
};
type PackRow = { id: string; name: string; minutes: number; price_label: string; price_cents: number; stripe_price_id: string | null; active: boolean };
type Settings = { public_signup: boolean; costs: Record<string, number> };
type Finance = {
  month: string;
  subscribers: { plan: string; source: string; count: number; price: number }[];
  income: { plans: number; packs: number; total: number };
  costs: { fixed: number; ai: number; payment_fees: number; total: number };
  margin: number;
  minutes_processed: number;
  cost_per_minute: number | null;
};

const COST_LABELS: Record<string, string> = {
  gpu_server: "Servidor GPU (US$/mes)",
  storage: "Almacenamiento (US$/mes)",
  domain_other: "Dominio y otros (US$/mes)",
  ai_per_million_input: "IA: US$ por millón de tokens de entrada",
  ai_per_million_output: "IA: US$ por millón de tokens de salida",
  stripe_percent: "Comisión de Stripe (%)",
};
const SOURCE: Record<string, string> = { free: "gratis", stripe: "Stripe", manual: "manual" };
const usd = (n: number) => `US$${n.toLocaleString("es", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const date = (d: string) => new Date(d).toLocaleDateString("es", { day: "numeric", month: "short" });
const input = "w-full rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-sm";

function ManualPayment({ row, plans, onDone }: { row: Usage; plans: PlanRow[]; onDone: () => void }) {
  const [planId, setPlanId] = useState(row.plan_id === "free" ? plans.find((p) => p.id !== "free")?.id ?? "free" : row.plan_id);
  const [months, setMonths] = useState(1);
  const [minutes, setMinutes] = useState(60);
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(path: string, json: unknown) {
    setBusy(true);
    setError("");
    try {
      await api(path, { method: "POST", json });
      onDone();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-3 space-y-3 rounded-2xl bg-white/5 p-3">
      <p className="text-xs text-white/50">Pago recibido a mano (WhatsApp, transferencia…). Queda registrado en la actividad.</p>
      <div className="grid gap-2 sm:grid-cols-2">
        <input className={input} placeholder="Monto recibido (ej. MXN 350)" value={amount} onChange={(e) => setAmount(e.target.value)} />
        <input className={input} placeholder="Nota (opcional)" value={note} onChange={(e) => setNote(e.target.value)} />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <select className={`${input} !w-auto`} value={planId} onChange={(e) => setPlanId(e.target.value)} aria-label="Plan">
          {plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        {planId !== "free" && (
          <select className={`${input} !w-auto`} value={months} onChange={(e) => setMonths(+e.target.value)} aria-label="Meses">
            {[1, 2, 3, 6, 12].map((m) => <option key={m} value={m}>{m} {m === 1 ? "mes" : "meses"}</option>)}
          </select>
        )}
        <Button className="!min-h-9 !px-3 text-xs" disabled={busy || row.source === "stripe"}
          onClick={() => run(`/admin/users/${row.user_id}/plan`, { plan_id: planId, months, amount, note })}>
          {planId === "free" ? "Pasar a gratis" : "Activar plan"}
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input type="number" className={`${input} !w-28`} value={minutes} onChange={(e) => setMinutes(+e.target.value)} aria-label="Minutos" />
        <Button variant="ghost" className="!min-h-9 !px-3 text-xs" disabled={busy || !minutes}
          onClick={() => run(`/admin/users/${row.user_id}/minutes`, { minutes, amount, note })}>
          {minutes < 0 ? "Quitar minutos" : "Añadir minutos"}
        </Button>
      </div>
      <ErrorText>{error}</ErrorText>
    </div>
  );
}

function PlanEditor({ plan, onSaved }: { plan: PlanRow; onSaved: () => void }) {
  const [p, setP] = useState(plan);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const set = <K extends keyof PlanRow>(k: K, v: PlanRow[K]) => { setP({ ...p, [k]: v }); setSaved(false); };
  async function save() {
    setError("");
    try {
      const { id, ...body } = p;
      await api(`/admin/plans/${id}`, { method: "PUT", json: body });
      setSaved(true);
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <div className="space-y-2 rounded-2xl bg-white/5 p-3 text-sm">
      <p className="font-mono text-xs text-white/40">{p.id}</p>
      <div className="grid grid-cols-2 gap-2">
        <label className="space-y-1"><span className="text-xs text-white/50">Nombre</span>
          <input className={input} value={p.name} onChange={(e) => set("name", e.target.value)} /></label>
        <label className="space-y-1"><span className="text-xs text-white/50">Precio visible</span>
          <input className={input} value={p.price_label} onChange={(e) => set("price_label", e.target.value)} /></label>
        <label className="space-y-1"><span className="text-xs text-white/50">Precio (centavos US$)</span>
          <input type="number" className={input} value={p.price_cents} onChange={(e) => set("price_cents", +e.target.value)} /></label>
        <label className="space-y-1"><span className="text-xs text-white/50">Minutos al mes</span>
          <input type="number" className={input} value={p.monthly_minutes} onChange={(e) => set("monthly_minutes", +e.target.value)} /></label>
        <label className="space-y-1"><span className="text-xs text-white/50">Videos a la vez</span>
          <input type="number" min={1} className={input} value={p.max_active_jobs} onChange={(e) => set("max_active_jobs", +e.target.value)} /></label>
        <label className="space-y-1"><span className="text-xs text-white/50">Stripe price id</span>
          <input className={`${input} font-mono`} placeholder="price_…" value={p.stripe_price_id ?? ""} onChange={(e) => set("stripe_price_id", e.target.value || null)} /></label>
      </div>
      <div className="flex flex-wrap gap-4">
        {([["live", "Directos"], ["ai", "IA"], ["active", "Visible"]] as const).map(([k, label]) => (
          <label key={k} className="flex items-center gap-2">
            <input type="checkbox" checked={p[k]} onChange={(e) => set(k, e.target.checked)} /> {label}
          </label>
        ))}
      </div>
      <div className="flex items-center gap-3">
        <Button className="!min-h-9 !px-3 text-xs" onClick={save}>Guardar</Button>
        {saved && <span className="text-xs text-emerald-300">Guardado</span>}
      </div>
      <ErrorText>{error}</ErrorText>
    </div>
  );
}

function PackEditor({ pack, onSaved }: { pack: PackRow; onSaved: () => void }) {
  const [k, setK] = useState(pack);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const set = <F extends keyof PackRow>(f: F, v: PackRow[F]) => { setK({ ...k, [f]: v }); setSaved(false); };
  async function save() {
    setError("");
    try {
      const { id, ...body } = k;
      await api(`/admin/packs/${id}`, { method: "PUT", json: body });
      setSaved(true);
      onSaved();
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <div className="space-y-2 rounded-2xl bg-white/5 p-3 text-sm">
      <div className="grid grid-cols-2 gap-2">
        <input className={input} aria-label="Nombre" value={k.name} onChange={(e) => set("name", e.target.value)} />
        <input className={input} aria-label="Precio visible" value={k.price_label} onChange={(e) => set("price_label", e.target.value)} />
        <input type="number" className={input} aria-label="Minutos" value={k.minutes} onChange={(e) => set("minutes", +e.target.value)} />
        <input type="number" className={input} aria-label="Precio en centavos" value={k.price_cents} onChange={(e) => set("price_cents", +e.target.value)} />
        <input className={`${input} col-span-2 font-mono`} placeholder="Stripe price id (price_…)" value={k.stripe_price_id ?? ""} onChange={(e) => set("stripe_price_id", e.target.value || null)} />
      </div>
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2"><input type="checkbox" checked={k.active} onChange={(e) => set("active", e.target.checked)} /> Visible</label>
        <Button className="!min-h-9 !px-3 text-xs" onClick={save}>Guardar</Button>
        {saved && <span className="text-xs text-emerald-300">Guardado</span>}
      </div>
      <ErrorText>{error}</ErrorText>
    </div>
  );
}

export function BillingAdmin({ isOwner }: { isOwner: boolean }) {
  const [usage, setUsage] = useState<Usage[]>([]);
  const [jobs, setJobs] = useState<ActiveJob[]>([]);
  const [plans, setPlans] = useState<PlanRow[]>([]);
  const [packs, setPacks] = useState<PackRow[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [costs, setCosts] = useState<Record<string, number>>({});
  const [finance, setFinance] = useState<Finance | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [showPlans, setShowPlans] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api<Usage[]>("/admin/usage").then(setUsage).catch(() => {});
    api<ActiveJob[]>("/admin/jobs/active").then(setJobs).catch(() => {});
    api<{ plans: PlanRow[]; packs: PackRow[] }>("/admin/plans").then((r) => { setPlans(r.plans); setPacks(r.packs); }).catch(() => {});
    api<Settings>("/admin/settings").then((s) => { setSettings(s); setCosts(s.costs); }).catch(() => {});
    if (isOwner) api<Finance>("/admin/finance").then(setFinance).catch(() => {});
  }, [isOwner]);

  useEffect(() => {
    load();
    const t = setInterval(() => api<ActiveJob[]>("/admin/jobs/active").then(setJobs).catch(() => {}), 5000);
    return () => clearInterval(t);
  }, [load]);

  async function saveSettings(patch: Partial<Settings>) {
    setError("");
    try {
      const s = await api<Settings>("/admin/settings", { method: "PUT", json: patch });
      setSettings(s);
      setCosts(s.costs);
      load();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  const shown = usage.filter((u) => !query || u.email.includes(query.toLowerCase()) || u.name.toLowerCase().includes(query.toLowerCase()));

  return (
    <>
      {finance && (
        <Card className="mb-6 space-y-3">
          <h2 className="text-lg font-semibold">Este mes ({finance.month})</h2>
          <div className="grid grid-cols-3 gap-3 text-center">
            <div><p className="text-xl font-bold">{usd(finance.income.total)}</p><p className="text-xs text-white/50">Ingresos estimados</p></div>
            <div><p className="text-xl font-bold">{usd(finance.costs.total)}</p><p className="text-xs text-white/50">Costos</p></div>
            <div>
              <p className={`text-xl font-bold ${finance.margin < 0 ? "text-red-300" : "text-emerald-300"}`}>{usd(finance.margin)}</p>
              <p className="text-xs text-white/50">Margen</p>
            </div>
          </div>
          <ul className="space-y-1 text-sm text-white/60">
            {finance.subscribers.map((s) => (
              <li key={s.plan + s.source}>{s.count} × {s.plan} ({SOURCE[s.source]}) · {usd(s.price)}</li>
            ))}
            <li>Paquetes de minutos: {usd(finance.income.packs)}</li>
            <li>
              Costos: fijos {usd(finance.costs.fixed)} · IA {usd(finance.costs.ai)} · comisiones {usd(finance.costs.payment_fees)}
            </li>
            <li>
              {finance.minutes_processed.toLocaleString("es")} minutos de video procesados
              {finance.cost_per_minute !== null && <> · {usd(finance.cost_per_minute)} por minuto</>}
            </li>
          </ul>
          <p className="text-xs text-white/40">Estimación con los precios de los planes activos; el dato exacto de cobros está en Stripe.</p>
        </Card>
      )}

      {settings && (
        <Card className="mb-6 space-y-4">
          <h2 className="text-lg font-semibold">Ajustes</h2>
          <label className="flex items-center justify-between gap-3">
            <span>
              <span className="block">Registro público</span>
              <span className="text-xs text-white/50">
                {settings.public_signup ? "Cualquiera puede crear una cuenta (plan gratis)." : "Solo se entra con invitación."}
              </span>
            </span>
            <input type="checkbox" className="h-6 w-6" disabled={!isOwner} checked={settings.public_signup}
              onChange={(e) => saveSettings({ public_signup: e.target.checked })} aria-label="Registro público" />
          </label>
          {isOwner && (
            <div className="space-y-2">
              <p className="text-sm text-white/70">Costos del servidor (para calcular el margen)</p>
              <div className="grid gap-2 sm:grid-cols-2">
                {Object.keys(COST_LABELS).map((k) => (
                  <label key={k} className="space-y-1">
                    <span className="text-xs text-white/50">{COST_LABELS[k]}</span>
                    <input type="number" step="0.01" min={0} className={input} value={costs[k] ?? 0}
                      onChange={(e) => setCosts({ ...costs, [k]: +e.target.value })} />
                  </label>
                ))}
              </div>
              <Button variant="ghost" className="!min-h-9 !px-3 text-xs" onClick={() => saveSettings({ costs })}>Guardar costos</Button>
            </div>
          )}
          <ErrorText>{error}</ErrorText>
        </Card>
      )}

      <Card className="mb-6">
        <h2 className="mb-3 text-lg font-semibold">Trabajos activos ({jobs.length})</h2>
        {jobs.length === 0 ? (
          <p className="text-sm text-white/50">No hay nada procesándose ahora.</p>
        ) : (
          <ul className="divide-y divide-white/5 text-sm">
            {jobs.map((j) => (
              <li key={j.id} className="flex justify-between gap-3 py-2">
                <span className="min-w-0 truncate">{j.title ?? "Video"} <span className="text-white/40">· {j.email}</span></span>
                <span className="shrink-0 text-white/60">{STAGE_LABELS[j.stage] ?? j.stage} · {j.progress}%</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="mb-6">
        <h2 className="mb-3 text-lg font-semibold">Planes y consumo</h2>
        <input className={`${input} mb-3`} placeholder="Buscar por correo o nombre" value={query} onChange={(e) => setQuery(e.target.value)} />
        <ul className="divide-y divide-white/5">
          {shown.map((u) => (
            <li key={u.user_id} className="py-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate">{u.email}</p>
                  <p className="text-xs text-white/40">
                    {u.unlimited ? "Sin límites (administración)" : (
                      <>
                        {u.plan_name} ({SOURCE[u.source]}{u.period_end ? ` hasta ${date(u.period_end)}` : ""}) ·{" "}
                        {Math.round(u.used)}/{u.allowance} min{u.credits > 0 && ` + ${Math.round(u.credits)} extra`}
                      </>
                    )}{" "}
                    · {u.minutes_30d} min procesados y {u.jobs_30d} videos en 30 días
                    {u.active_jobs > 0 && ` · ${u.active_jobs} en proceso`}
                  </p>
                </div>
                {isOwner && !u.unlimited && (
                  <Button variant="ghost" className="!min-h-9 !px-3 text-xs" onClick={() => setOpen(open === u.user_id ? null : u.user_id)}>
                    {open === u.user_id ? "Cerrar" : "Pago manual"}
                  </Button>
                )}
              </div>
              {open === u.user_id && (
                <ManualPayment row={u} plans={plans.filter((p) => p.active)} onDone={() => { setOpen(null); load(); }} />
              )}
            </li>
          ))}
        </ul>
      </Card>

      {isOwner && (
        <Card className="mb-6 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">Editar planes y paquetes</h2>
            <Button variant="ghost" className="!min-h-9 !px-3 text-xs" onClick={() => setShowPlans(!showPlans)}>
              {showPlans ? "Ocultar" : "Mostrar"}
            </Button>
          </div>
          {showPlans && (
            <>
              <p className="text-xs text-white/50">
                Crea el producto y su precio en Stripe y pega aquí el «price id» para que se pueda pagar con tarjeta.
                Sin price id, el plan solo se paga por WhatsApp.
              </p>
              {plans.map((p) => <PlanEditor key={p.id} plan={p} onSaved={load} />)}
              <h3 className="pt-2 font-semibold">Paquetes de minutos</h3>
              {packs.map((k) => <PackEditor key={k.id} pack={k} onSaved={load} />)}
            </>
          )}
        </Card>
      )}
    </>
  );
}

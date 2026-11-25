"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { Loading, Shell } from "@/components/Shell";
import { Button, Card, ErrorText, Field } from "@/components/ui";
import { api, ApiError, errorMessage, type Role, type User } from "@/lib/api";
import { useMe } from "@/lib/useMe";

type Invitation = {
  id: string;
  email: string;
  role: Role;
  expires_at: string;
  used_at: string | null;
  revoked_at: string | null;
  created_at: string;
  link?: string;
};
type Activity = { id: number; user_id: string | null; action: string; detail: Record<string, unknown>; ip: string | null; created_at: string };
type Stats = { users: number; active_jobs: number; jobs_total: number };

const ROLE_LABEL: Record<Role, string> = { owner: "Dueño", admin: "Administrador", user: "Usuario" };
const fmt = (d: string) => new Date(d).toLocaleString("es", { dateStyle: "short", timeStyle: "short" });

function inviteState(i: Invitation) {
  if (i.used_at) return "Usada";
  if (i.revoked_at) return "Revocada";
  if (new Date(i.expires_at) < new Date()) return "Caducada";
  return "Pendiente";
}

function TwoFactorSetup({ onDone }: { onDone: (u: User) => void }) {
  const [setup, setSetup] = useState<{ secret: string; qr_data_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");

  async function start() {
    setError("");
    try {
      setSetup(await api("/auth/2fa/setup", { method: "POST" }));
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  async function enable(e: FormEvent) {
    e.preventDefault();
    try {
      onDone(await api<User>("/auth/2fa/enable", { method: "POST", json: { code } }));
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Card className="space-y-4">
      <h1 className="text-xl font-semibold">Activa la verificación en dos pasos</h1>
      <p className="text-sm text-white/60">
        Es obligatoria para administradores. Usa Google Authenticator, 1Password, Authy o similar.
      </p>
      {!setup ? (
        <Button onClick={start}>Empezar</Button>
      ) : (
        <form onSubmit={enable} className="space-y-4">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={setup.qr_data_uri} alt="Código QR para tu app de autenticación" className="mx-auto w-52 rounded-xl" />
          <p className="break-all text-center font-mono text-xs text-white/50">{setup.secret}</p>
          <Field label="Código de 6 dígitos" inputMode="numeric" autoComplete="one-time-code" required value={code} onChange={(e) => setCode(e.target.value)} />
          <Button type="submit" className="w-full">Activar</Button>
        </form>
      )}
      <ErrorText>{error}</ErrorText>
    </Card>
  );
}

export default function AdminPage() {
  const { user, setUser, loading } = useMe();
  const [blocked, setBlocked] = useState<string | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [invites, setInvites] = useState<Invitation[]>([]);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("user");
  const [newLink, setNewLink] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [s, u, i, a] = await Promise.all([
        api<Stats>("/admin/stats"),
        api<User[]>("/admin/users"),
        api<Invitation[]>("/admin/invitations"),
        api<Activity[]>("/admin/activity?limit=50"),
      ]);
      setStats(s);
      setUsers(u);
      setInvites(i);
      setActivity(a);
      setBlocked(null);
    } catch (e) {
      setBlocked(e instanceof ApiError ? e.code : "network");
    }
  }, []);

  useEffect(() => {
    if (user) load();
  }, [user, load]);

  async function invite(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const inv = await api<Invitation>("/admin/invitations", { method: "POST", json: { email, role } });
      setNewLink(inv.link ?? "");
      setEmail("");
      load();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function updateUser(id: string, patch: Partial<Pick<User, "role" | "is_active">>) {
    setError("");
    try {
      await api(`/admin/users/${id}`, { method: "PATCH", json: patch });
      load();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function revoke(id: string) {
    await api(`/admin/invitations/${id}`, { method: "DELETE" }).catch(() => {});
    load();
  }

  if (loading || !user) return <Loading />;
  const isOwner = user.role === "owner";
  const emailById = Object.fromEntries(users.map((u) => [u.id, u.email]));

  if (blocked === "2fa_required") {
    return (
      <Shell user={user}>
        <TwoFactorSetup onDone={(u) => { setUser(u); load(); }} />
      </Shell>
    );
  }
  if (blocked) {
    return (
      <Shell user={user}>
        <Card>{errorMessage(new ApiError(403, blocked))}</Card>
      </Shell>
    );
  }

  return (
    <Shell user={user}>
      <h1 className="mb-4 text-2xl font-bold">Panel de administración</h1>

      {stats && (
        <div className="mb-6 grid grid-cols-3 gap-3">
          {[
            ["Usuarios", stats.users],
            ["Trabajos activos", stats.active_jobs],
            ["Trabajos totales", stats.jobs_total],
          ].map(([label, n]) => (
            <Card key={label} className="!p-4 text-center">
              <p className="text-2xl font-bold">{n}</p>
              <p className="text-xs text-white/50">{label}</p>
            </Card>
          ))}
        </div>
      )}

      <Card className="mb-6 space-y-4">
        <h2 className="text-lg font-semibold">Invitar a alguien</h2>
        <form onSubmit={invite} className="space-y-3 sm:flex sm:items-end sm:gap-3 sm:space-y-0">
          <div className="flex-1">
            <Field label="Correo" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          {isOwner && (
            <label className="block space-y-1.5">
              <span className="text-sm text-white/70">Rol</span>
              <select className="input-glass" value={role} onChange={(e) => setRole(e.target.value as Role)}>
                <option value="user">Usuario</option>
                <option value="admin">Administrador</option>
              </select>
            </label>
          )}
          <Button type="submit" className="w-full sm:w-auto">Invitar</Button>
        </form>
        {newLink && (
          <div className="space-y-2 rounded-2xl bg-emerald-500/10 p-3">
            <p className="text-sm text-emerald-200">Enlace de un solo uso (caduca en 7 días). Cópialo y envíalo:</p>
            <div className="flex gap-2">
              <input readOnly value={newLink} className="input-glass font-mono !text-xs" onFocus={(e) => e.target.select()} />
              <Button variant="ghost" type="button" onClick={() => navigator.clipboard.writeText(newLink)}>
                Copiar
              </Button>
            </div>
          </div>
        )}
        <ErrorText>{error}</ErrorText>
        {invites.length > 0 && (
          <ul className="divide-y divide-white/5 text-sm">
            {invites.map((i) => (
              <li key={i.id} className="flex items-center justify-between gap-2 py-2">
                <span className="min-w-0 truncate">
                  {i.email} <span className="text-white/40">· {ROLE_LABEL[i.role]}</span>
                </span>
                <span className="flex shrink-0 items-center gap-2 text-white/50">
                  {inviteState(i)}
                  {inviteState(i) === "Pendiente" && (
                    <button onClick={() => revoke(i.id)} className="text-red-300 hover:text-red-200">
                      Revocar
                    </button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="mb-6">
        <h2 className="mb-3 text-lg font-semibold">Usuarios</h2>
        <ul className="divide-y divide-white/5">
          {users.map((u) => {
            const locked = u.role === "owner" || u.id === user.id || (u.role === "admin" && !isOwner);
            return (
              <li key={u.id} className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm">
                <div className="min-w-0">
                  <p className="truncate">{u.email}</p>
                  <p className="text-xs text-white/40">
                    {ROLE_LABEL[u.role]} · {u.is_active ? "Activo" : "Pausado"}
                    {u.totp_enabled && " · 2FA"} · último acceso {u.last_login_at ? fmt(u.last_login_at) : "nunca"}
                  </p>
                </div>
                {!locked && (
                  <div className="flex gap-2">
                    {isOwner && (
                      <select
                        className="rounded-xl border border-white/10 bg-black/30 px-2 py-2"
                        value={u.role}
                        onChange={(e) => updateUser(u.id, { role: e.target.value as Role })}
                      >
                        <option value="user">Usuario</option>
                        <option value="admin">Admin</option>
                      </select>
                    )}
                    <Button
                      variant={u.is_active ? "danger" : "ghost"}
                      className="!min-h-9 !px-3 text-xs"
                      onClick={() => updateUser(u.id, { is_active: !u.is_active })}
                    >
                      {u.is_active ? "Quitar acceso" : "Reactivar"}
                    </Button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </Card>

      <Card>
        <h2 className="mb-3 text-lg font-semibold">Actividad reciente</h2>
        <ul className="space-y-2 text-xs">
          {activity.map((a) => (
            <li key={a.id} className="flex justify-between gap-3 text-white/60">
              <span className="min-w-0 truncate">
                <b className="text-white/80">{a.action}</b> {a.user_id ? emailById[a.user_id] ?? "" : ""}
              </span>
              <span className="shrink-0">{fmt(a.created_at)}</span>
            </li>
          ))}
        </ul>
      </Card>
    </Shell>
  );
}

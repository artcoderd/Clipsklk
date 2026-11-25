"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type FormEvent } from "react";

import { Button, Card, ErrorText, Field, Logo } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";

function RegisterForm() {
  const router = useRouter();
  const token = useSearchParams().get("token");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [inviteOk, setInviteOk] = useState<boolean | null>(token ? null : true);

  useEffect(() => {
    if (!token) return;
    api<{ email: string }>(`/auth/invitations/${encodeURIComponent(token)}`)
      .then((inv) => {
        setEmail(inv.email);
        setInviteOk(true);
      })
      .catch(() => setInviteOk(false));
  }, [token]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/auth/register", { method: "POST", json: { email, name, password, invitation_token: token ?? undefined } });
      router.replace("/");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (inviteOk === false) {
    return <p className="text-center text-white/70">Esta invitación no es válida, ya se usó o caducó. Pide una nueva.</p>;
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <Field label="Correo" type="email" required readOnly={!!token} value={email} onChange={(e) => setEmail(e.target.value)} />
      <Field label="Nombre" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
      <Field label="Contraseña (mínimo 10 caracteres)" type="password" autoComplete="new-password" minLength={10} required value={password} onChange={(e) => setPassword(e.target.value)} />
      <ErrorText>{error}</ErrorText>
      <Button type="submit" disabled={busy || inviteOk === null} className="w-full">
        {busy ? "Creando cuenta…" : "Crear cuenta"}
      </Button>
    </form>
  );
}

export default function RegisterPage() {
  return (
    <main className="grid min-h-dvh place-items-center px-4">
      <Card className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <Logo size="text-4xl" />
          <p className="mt-2 text-sm text-white/60">Te invitaron a Clipeo</p>
        </div>
        <Suspense>
          <RegisterForm />
        </Suspense>
      </Card>
    </main>
  );
}

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Button, Card, ErrorText, Field, Logo } from "@/components/ui";
import { api, ApiError, errorMessage } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [needCode, setNeedCode] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [publicSignup, setPublicSignup] = useState(false);

  useEffect(() => {
    api<{ public_signup: boolean }>("/auth/config").then((c) => setPublicSignup(c.public_signup)).catch(() => {});
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/auth/login", { method: "POST", json: { email, password, totp_code: needCode ? code : undefined } });
      router.replace("/");
    } catch (err) {
      if (err instanceof ApiError && err.code === "totp_required") setNeedCode(true);
      else setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-dvh place-items-center px-4">
      <Card className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <Logo size="text-4xl" />
          <p className="mt-2 text-sm text-white/60">Tus mejores momentos, en vertical.</p>
        </div>
        <form onSubmit={submit} className="space-y-4">
          <Field label="Correo" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          <Field label="Contraseña" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          {needCode && (
            <Field
              label="Código de verificación (6 dígitos)"
              inputMode="numeric"
              autoComplete="one-time-code"
              autoFocus
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          )}
          <ErrorText>{error}</ErrorText>
          <Button type="submit" disabled={busy} className="w-full">
            {busy ? "Entrando…" : "Entrar"}
          </Button>
        </form>
        <p className="mt-5 text-center text-sm text-white/50">
          {publicSignup ? (
            <Link href="/register" className="text-fuchsia-300">Crear cuenta</Link>
          ) : (
            "El acceso es solo por invitación."
          )}
        </p>
      </Card>
    </main>
  );
}

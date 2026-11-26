"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { ReactNode } from "react";

import { api, type User } from "@/lib/api";
import { Logo } from "./ui";

export function Shell({ user, children }: { user: User | null; children: ReactNode }) {
  const router = useRouter();
  async function logout() {
    await api("/auth/logout", { method: "POST" }).catch(() => {});
    router.replace("/login");
  }
  return (
    <div className="mx-auto min-h-dvh max-w-3xl px-4 pb-[max(2rem,env(safe-area-inset-bottom))] pt-[max(1rem,env(safe-area-inset-top))]">
      <header className="mb-6 flex items-center justify-between">
        <Link href="/" aria-label="Inicio">
          <Logo />
        </Link>
        {user && (
          <nav className="flex items-center gap-2 text-sm">
            {(user.role === "owner" || user.role === "admin") && (
              <Link href="/admin" className="rounded-xl bg-white/8 px-3 py-2 hover:bg-white/12">
                Panel
              </Link>
            )}
            <button onClick={logout} className="rounded-xl px-3 py-2 text-white/60 hover:text-white">
              Salir
            </button>
          </nav>
        )}
      </header>
      {user && (
        <nav className="-mt-2 mb-6 flex gap-1 overflow-x-auto text-sm" aria-label="Secciones">
          {[["/", "Proyectos"], ["/estilos", "Estilos"], ["/biblioteca", "Biblioteca"], ["/publicar", "Publicar"], ["/ia", "IA"], ["/planes", "Plan"]].map(([href, label]) => (
            <Link key={href} href={href} className="shrink-0 rounded-xl px-3 py-2 text-white/60 hover:bg-white/8 hover:text-white">
              {label}
            </Link>
          ))}
        </nav>
      )}
      {children}
    </div>
  );
}

export function Loading() {
  return <div className="grid min-h-dvh place-items-center text-white/50">Cargando…</div>;
}

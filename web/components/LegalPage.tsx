import Link from "next/link";
import type { ReactNode } from "react";

import { Logo } from "@/components/ui";

export function LegalPage({ title, updated, children }: { title: string; updated: string; children: ReactNode }) {
  return (
    <div className="mx-auto min-h-dvh max-w-3xl px-4 pb-12 pt-[max(1rem,env(safe-area-inset-top))]">
      <header className="mb-8 flex items-center justify-between py-2">
        <Link href="/" aria-label="Inicio"><Logo /></Link>
        <Link href="/login" className="rounded-xl bg-white/10 px-4 py-2 text-sm hover:bg-white/15">Entrar</Link>
      </header>
      <article className="legal space-y-4 text-white/75 [&_h2]:mt-8 [&_h2]:text-xl [&_h2]:font-semibold [&_h2]:text-white [&_li]:ml-5 [&_li]:list-disc">
        <h1 className="text-3xl font-bold text-white">{title}</h1>
        <p className="text-sm text-white/50">Última actualización: {updated}</p>
        {children}
      </article>
      <footer className="mt-12 flex gap-4 border-t border-white/10 pt-6 text-sm text-white/50">
        <Link href="/terminos" className="hover:text-white">Términos</Link>
        <Link href="/privacidad" className="hover:text-white">Privacidad</Link>
      </footer>
    </div>
  );
}

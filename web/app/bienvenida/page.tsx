import type { Metadata } from "next";
import Link from "next/link";

import { Logo } from "@/components/ui";
import { SITE_NAME, SITE_URL } from "@/lib/site";

// Rendered per request (cheap) so the plans are right from the first deploy; the plans fetch itself is cached 5 min.
export const dynamic = "force-dynamic";

const TITLE = "Clipeo — convierte videos y directos en clips verticales con subtítulos";
const DESCRIPTION =
  "Pega el link de YouTube, Twitch, Kick, TikTok o Facebook (o sube tu video) y recibe clips verticales con subtítulos animados, " +
  "encuadre que sigue a quien habla y los mejores momentos elegidos con IA. Listos para TikTok, Reels y Shorts.";

export const metadata: Metadata = {
  title: { absolute: TITLE },
  description: DESCRIPTION,
  alternates: { canonical: "/" },
  openGraph: { type: "website", url: "/", title: TITLE, description: DESCRIPTION, siteName: SITE_NAME, locale: "es_ES" },
  twitter: { card: "summary_large_image", title: TITLE, description: DESCRIPTION },
};

type Plan = { id: string; name: string; price_label: string; monthly_minutes: number; live: boolean; ai: boolean; max_active_jobs: number };

async function getPlans(): Promise<Plan[]> {
  try {
    const r = await fetch(`${process.env.API_URL ?? "http://localhost:8000"}/billing/plans`, { next: { revalidate: 300 } });
    return r.ok ? (await r.json()).plans : [];
  } catch {
    return [];
  }
}

const FEATURES = [
  ["Pega un link y listo", "YouTube, Twitch, Kick, TikTok y Facebook. O sube el archivo desde tu móvil o compu."],
  ["Los mejores momentos", "Detectamos risas, gritos, picos de chat y frases potentes. La IA elige los que tienen gancho."],
  ["Encuadre vertical inteligente", "La cámara sigue a quien habla, pantalla dividida para dos personas y modo gameplay."],
  ["Subtítulos karaoke", "Palabra por palabra, con tus fuentes, colores y estilos guardados."],
  ["Clips de directos en vivo", "Mientras transmites: botón «¡Clip ahora!», clips automáticos en los picos y aviso al móvil."],
  ["Editor en el navegador", "Recorta, corta silencios borrando texto, añade títulos, imágenes, sonidos y música."],
  ["Publica desde aquí", "Descarga, comparte o publica en Reels de Facebook e Instagram, TikTok y Telegram, o prográmalo."],
  ["Formatos para todo", "9:16, 4:5, 1:1 y 16:9, con portada y texto con hashtags sugeridos."],
];

const FAQ = [
  ["¿Qué es Clipeo?", "Una app web (se instala en el móvil como app) que convierte videos largos y directos en clips cortos verticales con subtítulos."],
  ["¿De qué plataformas acepta videos?", "YouTube, Twitch, Kick, TikTok y Facebook, además de archivos que subes tú (MP4, MOV, MKV, WEBM)."],
  ["¿Cómo se cobran los minutos?", "Cada plan incluye minutos al mes de video procesado. Si se acaban, puedes comprar minutos extra que no caducan."],
  ["¿Puedo pagar sin tarjeta?", "Sí. Además de tarjeta, puedes pagar por WhatsApp y activamos tu plan a mano."],
  ["¿Necesito instalar algo?", "No. Funciona en el navegador del móvil o la compu; todo el trabajo pesado se hace en nuestros servidores con GPU."],
];

export default async function Landing() {
  const plans = await getPlans();
  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "SoftwareApplication",
      name: SITE_NAME,
      url: SITE_URL,
      applicationCategory: "MultimediaApplication",
      operatingSystem: "Web, Android, iOS",
      description: DESCRIPTION,
      inLanguage: "es",
      ...(plans.length && {
        offers: plans.map((p) => ({ "@type": "Offer", name: p.name, description: `${p.monthly_minutes} minutos al mes`,
          priceCurrency: "USD", price: (p.price_label.match(/[\d.]+/)?.[0] ?? "0") })),
      }),
    },
    {
      "@context": "https://schema.org",
      "@type": "FAQPage",
      mainEntity: FAQ.map(([q, a]) => ({ "@type": "Question", name: q, acceptedAnswer: { "@type": "Answer", text: a } })),
    },
  ];

  return (
    <div className="mx-auto min-h-dvh max-w-5xl px-4 pb-12 pt-[max(1rem,env(safe-area-inset-top))]">
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd).replace(/</g, "\\u003c") }} />
      <header className="flex items-center justify-between py-2">
        <Logo />
        <nav className="flex items-center gap-2 text-sm">
          <a href="#planes" className="hidden rounded-xl px-3 py-2 text-white/70 hover:text-white sm:block">Planes</a>
          <Link href="/login" className="rounded-xl bg-white/10 px-4 py-2 font-medium hover:bg-white/15">Entrar</Link>
        </nav>
      </header>

      <main>
        <section className="py-14 text-center sm:py-20">
          <h1 className="mx-auto max-w-3xl text-4xl font-black leading-tight sm:text-6xl">
            Tus videos y directos, convertidos en <span className="bg-gradient-to-r from-fuchsia-400 to-amber-300 bg-clip-text text-transparent">clips virales</span>
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-lg text-white/70">
            Pega un link o sube un video. Clipeo encuentra los mejores momentos, los pone en vertical siguiendo a quien habla y les
            añade subtítulos animados. Listos para TikTok, Reels y Shorts.
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <Link href="/login" className="rounded-2xl bg-gradient-to-r from-fuchsia-500 to-violet-500 px-6 py-3.5 font-semibold shadow-lg shadow-fuchsia-500/20">
              Empezar
            </Link>
            <a href="#como-funciona" className="rounded-2xl border border-white/15 px-6 py-3.5 font-semibold text-white/80 hover:bg-white/5">
              Cómo funciona
            </a>
          </div>
        </section>

        <section id="como-funciona" aria-labelledby="como-h" className="py-10">
          <h2 id="como-h" className="mb-6 text-center text-2xl font-bold sm:text-3xl">Todo lo que necesitas para clipear</h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {FEATURES.map(([t, d]) => (
              <article key={t} className="glass rounded-3xl p-5">
                <h3 className="mb-1.5 font-semibold">{t}</h3>
                <p className="text-sm text-white/60">{d}</p>
              </article>
            ))}
          </div>
        </section>

        <section aria-labelledby="pasos-h" className="py-10">
          <h2 id="pasos-h" className="mb-6 text-center text-2xl font-bold sm:text-3xl">En tres pasos</h2>
          <ol className="grid gap-3 sm:grid-cols-3">
            {["Pega el link de tu video o directo, o sube el archivo.", "Elige cuántos clips, su duración, el formato y el estilo de subtítulos.",
              "Revisa, edita si quieres y descarga o publica en tus redes."].map((s, i) => (
              <li key={i} className="glass rounded-3xl p-5">
                <span className="mb-2 block text-3xl font-black text-fuchsia-300">{i + 1}</span>
                <span className="text-white/80">{s}</span>
              </li>
            ))}
          </ol>
        </section>

        {plans.length > 0 && (
          <section id="planes" aria-labelledby="planes-h" className="py-10">
            <h2 id="planes-h" className="mb-2 text-center text-2xl font-bold sm:text-3xl">Planes</h2>
            <p className="mb-6 text-center text-white/60">Paga con tarjeta o por WhatsApp. Cancela cuando quieras.</p>
            <div className="grid gap-3 sm:grid-cols-3">
              {plans.map((p) => (
                <article key={p.id} className="glass flex flex-col rounded-3xl p-6">
                  <h3 className="text-lg font-semibold">{p.name}</h3>
                  <p className="my-2 text-3xl font-black">{p.price_label}</p>
                  <ul className="flex-1 space-y-1.5 text-sm text-white/70">
                    <li>{p.monthly_minutes.toLocaleString("es")} minutos de video al mes</li>
                    <li>{p.live ? "Clips de directos en vivo" : "Videos grabados"}</li>
                    <li>{p.ai ? "Selección de momentos con IA" : "Selección automática"}</li>
                    <li>{p.max_active_jobs} {p.max_active_jobs === 1 ? "video" : "videos"} a la vez</li>
                  </ul>
                  <Link href="/login" className="mt-5 rounded-2xl bg-white/10 py-3 text-center font-medium hover:bg-white/15">Elegir</Link>
                </article>
              ))}
            </div>
          </section>
        )}

        <section aria-labelledby="faq-h" className="mx-auto max-w-3xl py-10">
          <h2 id="faq-h" className="mb-6 text-center text-2xl font-bold sm:text-3xl">Preguntas frecuentes</h2>
          <div className="space-y-2">
            {FAQ.map(([q, a]) => (
              <details key={q} className="glass rounded-2xl p-4">
                <summary className="cursor-pointer font-medium">{q}</summary>
                <p className="mt-2 text-sm text-white/70">{a}</p>
              </details>
            ))}
          </div>
        </section>
      </main>

      <footer className="mt-8 flex flex-wrap justify-between gap-3 border-t border-white/10 pt-6 text-sm text-white/50">
        <p>© {new Date().getFullYear()} Clipeo. Todos los derechos reservados.</p>
        <nav className="flex gap-4">
          <Link href="/terminos" className="hover:text-white">Términos</Link>
          <Link href="/privacidad" className="hover:text-white">Privacidad</Link>
        </nav>
      </footer>
    </div>
  );
}

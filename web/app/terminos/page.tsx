import type { Metadata } from "next";

import { LegalPage } from "@/components/LegalPage";
import { CONTACT, OWNER } from "@/lib/site";

export const metadata: Metadata = {
  title: "Términos de uso",
  description: "Condiciones para usar Clipeo, planes, pagos, contenido y propiedad de la app.",
  alternates: { canonical: "/terminos" },
};

export default function Terms() {
  return (
    <LegalPage title="Términos de uso" updated="26 de noviembre de 2026">
      <p>
        Estos términos regulan el uso de Clipeo (la «app»), un servicio web para crear clips cortos a partir de videos y
        transmisiones. Al crear una cuenta o usar la app aceptas estos términos.
      </p>

      <h2>1. Propiedad de la app</h2>
      <p>
        Clipeo, su nombre, logotipo, diseño, código fuente, modelos de configuración, bases de datos y demás elementos de la app
        son propiedad exclusiva de {OWNER}. Están protegidos por las leyes de propiedad intelectual e industrial. No se concede
        ninguna licencia sobre ellos más allá del uso personal del servicio según estos términos. Queda prohibido copiar,
        descompilar, revender o crear servicios derivados de la app sin autorización escrita del titular.
      </p>

      <h2>2. Cuentas</h2>
      <ul>
        <li>El acceso puede ser solo por invitación. Las invitaciones son personales y de un solo uso.</li>
        <li>Eres responsable de tu contraseña y de la actividad de tu cuenta. Las cuentas de administración usan verificación en dos pasos.</li>
        <li>Podemos suspender cuentas que incumplan estos términos o que abusen del servicio.</li>
      </ul>

      <h2>3. Tu contenido</h2>
      <p>
        Los videos que subes o enlazas y los clips que creas son tuyos. Nos das permiso únicamente para procesarlos, guardarlos
        y, si lo pides, publicarlos en tus redes, con el fin de prestarte el servicio. Cada usuario solo ve sus propios proyectos y
        clips.
      </p>
      <p>
        Debes tener derecho a usar el contenido que procesas. Al pegar un link de otra plataforma (YouTube, Twitch, Kick, TikTok,
        Facebook) tú eres responsable de respetar sus condiciones y los derechos de autor. Si una plataforma no permite la descarga,
        sube tu propio archivo. Retiraremos el contenido que infrinja derechos de terceros cuando se nos notifique.
      </p>

      <h2>4. Planes, minutos y pagos</h2>
      <ul>
        <li>Cada plan incluye minutos mensuales de video procesado. Los minutos del plan no se acumulan de un mes a otro.</li>
        <li>Los minutos extra comprados no caducan mientras la cuenta exista.</li>
        <li>Los pagos con tarjeta los procesa Stripe; no guardamos los datos de tu tarjeta. Las suscripciones se renuevan solas hasta que las canceles desde «Plan».</li>
        <li>Los pagos manuales (por ejemplo, por WhatsApp) se activan cuando confirmamos el pago, por el periodo acordado.</li>
        <li>Si un video falla por un error nuestro, se devuelven sus minutos.</li>
      </ul>

      <h2>5. Uso aceptable</h2>
      <p>
        No uses Clipeo para contenido ilegal, que infrinja derechos de terceros, de odio, de explotación de menores o engañoso.
        Hay límites de intentos de inicio de sesión y de trabajos simultáneos para proteger el servicio.
      </p>

      <h2>6. Servicios de terceros</h2>
      <p>
        Clipeo se conecta con servicios de terceros (Meta, TikTok, Telegram, proveedores de IA, Stripe). Su uso se rige además por
        sus propias condiciones. Las publicaciones pueden fallar si esos servicios cambian o revocan el acceso.
      </p>

      <h2>7. Garantías y responsabilidad</h2>
      <p>
        El servicio se ofrece «tal cual». Trabajamos para que esté disponible y funcione bien, pero no garantizamos que nunca haya
        interrupciones ni que la selección automática de momentos sea perfecta. En la medida que permita la ley, nuestra
        responsabilidad se limita a lo que hayas pagado en los últimos tres meses.
      </p>

      <h2>8. Cambios</h2>
      <p>Podemos actualizar estos términos. Si el cambio es importante, te avisaremos en la app antes de que entre en vigor.</p>

      {CONTACT && <p>Contacto: {CONTACT}</p>}
    </LegalPage>
  );
}

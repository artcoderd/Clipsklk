import type { Metadata } from "next";

import { LegalPage } from "@/components/LegalPage";
import { CONTACT, OWNER } from "@/lib/site";

export const metadata: Metadata = {
  title: "Aviso de privacidad",
  description: "Qué datos guarda Clipeo, para qué los usa y cómo puedes borrarlos.",
  alternates: { canonical: "/privacidad" },
};

export default function Privacy() {
  return (
    <LegalPage title="Aviso de privacidad" updated="26 de noviembre de 2026">
      <p>El responsable de tus datos es {OWNER}, titular de Clipeo.</p>

      <h2>Qué datos guardamos</h2>
      <ul>
        <li>Cuenta: correo, nombre, contraseña cifrada (nunca la guardamos en texto) y, si la activas, la clave de verificación en dos pasos.</li>
        <li>Tu contenido: los videos que subes o enlazas, su transcripción, los clips, estilos, archivos de tu biblioteca y ediciones.</li>
        <li>Conexiones a redes: los permisos de Facebook, Instagram, TikTok o Telegram que autorizas, guardados cifrados.</li>
        <li>Pagos: tu plan, minutos e historial. Los datos de la tarjeta los guarda Stripe, no nosotros.</li>
        <li>Seguridad: registro de actividad (inicios de sesión, cambios importantes) con la dirección IP.</li>
      </ul>

      <h2>Para qué los usamos</h2>
      <ul>
        <li>Para crear tus clips y, cuando lo pides, publicarlos en tus redes.</li>
        <li>Para elegir momentos con IA: si lo activas, enviamos solo la transcripción (texto) del video a un proveedor de IA (Anthropic, OpenAI o Google), que no la usa para entrenar sus modelos según sus condiciones de API.</li>
        <li>Para cobrar, aplicar los límites de tu plan, proteger el servicio contra abusos y enviarte avisos (por ejemplo, notificaciones de clips de un directo).</li>
      </ul>
      <p>No vendemos tus datos ni los usamos para publicidad.</p>

      <h2>Dónde se guardan</h2>
      <p>
        En servidores y almacenamiento contratados a nombre del titular de Clipeo, con HTTPS en todas las conexiones y copias de
        seguridad diarias cifradas de la base de datos.
      </p>

      <h2>Cuánto tiempo</h2>
      <p>Mientras tengas cuenta. Si borras un proyecto o clip, se elimina. Las copias de seguridad se rotan y se borran en unas semanas.</p>

      <h2>Tus derechos</h2>
      <p>
        Puedes pedir acceso, corrección o borrado de tus datos, o desconectar tus redes en cualquier momento desde «Publicar».
        {CONTACT ? ` Escríbenos a ${CONTACT}.` : " Escríbenos por el contacto indicado en la app."}
      </p>

      <h2>Cookies</h2>
      <p>Usamos una sola cookie necesaria para mantener tu sesión. No usamos cookies de publicidad ni de rastreo.</p>
    </LegalPage>
  );
}

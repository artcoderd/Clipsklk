# Clipeo: guía de lanzamiento

Esta guía pone Clipeo en internet con tu dominio, HTTPS, pagos y copias de seguridad.
Todo queda **a tu nombre**: tú creas cada cuenta, tú guardas cada contraseña y clave
(en un gestor de contraseñas como 1Password o Bitwarden) y nadie más tiene acceso.

## 0. Cuentas que tienes que crear a tu nombre

| Cuenta | Para qué | Activa 2FA |
|---|---|---|
| GitHub (repositorio **privado**) | El código | Sí |
| Cloudflare | Dominio, DNS, HTTPS, R2 (almacenamiento y copias) | Sí |
| Proveedor del servidor GPU (RunPod, Lambda, Hetzner GPU, Vast, OVH…) | La API y los workers | Sí |
| Stripe | Cobros con tarjeta | Sí |
| Anthropic / OpenAI / Google AI (al menos una) | Selección con IA | Sí |
| Meta for Developers, TikTok for Developers | Publicar en redes | Sí |
| Correo (Resend, Postmark, Google Workspace…) | Invitaciones por correo | Sí |

Nunca compartas estas claves ni las pegues en el código: van solo en el archivo `.env`
del servidor (que no se sube a GitHub).

## Atajo: instalación automática

En un servidor Ubuntu recién creado, `sudo bash ops/setup-server.sh` instala Docker y el
cortafuegos, crea `.env` desde `ops/env.production` con contraseñas aleatorias generadas
en el propio servidor y las claves de notificaciones, detecta si hay GPU, construye y
arranca. Después solo falta crear tu cuenta de dueño y añadir tus claves con `nano .env`.

## 1. El servidor

Recomendado: **un solo servidor Linux con GPU NVIDIA** (Ubuntu 22.04/24.04, 8+ GB de VRAM,
por ejemplo RTX 4000 Ada, L4, A10 o RTX 4090) con 100+ GB de disco. Ahí corren la web, la
API, los workers y la base de datos. Así web y API comparten dominio (la cookie de sesión
es de primera parte) y no pagas nada más.

> ¿Frontend en Vercel/Cloudflare? Se puede, pero entonces `/api` tiene que llegar al
> servidor por el mismo dominio (regla de proxy) para que la sesión funcione. Con el
> servidor único no hace falta nada de eso; es lo que recomendamos.

En el servidor:

```bash
# Docker y el toolkit de NVIDIA
curl -fsSL https://get.docker.com | sh
# https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi   # debe mostrar la GPU

# Cortafuegos: solo SSH, HTTP y HTTPS
sudo ufw allow OpenSSH && sudo ufw allow 80 && sudo ufw allow 443 && sudo ufw enable

git clone git@github.com:<tu-usuario>/Clipsklk.git clipeo && cd clipeo
cp .env.example .env
```

## 2. El dominio en Cloudflare

1. Compra o pasa tu dominio (ej. `clipeo.app`) a Cloudflare.
2. DNS: registro `A` `@` → IP del servidor y `CNAME` `www` → `clipeo.app`.
3. **Opción A (más simple):** nube gris (solo DNS). Caddy saca el certificado HTTPS solo.
   **Opción B:** nube naranja (proxy de Cloudflare). Pon SSL/TLS en **Full (strict)**;
   Caddy igual saca su certificado. En *Network* sube el límite de subida si tu plan lo
   permite (los planes gratis cortan subidas de más de 100 MB: con proxy naranja, los
   videos grandes conviene subirlos con link).
4. Regla de redirección `www.clipeo.app/*` → `https://clipeo.app/$1` (301).

## 3. El archivo `.env`

Lo mínimo para producción:

```bash
ENVIRONMENT=production
WEB_URL=https://clipeo.app
SITE_ADDRESS=clipeo.app
SECRET_KEY=<python3 -c "import secrets; print(secrets.token_urlsafe(48))">
POSTGRES_PASSWORD=<otra clave larga y aleatoria>
PUBLIC_SIGNUP=false
SITE_OWNER=Tu Nombre Completo        # aparece en Términos y Privacidad como dueño
SITE_CONTACT=hola@clipeo.app
```

Recomendado: almacenamiento en Cloudflare R2 (`STORAGE_BACKEND=s3` y las `S3_*`), las
claves de IA, `VAPID_*` (notificaciones), Stripe, WhatsApp, SMTP y copias fuera del
servidor (secciones siguientes). Todas las variables están explicadas en `.env.example`.

## 4. Arrancar

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.prod.yml up -d --build
docker compose run --rm api python -m app.cli vapid-keys          # una vez: pega las claves en .env
docker compose run --rm api python -m app.cli create-owner tu@correo.com
```

Entra en `https://clipeo.app/login`, inicia sesión y activa la **verificación en dos pasos**
(obligatoria para el panel). Desde **Panel** invitas a la gente.

Para actualizar después: `git pull` y el mismo `docker compose ... up -d --build`
(las migraciones de la base de datos se aplican solas al arrancar la API).

## 5. Pagos

### Stripe (tarjeta)

1. En Stripe → *Product catalog*, crea un producto por plan (Creador, Pro…) con un
   **precio recurrente mensual**, y uno por paquete de minutos con **precio único**.
2. Copia cada `price_…` y pégalo en **Panel → Editar planes y paquetes → Stripe price id**.
   Un plan sin price id solo se puede pagar por WhatsApp.
3. *Developers → API keys*: copia la clave secreta a `STRIPE_SECRET_KEY`.
4. *Developers → Webhooks → Add endpoint*: `https://clipeo.app/api/billing/webhook` con los
   eventos `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated` y `customer.subscription.deleted`. Copia el *signing
   secret* a `STRIPE_WEBHOOK_SECRET`. La firma se comprueba siempre: nadie puede fingir un pago.
5. *Settings → Billing → Customer portal*: actívalo y permite cambiar de plan (entre tus
   precios), actualizar la tarjeta y cancelar.
6. Reinicia: `docker compose ... up -d`. Prueba primero con las claves `sk_test_` y la
   tarjeta `4242 4242 4242 4242`.

### WhatsApp (pago manual)

Pon `WHATSAPP_NUMBER` (formato internacional, solo dígitos). En «Plan» aparece el botón
**Pagar por WhatsApp** con un mensaje que ya incluye el correo de la cuenta. Cuando
confirmes el pago, en **Panel → Planes y consumo → Pago manual** eliges el plan y los
meses (o añades minutos) y anotas el monto. Queda registrado en la actividad. Al terminar
los meses, la cuenta vuelve sola al plan gratis.

### Cómo se cuentan los minutos

Se descuenta la duración del video **original** procesado (pedir más clips o volver a
renderizar el mismo video no cobra otra vez). En directo, cada minuto grabado cuenta y la
grabación se detiene si se acaban. Si un video falla por un error del servidor, se
devuelven sus minutos. Dueño y administradores no tienen límites.

## 6. Correo de invitaciones

Con `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` y `SMTP_FROM`, las invitaciones del panel
se envían por correo (el enlace sigue apareciendo por si quieres copiarlo). Configura en
tu dominio los registros SPF/DKIM que te pida el proveedor para no caer en spam.

## 7. Copias de seguridad

El servicio `backup` hace un `pg_dump` **cada día** a las `BACKUP_HOUR` (UTC) y guarda
`BACKUP_KEEP_DAYS` días en el volumen `backups`.

- **Cifrado:** pon `BACKUP_PASSPHRASE` (y guárdala en tu gestor: sin ella no se pueden restaurar).
- **Fuera del servidor:** crea un bucket de R2 solo para copias, una clave de API con acceso
  únicamente a ese bucket, y rellena `BACKUP_REMOTE` y las `RCLONE_CONFIG_OFFSITE_*`.
- **Probar ahora:** `docker compose run --rm backup once`
- **Ver copias:** `docker compose run --rm --entrypoint ls backup -lh /backups`
- **Restaurar** (reemplaza la base de datos actual):
  `docker compose run --rm --entrypoint restore.sh backup /backups/clipeo-AAAAMMDD-HHMMSS.dump.enc`

Los videos y clips están en R2 (o en el volumen `storage`); si usas el volumen local,
haz también una copia periódica de él.

## 8. Antes de lanzar

- **Costos de GPU:** mira en **Panel → Este mes** los minutos procesados y el costo por
  minuto. Rellena en **Ajustes** lo que pagas por el servidor, almacenamiento e IA, y ajusta
  los precios de los planes para que el margen sea positivo.
- **Condiciones de las plataformas:** YouTube, Twitch, TikTok, etc. pueden bloquear o
  prohibir descargas. La opción de **subir el archivo** siempre está disponible; los
  Términos dicen que cada usuario responde por el contenido que procesa.
- **Revisión de apps:** Meta (permisos `pages_manage_posts`, `instagram_content_publish`…)
  y TikTok (Content Posting API, auditoría para publicar directo; hasta entonces
  `TIKTOK_POST_MODE=inbox`) revisan tu app. Usa estas URLs públicas:
  `https://clipeo.app/privacidad` y `https://clipeo.app/terminos`.
- **Textos legales:** revisa `/terminos` y `/privacidad` (rellena `SITE_OWNER` y
  `SITE_CONTACT`) y, si puedes, que los vea un abogado de tu país.
- **Marca:** registra la marca «Clipeo» en la oficina de tu país (IMPI en México, OEPM en
  España, USPTO en EE. UU., etc.). Los Términos ya dicen que la app y su código son tuyos.
- **SEO:** la página pública está en `/` para quien no ha iniciado sesión, con
  `sitemap.xml`, `robots.txt`, datos estructurados e imagen para compartir. Da de alta el
  dominio en Google Search Console y envía `https://clipeo.app/sitemap.xml`.
- **Registro público:** apagado por defecto. Si algún día quieres abrirlo, se activa en
  **Panel → Ajustes → Registro público** (las cuentas nuevas empiezan en el plan gratis).

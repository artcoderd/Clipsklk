# Clipeo

Versión web de Cliper: pegas el link de un video o directo (YouTube, Twitch, Kick,
TikTok, Facebook) o subes un archivo, y recibes clips verticales con subtítulos.
Funciona desde el navegador en iPhone, Android, Windows y Mac, y se puede instalar
en la pantalla de inicio (PWA).

## Estado: Fase 1 ✅ · Fase 2 (video grabado) ✅

| Pieza | Qué hay |
|---|---|
| `web/` | Next.js 16 + React 19 + Tailwind 4. PWA móvil primero, diseño oscuro con cristal. Login, registro por invitación, pantalla para pegar link / subir video, progreso en tiempo real y panel de administración. |
| `api/` | FastAPI + SQLAlchemy + Alembic. Cuentas, roles, invitaciones, 2FA, trabajos, registro de actividad. |
| `api/app/worker/` | Celery (cola `video`): descarga, transcripción, detección de momentos y corte. |
| `api/app/media/` | yt-dlp, ffmpeg (NVENC/x264), faster-whisper, análisis de audio y selección de clips. |
| Infra | PostgreSQL, Redis y Caddy (puerta de entrada única) con Docker Compose. |

Ya funciona:

- **Acceso solo por invitación** (registro público desactivado por defecto con `PUBLIC_SIGNUP=false`).
  Las invitaciones son de un solo uso, caducan a los 7 días y solo se guarda el hash del token.
- **Roles dueño / administrador / usuario.** Solo puede haber un dueño (se crea por línea de comandos);
  nadie puede cambiarlo, pausarlo ni invitar a otro dueño. Solo el dueño gestiona administradores.
- **2FA (TOTP) obligatoria** para entrar al panel de administración.
- Contraseñas con argon2id, sesión en cookie `httpOnly`, límite de intentos de login por correo e IP,
  y límite de trabajos activos por usuario.
- Cada usuario solo ve sus propios proyectos.
- Registro de actividad (logins, registros, invitaciones, trabajos, cambios de rol).
- Subida de archivos grandes con barra de progreso, y progreso del procesamiento en vivo por SSE.

### Fase 2: video grabado

```
link (yt-dlp) o archivo ─► almacenamiento (disco o S3/R2) ─► ffprobe
   ─► audio 16 kHz ─► faster-whisper (tiempos por palabra)
   ─► análisis de audio (volumen, gritos, risas) + pistas del texto ("jajaja", "[risas]", "¡...!")
   ─► N clips sin solaparse, empezando y terminando en frases ─► ffmpeg (NVENC o x264) + miniatura
```

- **El video se guarda una sola vez.** "Más clips" reutiliza el video, la transcripción y el
  análisis: no se descarga ni se transcribe de nuevo (solo corta). Pegar el mismo video con otro
  link (`youtu.be/…` vs `youtube.com/watch?v=…`) también lo reutiliza.
- **Videos de horas**: el análisis de audio lee el archivo por partes (poca memoria), y el
  límite es `MAX_SOURCE_HOURS` (6 por defecto).
- **Detección sin IA**: cada señal es relativa a los ~5 minutos alrededor, así funciona igual en
  un gameplay ruidoso que en un podcast tranquilo. La risa se reconoce por su ritmo de ~5 Hz
  ("ja-ja-ja"); el grito por ser fuerte y agudo. Es heurístico: la fase 5 añade la IA encima.
- **Progreso en vivo** por etapas, y cada clip aparece en cuanto está listo.
- **Clips**: se ven en el teléfono (con avance/retroceso), se descargan o se comparten con el
  menú nativo del teléfono.
- **Errores claros**: si YouTube bloquea la descarga desde el servidor, el video es privado, es
  un directo, etc., la web lo explica y sugiere subir el archivo.

Pendiente para la fase 3: "elegir una persona y sacar clips solo de ella" necesita detección de
hablantes y de rostros, que llega con el encuadre vertical.

## Arrancar en local

Requisitos: Docker con Compose.

```bash
cp .env.example .env
# Edita .env: pon un SECRET_KEY largo:
#   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
docker compose up -d --build

# Crea tu cuenta de dueño (una sola vez; te pide la contraseña):
docker compose exec api python -m app.cli create-owner tu@correo.com
```

Abre http://localhost:8080, entra con tu correo, ve a **Panel**, activa la verificación
en dos pasos y ya puedes invitar gente. Mientras no haya correo configurado (fase 8),
el panel te muestra el enlace de invitación para que lo copies y lo envíes tú.

### Desarrollo sin Docker

```bash
# API (necesita Postgres y Redis locales)
cd api && python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload            # http://localhost:8000/docs
celery -A app.worker.celery_app worker -Q video   # en otra terminal

# Web (redirige /api a localhost:8000)
cd web && npm install && npm run dev     # http://localhost:3000
```

Pruebas: `cd api && pip install -r requirements-dev.txt && pytest` (usa SQLite, Redis falso y
audio sintético con gritos y risas en tiempos conocidos; solo necesita `ffmpeg` instalado).

### Servidor con GPU (NVIDIA)

Requisitos en el servidor: driver de NVIDIA y NVIDIA Container Toolkit.

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

El worker usa entonces Whisper `large-v3` en la GPU (float16) y NVENC para cortar. Sin GPU, el
mismo código usa la CPU (Whisper `small` y x264), útil para desarrollar. El modelo se descarga
una vez de Hugging Face y queda guardado en el volumen `models`.

### Almacenamiento en Cloudflare R2 (producción)

Crea un bucket y un token de API de R2, y en `.env`:

```
STORAGE_BACKEND=s3
S3_BUCKET=clipeo
S3_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
```

Los clips se sirven desde R2 con enlaces firmados que caducan; solo el dueño del clip los recibe.

## Arquitectura

```
Navegador (PWA) ──► Caddy :8080 ─┬─ /api/*  ─► FastAPI ─┬─ PostgreSQL
                                 │                      └─ Redis ◄─┐ cola + eventos de progreso
                                 └─ /*      ─► Next.js            │
                                                       Worker Celery (GPU en fase 2+)
```

La web y la API comparten el mismo dominio, así la cookie de sesión es de primera parte
y las subidas y el progreso en vivo (SSE) van directos a FastAPI. Con un dominio real,
pon `SITE_ADDRESS=clipeo.app` en `.env` y Caddy saca el certificado HTTPS solo
(o déjalo en `:80` detrás de Cloudflare).

## Seguridad

- `.env` nunca se sube al repositorio (está en `.gitignore`). Todas las claves van ahí
  o en los secretos del servidor.
- En producción (`ENVIRONMENT=production`) la API no arranca sin un `SECRET_KEY` de 32+ caracteres,
  la cookie se marca `Secure` y la documentación `/docs` se desactiva.
- Copias de seguridad diarias de la base de datos: pendiente para el despliegue (fase 8).

## Siguientes fases

2. ~~Video grabado~~ ✅
3. Encuadre vertical que sigue a quien habla, pantalla dividida, modo gameplay, subtítulos karaoke, render con NVENC.
4. Directos en vivo con streamlink, chat, "¡Clip ahora!", notificaciones push.
5. Selección con IA (Claude / OpenAI / Gemini) con reglas virales y reglas propias.
6. Editor web táctil.
7. Publicar: descarga, compartir nativo, Meta, TikTok y Telegram.
8. Pagos con Stripe, límites por plan, correo, backups y despliegue con dominio.

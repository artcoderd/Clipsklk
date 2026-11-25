# Clipeo

Versión web de Cliper: pegas el link de un video o directo (YouTube, Twitch, Kick,
TikTok, Facebook) o subes un archivo, y recibes clips verticales con subtítulos.
Funciona desde el navegador en iPhone, Android, Windows y Mac, y se puede instalar
en la pantalla de inicio (PWA).

## Estado: Fase 1 (base del proyecto) ✅

| Pieza | Qué hay |
|---|---|
| `web/` | Next.js 16 + React 19 + Tailwind 4. PWA móvil primero, diseño oscuro con cristal. Login, registro por invitación, pantalla para pegar link / subir video, progreso en tiempo real y panel de administración. |
| `api/` | FastAPI + SQLAlchemy + Alembic. Cuentas, roles, invitaciones, 2FA, trabajos, registro de actividad. |
| `api/app/worker/` | Celery (cola `video`). El pipeline está como esqueleto; el trabajo real llega en la fase 2. |
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

Pruebas: `cd api && pytest` (usa SQLite y Redis falso, no necesita servicios).

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

2. Video grabado: yt-dlp / subida → S3/R2, faster-whisper con tiempos por palabra, detección de momentos, corte de N clips.
3. Encuadre vertical que sigue a quien habla, pantalla dividida, modo gameplay, subtítulos karaoke, render con NVENC.
4. Directos en vivo con streamlink, chat, "¡Clip ahora!", notificaciones push.
5. Selección con IA (Claude / OpenAI / Gemini) con reglas virales y reglas propias.
6. Editor web táctil.
7. Publicar: descarga, compartir nativo, Meta, TikTok y Telegram.
8. Pagos con Stripe, límites por plan, correo, backups y despliegue con dominio.

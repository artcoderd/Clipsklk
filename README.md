# Clipeo

Versión web de Cliper: pegas el link de un video o directo (YouTube, Twitch, Kick,
TikTok, Facebook) o subes un archivo, y recibes clips verticales con subtítulos.
Funciona desde el navegador en iPhone, Android, Windows y Mac, y se puede instalar
en la pantalla de inicio (PWA).

## Estado: Fases 1 a 7 ✅ (base, video grabado, encuadre vertical y subtítulos, directos en vivo, IA, editor web, publicar)

| Pieza | Qué hay |
|---|---|
| `web/` | Next.js 16 + React 19 + Tailwind 4. PWA móvil primero, diseño oscuro con cristal. Login, registro por invitación, pantalla para pegar link / subir video, progreso en tiempo real y panel de administración. |
| `api/` | FastAPI + SQLAlchemy + Alembic. Cuentas, roles, invitaciones, 2FA, trabajos, registro de actividad. |
| `api/app/worker/` | Celery (cola `video`): descarga, transcripción, detección de momentos y corte. |
| `api/app/media/` | yt-dlp, ffmpeg (NVENC/x264), faster-whisper, análisis de audio, caras (OpenCV), selección, render y subtítulos. |
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

### Fase 3: encuadre vertical y subtítulos

```
video ─► caras (YuNet, 4 por segundo) ─► seguimiento ─► personas (SFace: misma cara = misma persona)
      └► ¿quién habla? (movimiento de la boca, medido 8 veces por segundo, menos el de la cabeza)
render: ffmpeg decodifica ─► Python compone cada fotograma ─► ffmpeg + libass (subtítulos)
        + música con ducking + volumen -14 LUFS ─► NVENC o x264
```

- **Encuadres**: *Seguir al que habla*, *Pantalla dividida* (2 o 3 personas), *Gameplay*
  (cámara arriba y juego abajo, con recuadros que se dibujan a mano en el teléfono) y *Completo
  con fondo* (video entero sobre una copia desenfocada). *Automático* elige según quién sale.
- **Cámara**: *Fija*, *Suave* (se desliza), *Dinámica* (corta al cambiar de hablante) y
  *Cortes con zoom* (corta y acerca/aleja).
- **Formatos** 9:16, 4:5, 1:1 y 16:9. Los subtítulos quedan fuera de la zona que tapan los
  botones de TikTok y Reels; en pantalla dividida van en la línea divisoria y en gameplay justo
  bajo la cámara.
- **Subtítulos palabra por palabra** (karaoke) con 4 estilos de fábrica y estilos propios
  (letra, tamaño, colores, borde, sombra, animación, posición, mayúsculas) con vista previa en vivo.
  Las fuentes (Montserrat y Bebas Neue, licencia OFL) van incluidas en el repositorio.
- **Biblioteca** por usuario con logos (marca de agua) y música de fondo, que baja sola cuando
  alguien habla.
- **Clips solo de una persona**: la web muestra las caras encontradas; eliges una y los nuevos
  clips son de momentos en que ella habla, con la cámara siempre en ella.
- **Editar y volver a renderizar** cada clip con otras opciones sin reprocesar el video.
- Videos solo de audio (podcasts) salen con fondo degradado, logo y subtítulos.

Se probó con videos sintéticos de 2 y 3 personas y con cortes de cámara: personas agrupadas
correctamente y el hablante acertado en el 100 % de los momentos (sin contar 1,5 s tras cada cambio).

### Fase 4: directos en vivo

```
streamlink (YouTube, Twitch, Kick, TikTok) ─► ffmpeg en trozos de 10 s sin recodificar
   ├► audio de cada trozo ─► risas, gritos, volumen (comparado con los 5 min anteriores)
   ├► chat ─► mensajes por segundo y reacciones ("jajaja", KEKW, 😂…) vs. lo normal del canal
   └► pico ─► clip automático ─► el worker de GPU transcribe, encuadra y renderiza ─► notificación
al terminar: la grabación completa pasa por el proceso normal ─► "lo mejor del directo"
```

- **Pegas el link de un directo** (o de un canal antes de que empiece: espera hasta que arranque).
- **¡Clip ahora!**: guarda los últimos 30, 60 o 90 segundos desde el toque.
- **Clips automáticos** cuando hay risas, gritos, volumen alto o el chat explota, con sensibilidad
  baja/normal/alta, un mínimo de tiempo entre clips y un máximo por hora.
- **Reconexión automática** del video y del chat; si el directo se cae más de
  `LIVE_OFFLINE_GRACE_MINUTES` se da por terminado.
- **Al terminar**, la grabación completa se procesa como un video normal y salen los mejores clips.
- **Notificaciones push** en el teléfono con cada clip nuevo (y cuando termina un proyecto).
  En iPhone funcionan si Clipeo está añadido a la pantalla de inicio (iOS 16.4+).
- El chat se lee en **Twitch** (IRC anónimo), **YouTube** y **Kick**. En **TikTok** el chat
  necesita un servicio de firmas de terceros, así que ahí se detecta solo por el audio.
- El servicio `live-worker` graba (poco CPU, sin GPU, hasta 4 directos a la vez por defecto);
  el `worker` con GPU solo transcribe y renderiza los clips.

Probado con un directo simulado (HLS en tiempo real, como sirven las plataformas) y un chat de
Twitch simulado: grabación, caída y reconexión, fin del directo, clip automático por chat y por
audio, "¡Clip ahora!" y la selección final. Falta probarlo contra las plataformas reales.

Para activar las notificaciones, genera las claves una vez y ponlas en `.env`:

```bash
docker compose run --rm api python -m app.cli vapid-keys
```

### Fase 5: selección con IA

```
transcripción con tiempos (+ "(risas)", "(gritos)" del análisis) ─► Claude / OpenAI / Gemini
   reglas virales: gancho en 3 s · se entiende solo · termina en el remate · una idea
   + reglas del creador (página IA) + reglas del proyecto
─► respuesta en JSON con esquema estricto ─► se comprueba contra la transcripción real
   (tiempos que existen, inicio y fin de frase, duración, sin solaparse)
─► clips con título de portada, texto y hashtags ─► portada con el título
```

- **Solo se envía texto** (la transcripción). El video, el audio y los datos del usuario no salen
  del servidor. Las claves de la IA viven en `.env` del servidor y nunca llegan al navegador.
- **Claude por defecto** (`claude-opus-5-5`, esfuerzo `medium`), con salida estructurada,
  la transcripción en caché 1 hora (pedir "más clips" del mismo video sale más barato) y
  respaldo automático si el modelo rechaza un contenido. OpenAI y Gemini también funcionan;
  el usuario elige en la página **IA** cuál usar entre los configurados.
- **Si la IA falla** (clave mala, saturada, rechazo, respuesta inválida) o propone menos clips de
  los pedidos, el análisis automático completa el resto, y la web avisa por qué.
- **Portada** en el momento más expresivo, con el título y sin subtítulos; **texto y hashtags**
  listos para copiar, editar o pedir "otro texto" a la IA. Cambiar el título redibuja la portada.
- **En directo**, cada `LIVE_AI_REVIEW_MINUTES` la IA lee los últimos minutos y propone un clip
  solo si hay un momento realmente bueno (si no, no se guarda nada).
- El panel de administración muestra los **tokens gastados** por proveedor en 30 días.

Se probó con los tres SDK oficiales contra respuestas simuladas a nivel HTTP (sin claves reales
en este entorno), y de punta a punta en Docker con un servidor que imita la API de Claude.

### Fase 6: editor web

```
el servidor prepara una copia ligera del tramo (ya encuadrada, sin subtítulos, ±20 s de margen)
   └► el navegador la reproduce y dibuja encima subtítulos, textos e imágenes en vivo
guardar = documento de edición (tiempos del video original) ─► render final en el servidor
```

- **Funciona con el dedo y con el mouse** (eventos de puntero; probado con toques reales y con mouse).
- **Línea de tiempo** con forma de onda, tiradores para **recortar** inicio y fin, **cortes**
  (botón "Seleccionar tramo" para no chocar con el desplazamiento en el teléfono), **marcadores**,
  barras de textos/imágenes que se arrastran y se estiran, y zoom.
- **Editar borrando texto**: toca una palabra, toca otra para extender, "Borrar". Lo tachado no sale
  en el video (ni en los subtítulos). Botón **"Quitar silencios"**.
- **Textos** (título, etiqueta con fondo, Bebas) que se arrastran sobre la vista previa;
  **imágenes y stickers**, **efectos de sonido** y **música** de la biblioteca. La música baja sola
  cuando alguien habla en el render final.
- **Deshacer/rehacer**, **guardado automático** y "Deshacer todo" (vuelve al clip original).
- El **render final** lo hace el servidor con todo aplicado; los textos e imágenes quedan pegados al
  contenido: si después borras una frase, lo demás no se mueve de sitio.

### Fase 7: publicar

- **Descargar** y **compartir** con el menú nativo del teléfono (Web Share API), con el texto incluido.
- **Facebook e Instagram** (API de Meta): conectas con Facebook Login; aparecen tus Páginas y la
  cuenta profesional de Instagram vinculada a cada una. Reels subidos directamente a Meta (sin
  necesidad de un enlace público al video); Instagram se publica cuando termina de procesarlo.
- **TikTok** (Content Posting API): en modo `inbox` el video llega a tus borradores para
  terminarlo en la app (funciona antes de la auditoría de TikTok); en modo `direct` se publica con
  la privacidad que elijas de las que permite tu cuenta, como exigen las normas de TikTok.
- **Telegram**: tu propio bot envía el clip a tu canal, grupo o chat (`TELEGRAM_API_BASE` permite
  usar un servidor Bot API propio para pasar de 50 MB a 2 GB).
- **Programar**: el servicio `scheduler` revisa cada minuto y el servicio `publisher` sube los
  videos (cola propia, para no competir con la GPU ni con los directos).
- **Seguridad**: los tokens de las redes se guardan cifrados; el `state` de OAuth es de un solo
  uso y está ligado a tu sesión; cada usuario solo ve y usa sus propias cuentas.
- **Fallos**: reintenta solo si la red falla un momento (3 intentos); si la cuenta perdió el
  permiso queda marcada para volver a conectarla; notificación push con el resultado.

**Antes de usar Meta y TikTok** hay que registrar las apps (Meta for Developers, TikTok for
Developers) a tu nombre, poner las URI de redirección `<WEB_URL>/api/social/callback/meta` y
`.../tiktok`, y pasar su revisión. Mientras tanto solo los administradores/testers de la app pueden
conectar sus cuentas. Telegram, descargar y compartir funcionan desde ya.

Probado contra servidores simulados que imitan las rutas y respuestas de Meta, TikTok y Telegram
(no hay acceso real a esas plataformas en este entorno): conexión OAuth, subida, programación,
reintentos, token caducado y renovación del token de TikTok.

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
audio sintético con gritos y risas en tiempos conocidos; necesita `ffmpeg` instalado y descarga
una vez los modelos de caras de Hugging Face).

### Servidor con GPU (NVIDIA)

Requisitos en el servidor: driver de NVIDIA y NVIDIA Container Toolkit.

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

El worker usa entonces Whisper `large-v3` en la GPU (float16), decodifica con la GPU y codifica con NVENC. Sin GPU, el
mismo código usa la CPU (Whisper `small` y x264), útil para desarrollar. El modelo se descarga
una vez de Hugging Face y queda guardado en el volumen `models` (igual que los de caras, YuNet y SFace).

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
3. ~~Encuadre vertical y subtítulos~~ ✅
4. ~~Directos en vivo~~ ✅
5. ~~Selección con IA~~ ✅
6. ~~Editor web~~ ✅
7. ~~Publicar~~ ✅
8. Pagos con Stripe, límites por plan, correo, backups y despliegue con dominio.

export type Role = "owner" | "admin" | "user";

export type User = {
  id: string;
  email: string;
  name: string;
  role: Role;
  is_active: boolean;
  totp_enabled: boolean;
  created_at: string;
  last_login_at: string | null;
};

export type JobStatus = "queued" | "running" | "done" | "failed" | "canceled";

export type Job = {
  id: string;
  title: string | null;
  source: "url" | "upload";
  source_url: string | null;
  platform: string | null;
  original_filename: string | null;
  clip_count: number;
  clip_duration: number;
  render: Partial<RenderOptions>;
  focus_person: number | null;
  ai: { use_ai?: boolean | null; rules?: string };
  ai_status: string | null;
  status: JobStatus;
  progress: number;
  stage: string;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type Format = "9:16" | "4:5" | "1:1" | "16:9";
export type Layout = "auto" | "follow" | "split" | "gameplay" | "fit";
export type Framing = "fixed" | "smooth" | "dynamic" | "zoom";
export type Rect = [number, number, number, number];

export type RenderOptions = {
  format: Format;
  layout: Layout;
  framing: Framing;
  subtitles: boolean;
  style_id: string;
  music_id: string | null;
  music_volume: number;
  logo_id: string | null;
  logo_position: "top-left" | "top-right";
  gameplay: { cam: Rect | null; game: Rect | null } | null;
  person?: number | null;
};

export const DEFAULT_RENDER: RenderOptions = {
  format: "9:16",
  layout: "auto",
  framing: "smooth",
  subtitles: true,
  style_id: "preset:clasico",
  music_id: null,
  music_volume: 0.15,
  logo_id: null,
  logo_position: "top-right",
  gameplay: null,
};

export type StyleConfig = {
  font: "montserrat-black" | "montserrat-extrabold" | "montserrat-bold" | "bebas";
  size: number;
  text_color: string;
  highlight_color: string;
  outline_color: string;
  outline: number;
  shadow: number;
  uppercase: boolean;
  words_per_line: number;
  animation: "none" | "highlight" | "pop";
  position: "bottom" | "middle" | "top";
};

export type AIProvider = "anthropic" | "openai" | "gemini";
export type AIStatus = { available: { id: AIProvider; label: string; model: string }[]; default: AIProvider | null; viral_rules: string };
export type AIPrefs = { use_ai: boolean; provider: AIProvider | null; rules: string; hashtags: number };
export type AIOptions = { use_ai: boolean | null; rules: string };

export const AI_FAILURES: Record<string, string> = {
  ai_auth: "la clave de la IA no es válida",
  ai_rate_limited: "la IA está saturada en este momento",
  ai_unavailable: "la IA no respondió",
  ai_refused: "la IA no quiso procesar este contenido",
  ai_bad_output: "la IA dio una respuesta inválida",
  ai_truncated: "la respuesta de la IA quedó incompleta",
  ai_bad_request: "la IA rechazó la petición",
  ai_not_configured: "la IA no está configurada",
  person_filter: "se eligió una persona (la IA no sabe quién habla)",
};

export function aiStatusText(status: string | null, labels: Record<string, string> = {}): string | null {
  if (!status || status === "off") return null;
  if (status.startsWith("ai:")) {
    const p = status.slice(3);
    return `✨ Elegidos con ${labels[p] ?? ({ anthropic: "Claude", openai: "OpenAI", gemini: "Gemini" } as Record<string, string>)[p] ?? p}`;
  }
  const why = AI_FAILURES[status.slice(9)] ?? "la IA no respondió";
  return `Esta vez ${why}; elegimos los clips con el análisis automático (risas, gritos, volumen).`;
}

export type Style = { id: string; name: string; preset: boolean; config: StyleConfig };
export type LibraryItem = { id: string; kind: "logo" | "music"; name: string; size: number; created_at: string };
export type Person = { id: number; seconds: number };

export const FORMAT_LABELS: Record<Format, string> = {
  "9:16": "9:16 TikTok · Reels · Shorts",
  "4:5": "4:5 Feed",
  "1:1": "1:1 Cuadrado",
  "16:9": "16:9 Horizontal",
};
export const LAYOUT_LABELS: Record<Layout | "audio", string> = {
  auto: "Automático",
  follow: "Seguir al que habla",
  split: "Pantalla dividida",
  gameplay: "Gameplay",
  fit: "Completo con fondo",
  audio: "Solo audio",
};
export const FRAMING_LABELS: Record<Framing, string> = {
  fixed: "Fija",
  smooth: "Suave",
  dynamic: "Dinámica",
  zoom: "Cortes con zoom",
};

export type Clip = {
  id: string;
  job_id: string;
  index: number;
  start: number;
  end: number;
  duration: number;
  score: number;
  reasons: string[];
  text: string;
  render: Partial<RenderOptions>;
  layout: string | null;
  render_status: "ready" | "rendering" | "failed";
  version: number;
  offset: number;
  title: string;
  caption: string;
  hashtags: string[];
  ai_provider: string | null;
  cover_rev: string | null;
  created_at: string;
};

export type LiveStatus = "starting" | "waiting" | "recording" | "reconnecting" | "finishing" | "ended" | "failed";

export type Live = {
  id: string;
  url: string;
  platform: string | null;
  status: LiveStatus;
  chat_status: "none" | "connected" | "reconnecting";
  auto_clips: boolean;
  ai_review: boolean;
  sensitivity: "low" | "normal" | "high";
  clip_duration: number;
  final_clips: number;
  job_id: string;
  final_job_id: string | null;
  recorded_seconds: number;
  end_reason: string | null;
  error: string | null;
  started_at: string;
  ended_at: string | null;
};

export type LiveActivity = { recorded: number; interest: number[]; chat: number[]; chat_messages?: number };

export type LiveEvent =
  | { type: "live"; live: Live }
  | { type: "activity"; activity: LiveActivity }
  | { type: "clip"; clip: Clip };

export const LIVE_STATUS: Record<LiveStatus, string> = {
  starting: "Conectando…",
  waiting: "Esperando a que empiece el directo",
  recording: "Grabando",
  reconnecting: "Se cayó el directo, reconectando…",
  finishing: "Terminando y preparando lo mejor…",
  ended: "Directo terminado",
  failed: "Se perdió la conexión con el directo",
};

export type JobEvent = { type: "job"; job: Job } | { type: "clip"; clip: Clip };

export class ApiError extends Error {
  constructor(public status: number, public code: string) {
    super(code);
  }
}

const MESSAGES: Record<string, string> = {
  invalid_credentials: "Correo o contraseña incorrectos.",
  too_many_attempts: "Demasiados intentos. Espera unos minutos.",
  totp_required: "Escribe el código de tu app de autenticación.",
  totp_invalid: "Código de verificación incorrecto.",
  invitation_required: "El acceso es solo por invitación.",
  invitation_invalid: "La invitación no es válida, ya se usó o caducó.",
  invitation_email_mismatch: "Usa el mismo correo al que llegó la invitación.",
  email_taken: "Ese correo ya está registrado.",
  unsupported_url: "Ese link no es de YouTube, Twitch, Kick, TikTok o Facebook.",
  unsupported_file_type: "Formato no soportado. Usa MP4, MOV, MKV, WEBM, MP3 o WAV.",
  file_too_large: "El archivo es demasiado grande.",
  too_many_active_jobs: "Ya tienes varios trabajos en proceso. Espera a que terminen.",
  "2fa_required": "Activa la verificación en dos pasos para entrar al panel.",
  owner_protected: "El dueño no se puede modificar.",
  owner_only: "Solo el dueño puede hacer esto.",
  cannot_invite_owner: "No se puede invitar a otro dueño.",
  forbidden: "No tienes permiso.",
  not_authenticated: "Inicia sesión para continuar.",
  job_busy: "Este proyecto todavía se está procesando.",
  unsupported_live_url: "Pega el link de un directo de YouTube, Twitch, Kick o TikTok.",
  too_many_lives: "Ya estás grabando un directo. Termínalo antes de empezar otro.",
  live_not_active: "Este directo ya terminó.",
  ai_not_configured: "La IA no está configurada en el servidor.",
  no_text: "Este clip no tiene texto hablado.",
  ai_unavailable: "La IA no respondió. Inténtalo de nuevo.",
  ai_rate_limited: "La IA está saturada. Prueba en un momento.",
  ai_refused: "La IA no quiso procesar este clip.",
  ai_auth: "La clave de la IA del servidor no es válida.",
  clip_busy: "Este clip ya se está renderizando.",
  person_not_found: "No encontramos a esa persona en el video.",
  invalid_style: "Ese estilo de subtítulos no existe.",
  invalid_music: "Esa música ya no está en tu biblioteca.",
  invalid_logo: "Ese logo ya no está en tu biblioteca.",
  too_many_styles: "Llegaste al máximo de estilos guardados.",
  library_full: "Tu biblioteca está llena. Borra algo primero.",
  job_not_ready: "Espera a que termine el primer procesamiento.",
};

/** Why a job failed (codes set by the worker). */
export const JOB_ERRORS: Record<string, string> = {
  source_blocked:
    "La plataforma bloqueó la descarga desde nuestro servidor. Descarga el video y súbelo como archivo.",
  source_private: "El video es privado, para miembros o con restricción de edad. Súbelo como archivo.",
  source_unavailable: "El video no existe o fue eliminado.",
  source_is_live: "Ese link es un directo en curso. El modo en vivo llega pronto; mientras, usa la grabación.",
  source_too_long: "El video es demasiado largo (máximo 6 horas).",
  unsupported_url: "Ese link no se puede descargar (¿es una lista de reproducción?).",
  download_failed: "No pudimos descargar el video. Inténtalo de nuevo o súbelo como archivo.",
  invalid_media: "El archivo no es un video o audio válido.",
  no_audio: "El video no tiene audio, así que no podemos encontrar momentos.",
  no_moments: "No encontramos momentos para recortar.",
  no_person_moments: "No encontramos más momentos en los que hable esa persona.",
  person_not_found: "No encontramos a esa persona en el video.",
  render_failed: "Falló el render del clip. Inténtalo de nuevo.",
  ffmpeg_failed: "Falló el corte del video. Inténtalo de nuevo.",
  processing_failed: "Algo falló al procesar el video. Inténtalo de nuevo.",
};

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return MESSAGES[err.code] ?? "Algo salió mal. Inténtalo de nuevo.";
  return "No hay conexión con el servidor.";
}

function codeFrom(body: unknown): string {
  const detail = (body as { detail?: unknown })?.detail;
  if (typeof detail === "string") return detail;
  return "validation_error";
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const res = await fetch(`/api${path}`, {
    credentials: "same-origin",
    ...rest,
    headers: json !== undefined ? { "content-type": "application/json", ...headers } : headers,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, codeFrom(body));
  return body as T;
}

/** Upload with progress (fetch can't report upload progress yet). */
export function uploadVideo(
  file: File,
  opts: { clip_count: number; clip_duration: number; render: RenderOptions; ai: AIOptions },
  onProgress: (pct: number) => void,
): Promise<Job> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    form.append("clip_count", String(opts.clip_count));
    form.append("clip_duration", String(opts.clip_duration));
    form.append("render", JSON.stringify(opts.render));
    form.append("ai", JSON.stringify(opts.ai));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs/upload");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {}
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as Job);
      else reject(new ApiError(xhr.status, codeFrom(body)));
    };
    xhr.onerror = () => reject(new Error("network"));
    xhr.send(form);
  });
}

export const STAGE_LABELS: Record<string, string> = {
  queued: "En cola",
  starting: "Preparando",
  fetching: "Descargando video",
  extracting_audio: "Extrayendo audio",
  transcribing: "Transcribiendo palabra por palabra",
  detecting_moments: "Buscando risas, gritos y picos",
  detecting_faces: "Reconociendo caras y quién habla",
  ai_selecting: "La IA está eligiendo los mejores momentos",
  rendering: "Creando clips verticales con subtítulos",
  done: "Listo",
  failed: "Error",
};

export const REASON_LABELS: Record<string, string> = {
  risas: "😂 Risas",
  gritos: "😱 Gritos",
  volumen: "🔊 Volumen",
  "emoción": "🔥 Emoción",
  "conversación": "💬 Conversación",
  "video completo": "🎬 Completo",
  persona: "👤 Persona elegida",
  chat: "💬 Chat",
  "clip ahora": "⚡ ¡Clip ahora!",
  IA: "✨ IA",
};

export function fmtTime(s: number): string {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60).toString().padStart(2, "0");
  return h ? `${h}:${m.toString().padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

export const PLATFORM_LABELS: Record<string, string> = {
  youtube: "YouTube",
  twitch: "Twitch",
  kick: "Kick",
  tiktok: "TikTok",
  facebook: "Facebook",
  hls: "Señal HLS",
};

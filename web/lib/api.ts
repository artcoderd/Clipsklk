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
  source: "url" | "upload";
  source_url: string | null;
  platform: string | null;
  original_filename: string | null;
  clip_count: number;
  clip_duration: number;
  status: JobStatus;
  progress: number;
  stage: string;
  error: string | null;
  created_at: string;
  updated_at: string;
};

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
  opts: { clip_count: number; clip_duration: number },
  onProgress: (pct: number) => void,
): Promise<Job> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    form.append("clip_count", String(opts.clip_count));
    form.append("clip_duration", String(opts.clip_duration));
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
  transcribing: "Transcribiendo",
  detecting_moments: "Buscando los mejores momentos",
  cutting: "Cortando clips",
  done: "Listo",
  failed: "Error",
};

export const PLATFORM_LABELS: Record<string, string> = {
  youtube: "YouTube",
  twitch: "Twitch",
  kick: "Kick",
  tiktok: "TikTok",
  facebook: "Facebook",
};

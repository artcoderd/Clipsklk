"use client";

import { api } from "./api";

export type PushState = "unsupported" | "ios-install" | "denied" | "off" | "on" | "unconfigured";

function isIOS() {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

function standalone() {
  return window.matchMedia("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true;
}

function keyBytes(b64: string): Uint8Array {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

export async function pushState(): Promise<PushState> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    // On iPhone, web push only exists for apps added to the home screen.
    return isIOS() && !standalone() ? "ios-install" : "unsupported";
  }
  const { key } = await api<{ key: string | null }>("/push/key");
  if (!key) return "unconfigured";
  if (Notification.permission === "denied") return "denied";
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = await reg?.pushManager.getSubscription();
  return sub ? "on" : "off";
}

export async function enablePush(): Promise<PushState> {
  const { key } = await api<{ key: string | null }>("/push/key");
  if (!key) return "unconfigured";
  if ((await Notification.requestPermission()) !== "granted") return "denied";
  const reg = (await navigator.serviceWorker.getRegistration()) ?? (await navigator.serviceWorker.register("/sw.js"));
  await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: keyBytes(key) as BufferSource,
  });
  await api("/push/subscriptions", { method: "POST", json: sub.toJSON() });
  return "on";
}

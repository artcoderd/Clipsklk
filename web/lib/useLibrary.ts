"use client";

import { useCallback, useEffect, useState } from "react";

import { api, type LibraryItem, type Style } from "./api";

/** The user's subtitle styles (presets + saved) and library (logos, music). */
export function useLibrary(enabled = true) {
  const [styles, setStyles] = useState<Style[]>([]);
  const [items, setItems] = useState<LibraryItem[]>([]);
  const reload = useCallback(() => {
    api<Style[]>("/styles").then(setStyles).catch(() => {});
    api<LibraryItem[]>("/library").then(setItems).catch(() => {});
  }, []);
  useEffect(() => {
    if (enabled) reload();
  }, [enabled, reload]);
  return {
    styles,
    logos: items.filter((i) => i.kind === "logo"),
    music: items.filter((i) => i.kind === "music"),
    images: items.filter((i) => i.kind === "image"),
    sounds: items.filter((i) => i.kind === "sound"),
    reload,
  };
}

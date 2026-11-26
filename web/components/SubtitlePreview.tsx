"use client";

import { useEffect, useState } from "react";

import type { StyleConfig } from "@/lib/api";

const FAMILY: Record<StyleConfig["font"], string> = {
  "montserrat-black": "'Montserrat Black'",
  "montserrat-extrabold": "'Montserrat ExtraBold'",
  "montserrat-bold": "'Montserrat Bold'",
  bebas: "'Bebas Neue'",
};
const SAMPLE = "no lo vas a creer lo que pasó después".split(" ");

/** Approximates the burned-in karaoke subtitles on a phone-shaped frame. */
export function SubtitlePreview({ style, background }: { style: StyleConfig; background?: string }) {
  const [k, setK] = useState(0);
  const per = Math.max(1, style.words_per_line);
  useEffect(() => {
    const t = setInterval(() => setK((x) => (x + 1) % SAMPLE.length), 420);
    return () => clearInterval(t);
  }, []);
  const start = Math.floor(k / per) * per;
  const words = SAMPLE.slice(start, start + per);
  const scale = 0.27; // preview is ~290 px wide vs 1080 px output
  const outline = style.outline * scale * 1.6;
  const pos = { bottom: "bottom-[27%]", middle: "top-1/2 -translate-y-1/2", top: "top-[14%]" }[style.position];

  return (
    <div
      className="relative mx-auto aspect-[9/16] w-full max-w-[290px] overflow-hidden rounded-3xl border border-white/10"
      style={{ background: background ?? "linear-gradient(160deg,#3b2a4d,#1b1424 60%,#0d0a12)" }}
    >
      <div className={`absolute inset-x-[8%] text-center leading-tight ${pos}`}>
        {words.map((w, i) => {
          const active = style.animation !== "none" && start + i === k;
          return (
            <span
              key={`${start}-${i}`}
              className="inline-block px-[0.12em]"
              style={{
                fontFamily: FAMILY[style.font],
                fontSize: style.size * scale,
                color: active ? style.highlight_color : style.text_color,
                WebkitTextStroke: `${outline}px ${style.outline_color}`,
                paintOrder: "stroke fill",
                textShadow: style.shadow ? `${style.shadow * scale}px ${style.shadow * scale}px 0 rgba(0,0,0,.6)` : "none",
                textTransform: style.uppercase ? "uppercase" : "none",
                animation: active && style.animation === "pop" ? "pop 170ms ease-out" : "none",
              }}
            >
              {w}
            </span>
          );
        })}
      </div>
    </div>
  );
}

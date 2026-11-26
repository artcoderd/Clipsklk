import { ImageResponse } from "next/og";

export const alt = "Clipeo — clips verticales con subtítulos";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", justifyContent: "center",
                    padding: 80, background: "linear-gradient(135deg, #07060d 0%, #2a0f3d 60%, #4a1550 100%)", color: "white" }}>
        <div style={{ fontSize: 96, fontWeight: 900, letterSpacing: -2 }}>Clipeo</div>
        <div style={{ fontSize: 48, marginTop: 24, maxWidth: 900, lineHeight: 1.2 }}>
          Tus videos y directos, convertidos en clips verticales con subtítulos
        </div>
        <div style={{ fontSize: 30, marginTop: 40, color: "#f0abfc" }}>YouTube · Twitch · Kick · TikTok · Facebook</div>
      </div>
    ),
    size,
  );
}

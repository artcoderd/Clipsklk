import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { ServiceWorker } from "@/components/ServiceWorker";
import { SITE_URL } from "@/lib/site";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: "Clipeo — clips verticales con subtítulos", template: "%s · Clipeo" },
  description:
    "Pega el link de un video o directo y recibe clips verticales con subtítulos listos para TikTok, Reels y Shorts.",
  applicationName: "Clipeo",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "Clipeo", statusBarStyle: "black-translucent" },
  icons: {
    icon: [{ url: "/icons/icon.svg", type: "image/svg+xml" }, { url: "/icons/icon-192.png", sizes: "192x192" }],
    apple: "/icons/apple-touch-icon.png",
  },
};

export const viewport: Viewport = {
  themeColor: "#07060d",
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="es">
      <body className="font-sans antialiased">
        {children}
        <ServiceWorker />
      </body>
    </html>
  );
}

import type { MetadataRoute } from "next";

import { SITE_URL } from "@/lib/site";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", allow: ["/", "/terminos", "/privacidad"],
             disallow: ["/api/", "/admin", "/jobs/", "/clips/", "/live/", "/register", "/bienvenida"] },
    sitemap: `${SITE_URL}/sitemap.xml`,
  };
}

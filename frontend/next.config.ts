import type { NextConfig } from "next";

/**
 * Dual-mode config:
 * - `AK5_STATIC_EXPORT=1` → `out/` for packaging into `ak5/web_ui` (no rewrites/middleware).
 * - otherwise → `next dev` with gateway rewrites to FastAPI (auth + API).
 */
const embed = process.env.AK5_STATIC_EXPORT === "1";
const gateway = process.env.AK5_DEV_GATEWAY || "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
};

if (embed) {
  nextConfig.output = "export";
  nextConfig.images = { unoptimized: true };
  nextConfig.trailingSlash = true;
} else {
  nextConfig.rewrites = async () => [
    { source: "/api/auth/:path*", destination: `${gateway}/api/auth/:path*` },
    { source: "/api/v1/:path*", destination: `${gateway}/api/v1/:path*` },
    { source: "/docs", destination: `${gateway}/docs` },
    { source: "/docs/:path*", destination: `${gateway}/docs/:path*` },
    { source: "/openapi.json", destination: `${gateway}/openapi.json` },
    { source: "/health", destination: `${gateway}/health` },
  ];
}

export default nextConfig;

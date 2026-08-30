import type { NextConfig } from "next";

/**
 * Static export. `next build` writes a fully static site to `out/`, which
 * FastAPI serves from /app/static. No Node server, no image optimizer.
 */
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: true,
  reactStrictMode: true,
};

export default nextConfig;

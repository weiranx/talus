import type { NextConfig } from "next";

const engine = process.env.ENGINE_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  experimental: {
    // Uploads (Settings > Upload files) pass through to the engine: an Apple Health export.zip
    // can run to gigabytes and take minutes to read. The engine sets its own limit.
    middlewareClientMaxBodySize: "2gb",
    proxyTimeout: 15 * 60 * 1000,
  },
  // Browser calls to /api/* (the check-in form) go to the engine.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${engine}/api/:path*` }];
  },
};

export default config;

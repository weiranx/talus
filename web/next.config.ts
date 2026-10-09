import type { NextConfig } from "next";

const engine = process.env.ENGINE_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  // The rewrite proxy gives up after 30 s by default and answers 500, so a long sync or a
  // rescore of every activity read as failed even though the engine finished it.
  experimental: { proxyTimeout: 10 * 60 * 1000 },
  // Browser calls to /api/* (the check-in form) go to the engine.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${engine}/api/:path*` }];
  },
};

export default config;

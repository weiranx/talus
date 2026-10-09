import type { NextConfig } from "next";

const engine = process.env.ENGINE_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  // Browser calls to /api/* (the check-in form) go to the engine.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${engine}/api/:path*` }];
  },
};

export default config;

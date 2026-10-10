import type { NextConfig } from "next";

const engine = process.env.ENGINE_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  experimental: {
    // Uploads (Settings > Upload files) pass through to the engine: an Apple Health export.zip
    // can run to gigabytes and take minutes to read. The engine sets its own limit.
    middlewareClientMaxBodySize: "2gb",
    proxyTimeout: 15 * 60 * 1000,
  },
  // Pages stream: the rest of Today, Plan and an activity show while the AI coach is still
  // writing (Suspense). nginx buffers proxied responses by default, which holds the whole page
  // until the coach is done; this header tells it to pass each chunk through as it comes.
  async headers() {
    return [{ source: "/:path*", headers: [{ key: "X-Accel-Buffering", value: "no" }] }];
  },
  // Browser calls to /api/* (the check-in form) go to the engine.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${engine}/api/:path*` }];
  },
};

export default config;

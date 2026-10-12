import { execSync } from "node:child_process";
import type { NextConfig } from "next";

const engine = process.env.ENGINE_URL ?? "http://127.0.0.1:8000";

// The commit this build of the web app came from, for the status page. Built by CI, it's
// GITHUB_SHA; built by hand, it's the checkout's own, marked when it had uncommitted changes.
function revision(): string {
  if (process.env.GITHUB_SHA) return process.env.GITHUB_SHA;
  try {
    const git = (args: string) => execSync(`git ${args}`, { stdio: ["ignore", "pipe", "ignore"] }).toString().trim();
    const head = git("rev-parse HEAD");
    return git("status --porcelain -- .") ? `${head}-dirty` : head;
  } catch {
    return "unknown";
  }
}

const config: NextConfig = {
  env: { TALUS_WEB_REVISION: revision(), TALUS_WEB_BUILT_AT: new Date().toISOString() },
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

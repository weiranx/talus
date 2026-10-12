import { defineCloudflareConfig } from "@opennextjs/cloudflare";

// Every page reads the session cookie, so nothing is cached between requests and no
// incremental cache (R2/KV) is needed.
export default defineCloudflareConfig({});

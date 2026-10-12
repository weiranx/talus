# Deploying Talus

Talus runs on the DigitalOcean droplet (`digitalocean-vps` in `~/.ssh/config`) at
https://talus.weiranxiong.com, alongside the other sites there.

| Piece | Where |
|---|---|
| Web (Next.js) | Cloudflare Worker `talus-web` (built with `@opennextjs/cloudflare`), serving `talus.weiranxiong.com/*` |
| Engine (FastAPI) | `talus-engine-1` container, published on `127.0.0.1:3021` |
| Reverse proxy | host nginx, `/etc/nginx/sites-available/talus` (copy of `nginx-talus.conf`): `/api/` to the engine, 404 for anything else |
| TLS | Let's Encrypt via certbot on the host; Cloudflare proxied, SSL mode Full (strict) |
| Data | `/opt/talus/data` (accounts, per-user databases, raw files, `secret.key`) |
| Secrets | `/opt/talus/.env`, mode 600 |

## Deploy a new version

From a clean checkout of `main`:

```bash
deploy/deploy.sh
```

It ships the committed engine with `git archive`, rebuilds its image on the server and restarts
it. Data and `.env` stay put. The first build takes a few minutes on the 1 vCPU droplet.

## The web app on Cloudflare

Pushing to `main` deploys the droplet first, then the Worker (the `web-cloudflare` job, which needs
the `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` repository secrets; the token needs the
*Edit Cloudflare Workers* template). By hand, from `web/`: `npx wrangler login`, then `npm run cf:deploy`.

The Worker serves pages; the droplet serves everything under `/api/`. Two Worker routes on the
`weiranxiong.com` zone (Workers Routes in the dashboard) split them:

| Route | Worker |
|---|---|
| `talus.weiranxiong.com/api/*` | None (goes to the droplet) |
| `talus.weiranxiong.com/*` | `talus-web` |

So the browser, the iOS app and Google sign-in all keep one hostname and one session cookie, and
uploads never pass through the Worker. The Worker's own engine calls (`ENGINE_URL` in
`web/wrangler.jsonc`) go to `https://talus.weiranxiong.com/api/...`, which the first route sends
to the droplet.

The droplet no longer runs the web app, so the Worker route is the only way pages are served.
To roll back a bad Worker deploy, pick the previous version under Workers & Pages > talus-web >
Deployments, or run `npx wrangler rollback` from `web/`.

`npm run cf:preview` runs the Worker build locally (set `ENGINE_URL` in `web/.dev.vars`).

## Server `.env`

```
INTERVALS_API_KEY=...
INTERVALS_ATHLETE_ID=...
ANTHROPIC_API_KEY=...
TALUS_PUBLIC_URL=https://talus.weiranxiong.com
TALUS_SECURE_COOKIE=1
TALUS_SIGNUP=closed
# GOOGLE_CLIENT_ID=...       redirect URI https://talus.weiranxiong.com/api/auth/google/callback
# INTERVALS_CLIENT_ID=...    redirect URI https://talus.weiranxiong.com/api/auth/intervals/callback
# GOOGLE_CLIENT_SECRET=...
```

The encryption key for stored intervals.icu keys is `data/secret.key`; it must travel with the data.

## First-time setup (already done)

```bash
ssh digitalocean-vps
mkdir -p /opt/talus/data && chmod 700 /opt/talus /opt/talus/data   # then copy .env and data in
cp /opt/talus/src/deploy/nginx-talus.conf /etc/nginx/sites-available/talus
# with only the port-80 server block enabled first:
certbot certonly --webroot -w /var/www/certbot -d talus.weiranxiong.com
ln -s /etc/nginx/sites-available/talus /etc/nginx/sites-enabled/talus
nginx -t && systemctl reload nginx
```

Cloudflare DNS: `A talus 164.92.102.37`, proxied.

## Operating

```bash
ssh digitalocean-vps 'cd /opt/talus/src/deploy && docker compose logs -f --tail 100'
ssh digitalocean-vps 'cd /opt/talus/src/deploy && docker compose exec engine talus account list'
```

Back up by copying `/opt/talus/data` while the engine is stopped, or with `sqlite3 .backup` on each
`.db` file while it runs.

Cloudflare's free plan limits a request body to 100 MB, so a larger Apple Health export has to be
imported on the server with `talus import`.

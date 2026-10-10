# Deploying Talus

Talus runs on the DigitalOcean droplet (`digitalocean-vps` in `~/.ssh/config`) at
https://talus.weiranxiong.com, alongside the other sites there.

| Piece | Where |
|---|---|
| Engine (FastAPI) | `talus-engine-1` container, port 8000 on the compose network only |
| Web (Next.js) | `talus-web-1` container, published on `127.0.0.1:3020` |
| Reverse proxy | host nginx, `/etc/nginx/sites-available/talus` (copy of `nginx-talus.conf`) |
| TLS | Let's Encrypt via certbot on the host; Cloudflare proxied, SSL mode Full (strict) |
| Data | `/opt/talus/data` (accounts, per-user databases, raw files, `secret.key`) |
| Secrets | `/opt/talus/.env`, mode 600 |

## Deploy a new version

From a clean checkout of `main`:

```bash
deploy/deploy.sh
```

It ships the committed tree with `git archive`, rebuilds both images on the server and restarts
them. Data and `.env` stay put. The first build takes a few minutes on the 1 vCPU droplet.

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

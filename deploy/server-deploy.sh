#!/usr/bin/env bash
# Runs on the server as /usr/local/bin/talus-deploy. Reads a tar of engine/ and deploy/ on
# stdin, builds new images, and only then swaps them in, so a failed build leaves the running
# containers alone. If the new engine doesn't answer, it goes back to the previous image. It also
# installs deploy/nginx-talus.conf when that changes. (The web app is on Cloudflare Workers.)
#   git archive HEAD engine deploy | ssh HOST talus-deploy REV
# CI's SSH key is pinned to this command; the revision comes in as SSH_ORIGINAL_COMMAND.
set -euo pipefail
REV="${1:-${SSH_ORIGINAL_COMMAND:-}}"
REV="$(printf %s "${REV##* }" | tr -cd 'a-f0-9' | cut -c1-40)"
cd /opt/talus
exec 9>/opt/talus/.deploy.lock
flock 9
test -f /opt/talus/.env || { echo "missing /opt/talus/.env, see deploy/README.md" >&2; exit 1; }

rm -rf /opt/talus/src.new
mkdir /opt/talus/src.new
tar -x -C /opt/talus/src.new
echo "${REV:-unknown}" > /opt/talus/src.new/REVISION
ln -sf ../../.env /opt/talus/src.new/deploy/.env
export TALUS_DATA_PATH=/opt/talus/data
export TALUS_REVISION="${REV:-unknown}"  # shown on the admin page

echo "== building ${REV:-unknown}"
for img in talus-engine; do
  docker image inspect "$img:latest" >/dev/null 2>&1 && docker tag "$img:latest" "$img:prev"
done
(cd /opt/talus/src.new/deploy && docker compose build)

echo "== starting"
rm -rf /opt/talus/src.prev
[ -d /opt/talus/src ] && mv /opt/talus/src /opt/talus/src.prev
mv /opt/talus/src.new /opt/talus/src
cd /opt/talus/src/deploy
docker compose up -d --remove-orphans

for _ in $(seq 60); do
  if curl -fsS -o /dev/null http://127.0.0.1:${TALUS_ENGINE_PORT:-3021}/api/auth/status; then
    NGINX=/etc/nginx/sites-available/talus
    if ! cmp -s /opt/talus/src/deploy/nginx-talus.conf "$NGINX"; then
      cp "$NGINX" "$NGINX.prev"
      install -m 644 /opt/talus/src/deploy/nginx-talus.conf "$NGINX"
      if nginx -t 2>&1; then systemctl reload nginx && echo "== nginx config updated"
      else echo "== new nginx config failed nginx -t, kept the old one" >&2; cp "$NGINX.prev" "$NGINX"; fi
    fi
    install -m 755 /opt/talus/src/deploy/server-deploy.sh /usr/local/bin/talus-deploy
    docker image prune -f >/dev/null
    docker compose ps --format '{{.Name}} {{.Status}}'
    echo "== deployed ${REV:-unknown}"
    exit 0
  fi
  sleep 2
done

echo "== new version did not come up, rolling back" >&2
docker compose logs --tail 40 >&2 || true
if [ -d /opt/talus/src.prev ]; then
  for img in talus-engine; do docker tag "$img:prev" "$img:latest"; done
  rm -rf /opt/talus/src.failed
  mv /opt/talus/src /opt/talus/src.failed
  mv /opt/talus/src.prev /opt/talus/src
  TALUS_REVISION="$(cat /opt/talus/src/REVISION 2>/dev/null || echo unknown)"
  (cd /opt/talus/src/deploy && docker compose up -d --remove-orphans)
fi
exit 1

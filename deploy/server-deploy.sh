#!/usr/bin/env bash
# Runs on the server as /usr/local/bin/talus-deploy. Reads a tar of engine/, web/ and deploy/ on
# stdin, builds new images, and only then swaps them in, so a failed build leaves the running
# containers alone. If the new web app doesn't answer, it goes back to the previous images.
#   git archive HEAD engine web deploy | ssh HOST talus-deploy REV
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
for img in talus-engine talus-web; do
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
  if curl -fsS -o /dev/null http://127.0.0.1:${TALUS_WEB_PORT:-3020}/login \
     && docker compose exec -T engine python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/auth/status')" 2>/dev/null; then
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
  for img in talus-engine talus-web; do docker tag "$img:prev" "$img:latest"; done
  rm -rf /opt/talus/src.failed
  mv /opt/talus/src /opt/talus/src.failed
  mv /opt/talus/src.prev /opt/talus/src
  TALUS_REVISION="$(cat /opt/talus/src/REVISION 2>/dev/null || echo unknown)"
  (cd /opt/talus/src/deploy && docker compose up -d --remove-orphans)
fi
exit 1

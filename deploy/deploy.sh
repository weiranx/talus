#!/usr/bin/env bash
# Deploy the committed tree at HEAD from this machine. CI does the same on every push to main.
#   deploy/deploy.sh                 # to digitalocean-vps
#   TALUS_HOST=other deploy/deploy.sh
# The server keeps /opt/talus/.env and /opt/talus/data; deploys never touch either.
set -euo pipefail
HOST="${TALUS_HOST:-digitalocean-vps}"
cd "$(git rev-parse --show-toplevel)"
REV="$(git rev-parse HEAD)"
[ -z "$(git status --porcelain -- engine web deploy)" ] || echo "note: uncommitted changes are not deployed, only ${REV:0:7}"
echo "Deploying ${REV:0:7} to $HOST"
git archive --format=tar HEAD engine web deploy | ssh "$HOST" talus-deploy "$REV"

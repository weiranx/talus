#!/usr/bin/env bash
# Ship the committed tree at HEAD to the server and rebuild the containers.
#   deploy/deploy.sh                 # deploy HEAD to digitalocean-vps:/opt/talus
#   TALUS_HOST=other deploy/deploy.sh
# The server keeps /opt/talus/.env and /opt/talus/data; this script never touches either.
set -euo pipefail
HOST="${TALUS_HOST:-digitalocean-vps}"
DIR="${TALUS_DIR:-/opt/talus}"
cd "$(git rev-parse --show-toplevel)"
REV="$(git rev-parse --short HEAD)"
[ -z "$(git status --porcelain -- engine web deploy)" ] || echo "note: uncommitted changes are not deployed, only $REV"

echo "Shipping $REV to $HOST:$DIR"
git archive --format=tar HEAD engine web deploy | ssh "$HOST" "set -e
  mkdir -p $DIR/src $DIR/data && chmod 700 $DIR/data
  rm -rf $DIR/src.new && mkdir $DIR/src.new && tar -x -C $DIR/src.new
  rm -rf $DIR/src && mv $DIR/src.new $DIR/src && echo $REV > $DIR/src/REVISION
  test -f $DIR/.env || { echo 'missing $DIR/.env, see deploy/README.md'; exit 1; }
  ln -sf ../../.env $DIR/src/deploy/.env
  cd $DIR/src/deploy && TALUS_DATA_PATH=$DIR/data docker compose up -d --build
  docker image prune -f >/dev/null
  docker compose ps"

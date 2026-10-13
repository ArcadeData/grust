#!/bin/bash
# Build an ArcadeDB server image on Temurin 25 from a local ArcadeDB checkout.
#   arcadedb/tools/build-server-image.sh ~/Documents/GitHub/arcadedb [commit]
# Tags the result arcadedb-bench:<short commit>-j25.
set -euo pipefail
SRC=${1:?usage: build-server-image.sh ARCADEDB_CHECKOUT [COMMIT]}
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$SRC"
[ -n "${2:-}" ] && git checkout -q "$2"
COMMIT=$(git rev-parse --short=10 HEAD)
mvn -q package -DskipTests -pl package -am
DIR=$(ls -d package/target/arcadedb-*.dir | head -1)
docker build -q -f "$HERE/Dockerfile.temurin25" -t "arcadedb-bench:$COMMIT-j25" "$DIR"
echo "arcadedb-bench:$COMMIT-j25"

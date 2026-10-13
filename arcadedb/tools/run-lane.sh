#!/bin/bash
# Run the native ArcadeDB lane (benchmarks/lsqb/ARCADEDB.md) at example, SF0.1
# and SF0.3, W2/R10, one fresh server per scale, then audit each run.
#   arcadedb/tools/run-lane.sh SERVER_IMAGE [SCALES...]
# SERVER_IMAGE is the pinned image (tag@digest) or, for development, any image
# (the run is then marked unpinned). The client image is built from HEAD.
set -uo pipefail
SERVER_IMAGE=${1:?usage: run-lane.sh SERVER_IMAGE [example 0.1 0.3]}; shift
SCALES=${*:-example 0.1 0.3}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
cd "$REPO"
SHA=$(git rev-parse HEAD)
CLIENT=grust-lsqb-arcadedb:${SHA:0:8}
docker build -q -f benchmarks/lsqb/Dockerfile --build-arg BENCHMARK_FEATURE=arcadedb-native \
  --build-arg GRUST_SOURCE_REVISION="$SHA" -t "$CLIENT" . >/dev/null || exit 1
docker network inspect grust-lsqb-arcadedb-qualification >/dev/null 2>&1 ||
  docker network create --internal grust-lsqb-arcadedb-qualification >/dev/null
UNPINNED=()
case "$SERVER_IMAGE" in *@sha256:*) ;; *) UNPINNED=(--diagnostic-server-image) ;; esac
cd benchmarks/lsqb
for scale in $SCALES; do
  out=out/arcadedb-sf$scale-${SHA:0:8}-$(date +%Y%m%d-%H%M)
  docker rm -f grust-lsqb-arcadedb-dev >/dev/null 2>&1
  docker run -d --name grust-lsqb-arcadedb-dev --network grust-lsqb-arcadedb-qualification \
    --cpus 8 --memory 6g --memory-swap 6g \
    -e JAVA_OPTS="-Darcadedb.server.rootPassword=benchmark -Darcadedb.command.timeout=60000 -Darcadedb.server.httpBodyContentMaxSize=4294967296" \
    "$SERVER_IMAGE" >/dev/null || exit 1
  sleep 45   # the host preflight refuses a server still warming up
  ARCADEDB_PASSWORD=benchmark python3 run-native-arcadedb.py --server grust-lsqb-arcadedb-dev --image "$CLIENT" \
    --source-revision "$SHA" --output "$out" --scale "$scale" --warmups 2 --runs 10 "${UNPINNED[@]}" | tail -1
  python3 validate-arcadedb-diagnostic.py "$out" --runtime --unpinned --matched-sampling --require-cancellation \
    | python3 -c "import json,sys; r=json.load(sys.stdin); print('$scale', r['outcomes'], 'cancellation', r['cancellation_verified'])"
  python3 "$REPO/arcadedb/tools/summarize.py" "$out"
done
docker rm -f grust-lsqb-arcadedb-dev >/dev/null 2>&1

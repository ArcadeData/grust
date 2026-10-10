# Native ArcadeDB comparison lane

**Status: diagnostic, not publishable yet.** Counts, sampling, runtime records,
the coordinator deadline and server-side cancellation all qualify on a server
built from ArcadeDB `main`; what is missing is a pinned release image that
contains the running-statement listing and termination
([ArcadeData/arcadedb#9680](https://github.com/ArcadeData/arcadedb/issues/9680)).
These are not LDBC Benchmark Results.

This is a native engine comparator modelled on the [Neo4j lane](NEO4J.md), not
a Grust adapter. The private harness feature is `arcadedb-native`; the binary is
`grust-lsqb-arcadedb`.

## Protocol

- **Server:** the pinned ArcadeDB release image (the first release with #9680; the
  runner accepts others only with `--diagnostic-server-image`, and those runs
  are marked unpinned and cannot be bundled). 8 CPU / 6 GiB without swap, no
  host ports, attached only to the internal `grust-lsqb-arcadedb-qualification`
  network. Image defaults otherwise (heap `-XX:MaxRAMPercentage=75`, ZGC), plus
  three required settings the runner checks without exporting the environment:
  `-Darcadedb.command.timeout=60000` (the server bounds its own work by the
  comparison deadline), `-Darcadedb.server.httpBodyContentMaxSize` of at least
  2 GiB (the import is one streamed request), and a disposable root password.
  The password reaches the client as `--env ARCADEDB_PASSWORD` by name, so it is
  never written into `invocation.json`; the audit checks that.
- **Client:** HTTP/1.1 over a persistent connection (a small built-in client, no
  new dependency). The HTTP API is the only protocol used.
- **Import:** a database the run creates (refused if it exists; never cleared),
  schema from the projected file names, Post and Comment as subtypes of Message
  that keep `kind`, so `(:Message)`, `(:Post)` and `{kind: 'Post'}` all match as
  in the reference model. One streamed `/api/v1/batch` request (ArcadeDB's bulk
  loader) with `expectedRecords`, edges with their `id` and multiplicity. The
  server's created counts must equal the records sent, then
  `MATCH (n) RETURN count(n)` and `MATCH ()-[r]->() RETURN count(r)` must equal
  the dataset totals. Finally ArcadeDB's Graph Analytical View (its
  read-optimised adjacency for traversals) is built once. All of this is inside
  the 600-second load budget and reported as `load_ns`, split into `import_ns`
  and `analytical_view_ns`.
- **Observations:** the shared READY/GO isolated-process protocol, rotating
  W2/R10, 60-second coordinator deadline, a fresh worker per sample. The worker
  opens an HTTP transaction session (`/api/v1/begin`) before READY; after GO it
  runs the query in that session, consumes the single scalar and rolls back
  (`/api/v1/rollback`), all inside the measured interval.
- **Recovery:** the worker sends `X-ArcadeDB-Query-Tag: arcadedb-<pid>-<nanos>`
  on `/api/v1/begin` and on its query, so the server lists the sample's session
  and statement under that tag. After every sample, whatever its outcome, a
  separate connection lists them (`list queries tag <tag>`, `list transactions`),
  terminates exactly those (`terminate query <id>`, `terminate transaction <id>`)
  and polls until neither listing holds the tag, within 15 seconds or the run
  stops. A statement leaves ArcadeDB's listing only after its rollback and
  cleanup, so absence means the server no longer works on it. Then `RETURN 42`.
  Each observation records `cancellation_verified: true`, the terminated
  statement and session ids, work that ended before termination, and zero
  owned statements and sessions remaining; the audit requires all of it.

## Running

```bash
docker network create --internal grust-lsqb-arcadedb-qualification
docker run -d --name grust-lsqb-arcadedb-1 --network grust-lsqb-arcadedb-qualification \
  --cpus 8 --memory 6g --memory-swap 6g \
  -e JAVA_OPTS="-Darcadedb.server.rootPassword=<disposable> -Darcadedb.command.timeout=60000 -Darcadedb.server.httpBodyContentMaxSize=4294967296" \
  <pinned ArcadeDB image>
docker build -f benchmarks/lsqb/Dockerfile --build-arg BENCHMARK_FEATURE=arcadedb-native \
  --build-arg GRUST_SOURCE_REVISION=$(git rev-parse HEAD) -t grust-lsqb-arcadedb .
ARCADEDB_PASSWORD=<disposable> python3 benchmarks/lsqb/run-native-arcadedb.py \
  --server grust-lsqb-arcadedb-1 --image grust-lsqb-arcadedb --source-revision $(git rev-parse HEAD) \
  --output benchmarks/lsqb/out/<new> --scale 0.1 --warmups 2 --runs 10
python3 benchmarks/lsqb/validate-arcadedb-diagnostic.py benchmarks/lsqb/out/<new> --runtime --matched-sampling --summaries
```

Each run needs a fresh server (the import refuses an existing database). Let the
server go idle before starting: the host preflight refuses a busy Docker VM.

## Development runs (unpinned, not publishable)

Client source `1c11257e`, server built from ArcadeDB `main` at `2384a46e52`
(#9680 merged) on `eclipse-temurin:25-alpine`, one Apple Silicon laptop on AC
power, `--diagnostic-server-image`. All three passed
`validate-arcadedb-diagnostic.py --runtime --unpinned --matched-sampling --require-cancellation`:
264 of 264 samples pass (44 warm-ups, 220 measurements), no mismatch, timeout
or error, cancellation verified on every sample with nothing left running
(every sample rolled back on its own, so no targeted termination was needed),
journal and report identical. The recovery check takes a median 0.6-0.7 ms and
is outside the measured interval.

| Scale | Load (import + view) | q1-q9 medians | slowest attack medians |
|---|---|---|---|
| example | 0.4 s | 1.1-2.3 ms | a6 2.6 ms |
| SF0.1 | 9.9 s (8.2 + 1.6) | 7.2-24.5 ms | a2 110 ms, a1 38 ms, a5 34 ms |
| SF0.3 | 27.8 s (18.9 + 8.8) | 16.9-79.9 ms | a2 446 ms, a1 140 ms, a5 136 ms |

Probes on the same server:
- `recovery-probe`: the stress statement was observed running inside its tagged
  session, `terminate query` acknowledged `terminated` after 106 ms, the worker
  request failed with HTTP 409, no tagged statement or session remained, next
  scalar 42.
- `deadline-probe`: the coordinator deadline fired at 2.0003 s (SIGTERM); the
  server was still running the abandoned statement, which the recovery step
  terminated (its session ended with it) in 28 ms; the next isolated scalar
  returned 42.

## Before publication

1. A released ArcadeDB image that contains #9680 (merged to `main` as
   `2384a46e52`).
2. Pin the platform image digests of that release in `run-native-arcadedb.py` and
   `validate-arcadedb-diagnostic.py`, and the reviewed client source/image in
   `CLIENT_PROFILES`.
3. Clean-host W2/R10 runs at example, SF0.1 and SF0.3, then
   `bundle-native-arcadedb.py`.

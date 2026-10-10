# Native ArcadeDB comparison lane

**Status: diagnostic, not publishable yet.** Counts, sampling, runtime records
and the coordinator deadline all qualify. Server-side cancellation proof does
not exist yet: ArcadeDB has no HTTP listing or targeted termination of running
statements ([ArcadeData/arcadedb#9680](https://github.com/ArcadeData/arcadedb/issues/9680)).
Every observation records `cancellation_verified: false` with that reference,
the audit's `--require-cancellation` gate refuses such runs, and
`bundle-native-arcadedb.py` applies the gate. These are not LDBC Benchmark Results.

This is a native engine comparator modelled on the [Neo4j lane](NEO4J.md), not
a Grust adapter. The private harness feature is `arcadedb-native`; the binary is
`grust-lsqb-arcadedb`.

## Protocol

- **Server:** the pinned ArcadeDB release image (26.10.2 once published; the
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
- **Recovery:** after every sample a separate connection runs `RETURN 42`.
  Proof that the server no longer works on the sample's statement needs #9680.

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

Client source `9899d885`, server built from ArcadeDB `main` at `2b0a7a636f` on
`eclipse-temurin:25-alpine` (the base 26.10.2 will ship), one Apple Silicon
laptop on AC power, `--diagnostic-server-image`. All three passed
`validate-arcadedb-diagnostic.py --runtime --unpinned --matched-sampling`:
264 of 264 samples pass (44 warm-ups, 220 measurements), no mismatch, timeout
or error, journal and report identical.

| Scale | Load (import + view) | q1-q9 medians | slowest attack medians |
|---|---|---|---|
| example | 0.4 s | 1.1-2.3 ms | a6 2.8 ms |
| SF0.1 | 10.5 s (8.4 + 2.0) | 7.0-24.6 ms | a2 119 ms, a1 43 ms, a5 37 ms |
| SF0.3 | 29.2 s (20.6 + 8.6) | 17.3-82.1 ms | a2 484 ms, a1 147 ms, a5 145 ms |

The forced 2-second deadline probe passed on the coordinator side (deadline at
2.0007 s, SIGTERM and reap in 2.3 ms, next isolated scalar 42), and showed the
gap #9680 closes: the server kept one core busy for about ten more seconds on
the abandoned statement. The targeted-termination `recovery-probe` refuses to
run until #9680 exists.

## Before publication

1. ArcadeDB #9680, then tag each statement, list and terminate it in
   `native_arcadedb_recovery.rs`, record `owned_statements_remaining: 0`, and
   implement `recovery-probe`.
2. Pin the 26.10.2 platform image digests in `run-native-arcadedb.py` and
   `validate-arcadedb-diagnostic.py`, and the reviewed client source/image in
   `CLIENT_PROFILES`.
3. Clean-host W2/R10 runs at example, SF0.1 and SF0.3, then
   `bundle-native-arcadedb.py`.

# ArcadeDB on the LSQB native lane: working tools and history

This folder is ArcadeDB's working area for the native LSQB comparison lane. It
is **not** part of the lane and is not proposed upstream: the lane itself
(client, runner, audit, bundle, [`ARCADEDB.md`](../benchmarks/lsqb/ARCADEDB.md))
lives in `benchmarks/lsqb/` on the `arcadedb-lane` branch, which is the branch
the pull request to `querygraph/grust` comes from. This branch,
`arcadedb-tools`, is `arcadedb-lane` plus this folder.

Nothing here is an LDBC Benchmark Result.

## Contents

| Path | What it is |
|---|---|
| `dryrun.py`, `oracle.json` | A quick check of the 9 LSQB queries and the 13 count attacks against an ArcadeDB image, over HTTP, without building the Rust client. It reads the queries and attacks from this repository (`benchmarks/lsqb/upstream/lsqb/cypher`, `benchmarks/lsqb/attacks`) and checks every count against `oracle.json` (the expected counts of the grust contract). Useful as an engine regression check: about a minute at SF0.1. |
| `runs/` | Dry-run results (`result.json` with per-query medians, `observations.jsonl`), one folder per run. |
| `tools/build-server-image.sh`, `tools/Dockerfile.temurin25` | Build an ArcadeDB server image on Temurin 25 from any commit of a local ArcadeDB checkout (`arcadedb-bench:<commit>-j25`). |
| `tools/run-lane.sh` | Run the real lane (grust's harness, `run-native-arcadedb.py`) at example, SF0.1 and SF0.3 with W2/R10, a fresh server per scale, and audit each run with the cancellation gate. |
| `tools/summarize.py` | Load, outcomes and per-query medians of a lane run, optionally against a previous run. |
| `lane-runs/` | Summaries of the lane runs and the termination/deadline probes. |
| `bundles/` | The frozen evidence bundles of the qualified 26.10.2 runs (`bundle-native-arcadedb.py` output, one folder per scale). |

## Running

The data comes from the lane's own fetch steps (see `benchmarks/lsqb/README.md`):
`benchmarks/lsqb/upstream/lsqb` for the queries and the example data,
`benchmarks/lsqb/data/` for SF0.1 and SF0.3.

```bash
# Dry run (starts its own container on port 2480)
python3 arcadedb/dryrun.py --scale 0.1 --image arcadedata/arcadedb:26.11.1-SNAPSHOT

# Server image from an engine commit
arcadedb/tools/build-server-image.sh ~/Documents/GitHub/arcadedb <commit>

# The lane at three scales (a tag@digest image is a pinned run; any other is marked unpinned)
arcadedb/tools/run-lane.sh arcadedb-bench:<commit>-j25
python3 arcadedb/tools/summarize.py benchmarks/lsqb/out/<run> [benchmarks/lsqb/out/<previous run>]
```

Rules from the ArcadeDB benchmark repository apply: AC power only, one
database container at a time, every count validated, Temurin 25.

## Engine issues found

| Issue | Fixed |
|---|---|
| [#9595](https://github.com/ArcadeData/arcadedb/issues/9595) a property predicate on a pattern node took count-only chains off the Graph Analytical View (Q1 0.016 s to 6.7 s) | yes |
| [#9596](https://github.com/ArcadeData/arcadedb/issues/9596) `count(*)` over a Cartesian product enumerated every row | yes |
| [#9597](https://github.com/ArcadeData/arcadedb/issues/9597) a pass-through `WITH` before `OPTIONAL MATCH` (Q7) | yes |
| [#9598](https://github.com/ArcadeData/arcadedb/issues/9598) `OPTIONAL MATCH ... WHERE h IS NULL` not planned as an anti-join (Q8) | yes |
| [#9599](https://github.com/ArcadeData/arcadedb/issues/9599) the order of comma-separated patterns changed the cost 17x (Q2) | yes |
| [#9600](https://github.com/ArcadeData/arcadedb/issues/9600) edge counts walked the graph instead of reading the counters | yes |
| [#9602](https://github.com/ArcadeData/arcadedb/issues/9602) Docker images on Java 21: move to Temurin 25 | yes |
| [#9608](https://github.com/ArcadeData/arcadedb/issues/9608) a property predicate still disabled the pair-join and anti-join push-downs (Q2, Q8) | yes |
| [#9680](https://github.com/ArcadeData/arcadedb/issues/9680) list and terminate running queries over HTTP (the lane's cancellation proof) | yes |
| [#9953](https://github.com/ArcadeData/arcadedb/issues/9953) since the client-disconnect watch, an HTTP statement longer than `arcadedb.network.socketTimeout` (30 s) was killed with no response (the session left open after `terminate query` on a read is intended; the answer now carries `sessionId`/`sessionOpen`) | yes (#10010) |

## History

Dry runs at SF0.1 (`runs/`), slowest medians:

| Run | Server | Result |
|---|---|---|
| 2026-10-09 16:52 | published `26.11.1-SNAPSHOT` | a7 times out; a5 7.2 s, a1 6.7 s, a2 1.7 s |
| 2026-10-09 19:49 | `89100cb279` (#9595-#9597) | all counts right; a5 7.1 s, a2 1.5 s |
| 2026-10-09 22:11 | `3de8af2a5d` (#9598-#9600) | a2 1.5 s, a5 0.66 s, a1 0.06 s |
| 2026-10-09 23:38 | `2b0a7a636f` (#9608) | a2 0.13 s, everything else at most 0.06 s |
| 2026-10-10 00:11 | `2b0a7a636f` on Temurin 25 | a2 0.15 s, a5 0.06 s |
| 2026-10-13 17:54 | published `26.11.1-SNAPSHOT` (`5100e4bf5b`) | 22/22 at every scale; a2 0.13 s (SF0.3 0.43 s); on battery |

The lane (grust's harness, W2/R10, `lane-runs/`), server `main` at
`2384a46e52` (#9680) on Temurin 25, client `1c11257e`: 264 of 264 samples
pass at every scale, cancellation verified on every sample.

| Scale | Load | q1-q9 medians | Slowest attacks |
|---|---|---|---|
| example | 0.4 s | 1.1-2.3 ms | a6 2.6 ms |
| SF0.1 | 9.9 s | 7.2-24.5 ms | a2 110 ms, a1 38 ms |
| SF0.3 | 27.8 s | 16.9-79.9 ms | a2 446 ms, a1 140 ms |

The same lane on the published `26.11.1-SNAPSHOT` (`main` at `5100e4bf5b`,
pinned by digest), client built at `ee004b5f` (lane source `a388574f`; that tools commit was later rebased), 2026-10-13: 264 of 264 at every scale,
cancellation verified, audit passes. Taken on battery, so the times are not
comparable (they are close to the table above: SF0.3 q1-q9 16-77 ms, a2 418 ms).
That build stops a statement when its HTTP client disconnects and closes the
statement's session a few seconds after a `terminate query`; the probes were
adjusted for both (`8bb8d768`) and pass twice each
(`lane-runs/probes-8bb8d768-snapshot-5100e4bf5b.jsonl`).

**Qualified, 2026-10-15:** ArcadeDB 26.10.2 (pinned by digest), client
`cf184468` (in `CLIENT_PROFILES`), AC power, each run started on a quiet host:
264 of 264 at every scale, pinned audit passes, cancellation verified, zero
targeted terminations; both probes pass. Bundles in `bundles/`, manifest hashes
in `benchmarks/lsqb/ARCADEDB.md`.

| Scale | Load | q1-q9 medians | Slowest attacks |
|---|---|---|---|
| example | 0.4 s | 1.2-1.3 ms | a6 2.5 ms |
| SF0.1 | 9.8 s | 6.9-23.3 ms | a2 106 ms, a1 37 ms |
| SF0.3 | 27.7 s | 16.4-76.8 ms | a2 451 ms, a1 132 ms, a5 130 ms |

On AC power, 2026-10-14, server built from `main` at `620cb7c94d` (#9953 fixed
by #10010) on Temurin 25, client `94be618c`: 264 of 264 at every scale,
cancellation verified, both probes pass. Times equal the `2384a46e52` table
within noise (SF0.3 q1-q9 16.3-77.4 ms, a2 446 ms, a1 140 ms; load 27.7 s).

## Before the pull request

Done: 26.10.2 pinned (`cf184468`), client qualified and bundles frozen
(`b6a65552`). Next: the pull request from `arcadedb-lane` to `querygraph/grust`,
then independent site admission of the bundles.

#!/usr/bin/env python3
"""Local dry run of the adversari.al LSQB native lane for ArcadeDB (not a submission, not LDBC results).

Mirrors the protocol of querygraph/grust benchmarks/lsqb (Neo4j native lane) as closely as a local run can:

- ArcadeDB server image in a disposable container: 8 CPUs, 6 GiB, no swap (--memory-swap = --memory).
- The pinned projected-FK LSQB data (example, SF0.1, SF0.3), loaded through ArcadeDB's own bulk loader
  (HTTP /api/v1/batch). Post and Comment are Message subtypes and carry kind='Post'/'Comment' (the attacks use it).
  Node and edge totals are checked against the oracle before any query runs.
- The 9 upstream LSQB Cypher queries (benchmarks/lsqb/upstream/lsqb/cypher/q*.cypher, lsqb commit 242cb2fd, fetched
  by benchmarks/lsqb/fetch-upstream-source.py) and the 13 count attacks (benchmarks/lsqb/attacks/a*.cypher), read
  from this repository unchanged; every count is compared with oracle.json.
- Rotating query order, 2 warm-ups + 10 measurements per query, a fresh worker process per observation, a 60 s
  query deadline enforced by the server (arcadedb.command.timeout) with a check that the container goes idle after a
  timeout. Time = submit to scalar consumed, on the client (HTTP, read-only /api/v1/query).

    python3 arcadedb/dryrun.py --scale example
    python3 arcadedb/dryrun.py --scale 0.1 [--heap 4g] [--no-gav] [--skip-after-timeouts 2]

--skip-after-timeouts N stops re-running a query after N consecutive timeouts (dry-run shortcut, flagged in the
output; the real protocol runs every observation).
"""

import argparse
import base64
import datetime
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LSQB = os.path.abspath(os.path.join(HERE, "..", "benchmarks", "lsqb"))
CONTAINER = "arcadedb-adv"
PORT = 2480
DB = "lsqb"
AUTH = "Basic " + base64.b64encode(b"root:benchmark").decode()
DEADLINE_S = 60

VERTEX_TYPES = ["Continent", "Country", "City", "Company", "University", "TagClass", "Tag", "Person", "Forum",
                "Post", "Comment"]


def http(method, path, body=None, timeout=30, content_type="application/json", chunks=None):
    headers = {"Authorization": AUTH, "Content-Type": content_type}
    if chunks is not None:  # streamed upload (chunked transfer), as the SF1 driver does
        data = chunks
        headers["Transfer-Encoding"] = "chunked"
    else:
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    req = urllib.request.Request(f"http://localhost:{PORT}{path}", data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def sql(command, timeout=600):
    return http("POST", f"/api/v1/command/{DB}", {"language": "sql", "command": command}, timeout)


def snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()


# ---------------------------------------------------------------- worker (one observation, fresh process)

def worker(query_file):
    query = open(query_file).read()
    t0 = time.perf_counter()
    try:
        res = http("POST", f"/api/v1/query/{DB}", {"language": "opencypher", "command": query},
                   timeout=DEADLINE_S + 15)
        rows = res["result"]
        if len(rows) != 1 or len(rows[0]) != 1:
            raise ValueError(f"not a single scalar: {str(rows)[:200]}")
        value = int(next(iter(rows[0].values())))
        out = {"outcome": "ok", "count": value}
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace")[:400]
        out = {"outcome": "timeout" if "imeout" in text else "error", "detail": text}
    except Exception as e:  # noqa: BLE001
        out = {"outcome": "timeout" if "timed out" in str(e) else "error", "detail": str(e)[:400]}
    out["seconds"] = time.perf_counter() - t0
    print(json.dumps(out))


# ---------------------------------------------------------------- container

def docker(*args, check=True):
    return subprocess.run(["docker", *args], check=check, capture_output=True, text=True).stdout.strip()


def start_server(image, heap):
    docker("rm", "-f", CONTAINER, check=False)
    java_opts = (f"-Darcadedb.server.rootPassword=benchmark -Darcadedb.command.timeout={DEADLINE_S * 1000} "
                 "-Darcadedb.server.httpBodyContentMaxSize=1073741824")  # the batch payload exceeds the 100 MB default
    docker("run", "-d", "--name", CONTAINER, "--cpus", "8", "--memory", "6g", "--memory-swap", "6g",
           "-p", f"{PORT}:2480", "-e", f"JAVA_OPTS={java_opts}",
           "-e", f"ARCADEDB_OPTS_MEMORY=-Xms{heap} -Xmx{heap}", image)
    t0 = time.time()
    while time.time() - t0 < 120:
        try:
            http("GET", "/api/v1/server", timeout=3)
            return time.time() - t0
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    raise RuntimeError("server not ready in 120 s:\n" + docker("logs", "--tail", "30", CONTAINER, check=False))


def container_cpu():
    out = docker("stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}", CONTAINER, check=False)
    try:
        cpu, mem = out.split("|")
        return float(cpu.strip("%")), mem.split("/")[0].strip()
    except ValueError:
        return None, None


def wait_quiet(limit_s=60):
    """After a timeout: wait until the server container is idle (< 5% CPU); return the seconds it took or None."""
    t0 = time.time()
    while time.time() - t0 < limit_s:
        cpu, _ = container_cpu()
        if cpu is not None and cpu < 5:
            return time.time() - t0
    return None


class MemorySampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.peak_mib, self.stop = 0.0, False

    def run(self):
        while not self.stop:
            _, mem = container_cpu()
            if mem:
                m = re.match(r"([\d.]+)\s*([KMG]i?B)", mem)
                if m:
                    mib = float(m.group(1)) * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}.get(m.group(2), 1)
                    self.peak_mib = max(self.peak_mib, mib)
            time.sleep(1)


# ---------------------------------------------------------------- load

def load(data_dir, gav):
    t0 = time.perf_counter()
    http("POST", "/api/v1/server", {"command": f"create database {DB}"}, timeout=60)
    for t in VERTEX_TYPES:
        if t in ("Post", "Comment"):
            continue
        sql(f"CREATE VERTEX TYPE {t}")
    sql("CREATE VERTEX TYPE Message")
    sql("CREATE VERTEX TYPE Post EXTENDS Message")
    sql("CREATE VERTEX TYPE Comment EXTENDS Message")
    files = sorted(f for f in os.listdir(data_dir) if f.endswith(".csv"))
    edge_files = [f for f in files if f.count("_") == 2]
    edge_types = sorted({snake(f[:-4].split("_")[1]) for f in edge_files})
    for e in edge_types:
        sql(f"CREATE EDGE TYPE {e}")

    def payload():
        buf = []
        for t in VERTEX_TYPES:
            with open(os.path.join(data_dir, f"{t}.csv")) as f:
                next(f)
                for line in f:
                    rec = {"@type": "vertex", "@class": t, "@id": f"{t}:{line.strip()}", "cid": int(line)}
                    if t in ("Post", "Comment"):
                        rec["kind"] = t
                    buf.append(json.dumps(rec))
                    if len(buf) >= 50000:
                        yield ("\n".join(buf) + "\n").encode()
                        buf = []
        for fname in edge_files:
            src, rel, dst = fname[:-4].split("_")
            label = snake(rel)
            with open(os.path.join(data_dir, fname)) as f:
                next(f)
                for line in f:
                    a, b = line.rstrip("\n").split("|")
                    buf.append(json.dumps({"@type": "edge", "@class": label,
                                           "@from": f"{src}:{a}", "@to": f"{dst}:{b}"}))
                    if len(buf) >= 50000:
                        yield ("\n".join(buf) + "\n").encode()
                        buf = []
        if buf:
            yield ("\n".join(buf) + "\n").encode()

    res = http("POST", f"/api/v1/batch/{DB}?wal=false&lightEdges=false", timeout=1800,
               content_type="application/x-ndjson", chunks=payload())
    load_s = time.perf_counter() - t0
    gav_s = None
    if gav:
        t1 = time.perf_counter()
        sql(f"CREATE GRAPH ANALYTICAL VIEW {DB} VERTEX TYPES ({', '.join(VERTEX_TYPES)}) "
            f"EDGE TYPES ({', '.join(edge_types)})")
        sql(f"REBUILD GRAPH ANALYTICAL VIEW {DB}", timeout=1800)
        gav_s = time.perf_counter() - t1
    return load_s, gav_s, res


def counts():
    v = sum(sql(f"SELECT count(*) AS c FROM {t}")["result"][0]["c"] for t in VERTEX_TYPES)
    types = http("POST", f"/api/v1/query/{DB}", {"language": "sql",
                 "command": "SELECT name FROM schema:types WHERE type = 'edge'"})["result"]
    e = sum(sql(f"SELECT count(*) AS c FROM `{t['name']}`")["result"][0]["c"] for t in types)
    return v, e


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", required=True, choices=["example", "0.1", "0.3"])
    ap.add_argument("--image", default=os.environ.get("ARCADEDB_IMAGE", "arcadedata/arcadedb:26.11.1-SNAPSHOT"))
    ap.add_argument("--heap", default="4g")
    ap.add_argument("--no-gav", action="store_true")
    ap.add_argument("--warmups", type=int, default=2)
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--skip-after-timeouts", type=int, default=0)
    ap.add_argument("--only", help="comma-separated query ids")
    ap.add_argument("--keep", action="store_true", help="leave the container running at the end")
    ap.add_argument("--worker", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.worker:
        return worker(args.worker)

    oracle = json.load(open(os.path.join(HERE, "oracle.json")))
    ds = "sfexample" if args.scale == "example" else f"sf{args.scale}"
    # The same pinned data the lane uses (benchmarks/lsqb/README.md: fetch-upstream-source.py and the data fetch)
    data_dir = os.path.join(LSQB, "upstream/lsqb/data" if args.scale == "example" else "data",
                            f"social-network-{ds}-projected-fk")
    cases = [(q, "baseline", os.path.join(LSQB, "upstream", "lsqb", "cypher", f"{q}.cypher"),
              oracle["baseline"][q][args.scale]) for q in oracle["order"]["baseline"]]
    cases += [(a, "adversarial", os.path.join(LSQB, "attacks", f"{a}.cypher"), oracle["adversarial"][a][args.scale])
              for a in oracle["order"]["adversarial"]]
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c[0] in keep]

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(HERE, "runs", f"{stamp}-{ds}{'-nogav' if args.no_gav else ''}")
    os.makedirs(out_dir)
    image_id = docker("image", "inspect", "--format", "{{.Id}}", args.image)
    meta = {"scale": args.scale, "image": args.image, "image_id": image_id, "heap": args.heap,
            "gav": not args.no_gav, "limits": "8 CPU, 6 GiB, no swap", "deadline_s": DEADLINE_S,
            "warmups": args.warmups, "reps": args.reps, "skip_after_timeouts": args.skip_after_timeouts,
            "started": stamp, "warning": "Local dry run. These are not LDBC Benchmark Results."}
    print(json.dumps(meta, indent=1), flush=True)

    sampler = MemorySampler()
    try:
        ready_s = start_server(args.image, args.heap)
        sampler.start()
        print(f"server ready in {ready_s:.1f}s; loading {data_dir}", flush=True)
        load_s, gav_s, batch = load(data_dir, not args.no_gav)
        v, e = counts()
        exp = oracle["datasets"][args.scale]
        meta.update(ready_s=ready_s, load_s=load_s, gav_s=gav_s, nodes=v, edges=e,
                    counts_ok=(v == exp["nodes"] and e == exp["edges"]), batch=batch)
        print(f"load {load_s:.1f}s, GAV {gav_s if gav_s is None else round(gav_s, 1)}s, "
              f"nodes {v}/{exp['nodes']}, edges {e}/{exp['edges']}", flush=True)
        if not meta["counts_ok"]:
            raise SystemExit("node/edge totals differ from the oracle; not running queries")

        observations, consecutive_to, skipped = [], {}, set()
        log = open(os.path.join(out_dir, "observations.jsonl"), "w")
        rounds = args.warmups + args.reps
        for r in range(rounds):
            phase = "warmup" if r < args.warmups else "measure"
            order = cases[r % len(cases):] + cases[:r % len(cases)]
            for qid, suite, qfile, expected in order:
                if qid in skipped:
                    obs = {"query": qid, "suite": suite, "round": r, "phase": phase, "outcome": "skipped"}
                else:
                    raw = subprocess.run([sys.executable, "-I", os.path.abspath(__file__), "--scale", args.scale,
                                          "--worker", qfile], capture_output=True, text=True,
                                         timeout=DEADLINE_S + 60).stdout
                    obs = json.loads(raw.strip().splitlines()[-1])
                    obs.update(query=qid, suite=suite, round=r, phase=phase, expected=expected)
                    if obs["outcome"] == "ok" and obs["count"] != expected:
                        obs["outcome"] = "mismatch"
                    if obs["outcome"] == "timeout":
                        obs["quiet_after_s"] = wait_quiet()
                        consecutive_to[qid] = consecutive_to.get(qid, 0) + 1
                        if args.skip_after_timeouts and consecutive_to[qid] >= args.skip_after_timeouts:
                            skipped.add(qid)
                    else:
                        consecutive_to[qid] = 0
                observations.append(obs)
                log.write(json.dumps(obs) + "\n")
                log.flush()
                print(f"[{phase} {r + 1}/{rounds}] {qid:<26} {obs['outcome']:<8} "
                      f"{obs.get('seconds', 0):8.3f}s {obs.get('count', '')}", flush=True)
        log.close()
    finally:
        sampler.stop = True
        meta["peak_container_mib"] = round(sampler.peak_mib)
        if not args.keep:
            docker("rm", "-f", CONTAINER, check=False)

    summary = []
    for qid, suite, _, expected in cases:
        obs = [o for o in observations if o["query"] == qid]
        meas = [o for o in obs if o["phase"] == "measure"]
        outcomes = sorted({o["outcome"] for o in obs})
        times = [o["seconds"] for o in meas if o["outcome"] == "ok"]
        row = {"query": qid, "suite": suite, "expected": expected,
               "got": next((o["count"] for o in obs if "count" in o), None),
               "outcomes": outcomes, "measured_ok": len(times), "measured": len(meas)}
        if times and outcomes == ["ok"]:
            row.update(median_s=statistics.median(times), min_s=min(times), max_s=max(times))
        summary.append(row)
    meta["summary"] = summary
    json.dump(meta, open(os.path.join(out_dir, "result.json"), "w"), indent=1)

    print(f"\n{'query':<26} {'status':<16} {'expected':>14} {'got':>14} {'median s':>10} {'min':>8} {'max':>8}")
    for s in summary:
        status = "pass" if s["outcomes"] == ["ok"] else "/".join(s["outcomes"])
        med = f"{s['median_s']:10.3f} {s['min_s']:8.3f} {s['max_s']:8.3f}" if "median_s" in s else ""
        print(f"{s['query']:<26} {status:<16} {s['expected']:>14} {str(s['got']):>14} {med}")
    print(f"\nload {meta['load_s']:.1f}s  GAV {meta['gav_s']}  peak container {meta['peak_container_mib']} MiB"
          f"\nresults: {out_dir}")


if __name__ == "__main__":
    main()

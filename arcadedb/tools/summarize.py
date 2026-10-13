#!/usr/bin/env python3
"""Summarize one native ArcadeDB lane run: load, outcomes, per-query measured medians.

    python3 arcadedb/tools/summarize.py benchmarks/lsqb/out/<run> [<baseline run>]

With a second run, each median is followed by the baseline's.
"""
import collections
import json
import statistics
import sys


def load(directory):
    report = json.load(open(directory + '/result/diagnostic.json'))
    by = collections.OrderedDict()
    for item in report['observations']:
        by.setdefault(item['id'], []).append(item)
    return report, by


def medians(by):
    return {q: statistics.median(o['elapsed_ns'] / 1e6 for o in items if o.get('phase') == 'measurement')
            for q, items in by.items()}


report, by = load(sys.argv[1])
base = medians(load(sys.argv[2])[1]) if len(sys.argv) > 2 else {}
outcomes = collections.Counter(o['outcome'] for o in report['observations'])
recovery = statistics.median(o['server_recovery']['server_recovery_ns'] / 1e6 for o in report['observations'])
print(f"scale {report['scale']}  server {report['server_version']}")
print(f"load {report['load_ns'] / 1e9:.1f}s (import {report['import_ns'] / 1e9:.1f}s, "
      f"view {report['analytical_view_ns'] / 1e9:.1f}s)  outcomes {dict(outcomes)}  "
      f"cancellation verified {all(o['server_recovery'].get('cancellation_verified') for o in report['observations'])}  "
      f"recovery median {recovery:.2f} ms")
for q, median in medians(by).items():
    ok = all(o['outcome'] == 'pass' for o in by[q])
    was = f"  (was {base[q]:.1f})" if q in base else ''
    print(f"  {q:<26} {'pass' if ok else 'FAIL'}  median {median:8.1f} ms{was}")

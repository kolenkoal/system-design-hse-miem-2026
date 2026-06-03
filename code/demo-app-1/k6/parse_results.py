#!/usr/bin/env python3
"""Parse k6 text output files and print a markdown comparison table."""

import re
import os

FILES = {
    'storm': 'k6/results/storm.txt',
    'wave':  'k6/results/wave.txt',
    'pulse': 'k6/results/pulse.txt',
}

METRIC_RE = re.compile(r'^\s+(?P<name>[\w_()]+)\.*:\s+(?P<values>.+)$')
NAMED_STAT_RE = re.compile(r'(avg|min|med|max|p\(\d+\))=([0-9.]+(?:µs|ms|s)?)')
RATE_RE = re.compile(r'([0-9.]+)\s*/\s*s')
PCT_RE  = re.compile(r'([0-9.]+(?:\.[0-9]+)?)%')


def to_ms(value: str) -> str:
    value = value.strip()
    if value.endswith('µs'):
        return f"{float(value[:-2]) / 1000:.2f}ms"
    if value.endswith('ms'):
        return value
    if value.endswith('s'):
        return f"{float(value[:-1]) * 1000:.0f}ms"
    return value


def parse_file(path: str) -> dict:
    if not os.path.exists(path):
        return {}

    results = {}
    with open(path) as f:
        for line in f:
            m = METRIC_RE.match(line)
            if not m:
                continue
            name = m.group('name').strip()
            vals = m.group('values').strip()

            stats = {}
            for stat_name, stat_val in NAMED_STAT_RE.findall(vals):
                stats[stat_name] = stat_val

            rps_m = RATE_RE.search(vals)
            if rps_m:
                stats['rps'] = rps_m.group(1)

            # For rate/percentage metrics (http_req_failed, error_rate):
            # format is "24.77% 3853 out of 15554"
            if not stats:
                pct_m = PCT_RE.search(vals)
                if pct_m:
                    stats['avg'] = pct_m.group(1) + '%'

            if stats:
                results[name] = stats

    return results


WANTED = [
    ('http_req_duration',    ['avg', 'p(90)', 'p(95)']),
    ('http_req_failed',      ['avg']),
    ('http_reqs',            ['rps']),
    ('error_rate',           ['avg']),
    ('create_order_latency', ['avg', 'p(90)', 'p(95)']),
    ('get_order_latency',    ['avg', 'p(90)', 'p(95)']),
]

scenarios = list(FILES.keys())
data = {s: parse_file(FILES[s]) for s in scenarios}

missing = [s for s in scenarios if not data[s]]
if missing:
    print(f"Warning: missing result files: {', '.join(FILES[s] for s in missing)}\n")

header_cols = ['Metric', 'Stat'] + scenarios
print('| ' + ' | '.join(header_cols) + ' |')
print('|' + '|'.join(['---'] * len(header_cols)) + '|')

for metric, stats in WANTED:
    for stat in stats:
        row = [metric, stat]
        for s in scenarios:
            val = data[s].get(metric, {}).get(stat, '—')
            # Normalize duration/latency to ms
            if val != '—' and (metric.endswith('_duration') or metric.endswith('_latency')):
                val = to_ms(val)
            row.append(val)
        print('| ' + ' | '.join(row) + ' |')

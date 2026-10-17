"""Summarize only synthetic benchmark measurements, never private runner logs."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics


def summarize(root):
    groups = defaultdict(list)
    errors = []
    count = 0
    for path in sorted(Path(root).rglob('measurements.jsonl')):
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row['kind'] not in ('transfer', 'read', 'tuning'):
                continue
            count += 1
            if not row.get('valid'):
                errors.append({'file': str(path), **row})
                continue
            key = tuple(row.get(k) for k in ('engine', 'kind', 'shape', 'lane', 'driver', 'batch', 'workers', 'profile', 'bcp_batch'))
            groups[key].append(row)
    print('BENCH_COUNTS ' + json.dumps({'total': count, 'valid': count-len(errors), 'failed': len(errors)}))
    for key, rows in sorted(groups.items(), key=lambda pair: str(pair[0])):
        result = dict(zip(('engine', 'kind', 'shape', 'lane', 'driver', 'batch', 'workers', 'profile', 'bcp_batch'), key))
        result.update(n=len(rows), rows=rows[0].get('rows'), median_rows_s=round(statistics.median(r['rows_s'] for r in rows)),
                      min_s=round(min(r['elapsed_s'] for r in rows), 3), max_s=round(max(r['elapsed_s'] for r in rows), 3),
                      median_s=round(statistics.median(r['elapsed_s'] for r in rows), 3))
        for metric in ('fetch_s', 'load_s', 'spool_bytes', 'client_peak_rss_bytes'):
            values = [r[metric] for r in rows if metric in r]
            if values:
                result[metric] = round(statistics.median(values), 3)
        print('BENCH_SUMMARY ' + json.dumps(result))
    for error in errors:
        print('BENCH_FAILURE ' + json.dumps(error))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root')
    summarize(parser.parse_args().root)

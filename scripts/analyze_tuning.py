"""Produce portable numerical summaries from archived synthetic trial records."""
import argparse
import csv
import json
from pathlib import Path
import statistics

KEYS = ('engine', 'profile', 'shape', 'lane', 'batch', 'bcp_batch', 'workers', 'java_xmx')


def counters(text):
    result = {}
    current = None
    for line in text.splitlines():
        if line.startswith('COUNTER:'):
            current = line.removeprefix('COUNTER:')
        elif current and line:
            result[current] = result.get(current, '')+line+'\n'
    return result


def cpu_seconds(text):
    stats = dict(line.split() for line in counters(text).get('cpu.stat', '').splitlines())
    return int(stats['usage_usec'])/1e6 if 'usage_usec' in stats else None


def io_bytes(text, name):
    lines = counters(text).get('io.stat', '').splitlines()
    if not lines:
        return None
    return sum(int(field.split('=')[1]) for line in lines for field in line.split()[1:] if field.startswith(name+'='))


def analyze(directory):
    summaries = []
    for path in sorted(Path(directory).glob('tuning-*.jsonl')):
        groups = {}
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row['kind'] == 'tuning':
                key = tuple(row[k] for k in KEYS)
                groups.setdefault(key, []).append(row)
        for key, rows in groups.items():
            result = dict(dataset=path.stem, **dict(zip(KEYS, key)))
            result.update(n=len(rows), valid=sum(r.get('valid', False) for r in rows))
            eligible = len(rows) == 3 and {r['rep'] for r in rows} == {0, 1, 2} and all(r.get('valid') for r in rows)
            applicable = not (result['profile'] == 'java-heap' and not result['lane'].startswith('jdbc'))
            result['setting_applicable'] = applicable
            eligible = eligible and applicable
            result['rankable'] = eligible
            if eligible:
                result.update(rows=rows[0]['rows'], median_rows_s=round(statistics.median(r['rows_s'] for r in rows)),
                              median_s=round(statistics.median(r['elapsed_s'] for r in rows), 4),
                              min_s=round(min(r['elapsed_s'] for r in rows), 4),
                              max_s=round(max(r['elapsed_s'] for r in rows), 4),
                              client_peak_mib=round(max(r['client_peak_rss_bytes'] for r in rows)/2**20, 1),
                              spool_mib=round(max(r.get('spool_bytes', 0) for r in rows)/2**20, 1))
                for server in ('source', 'target'):
                    cpu = []
                    reads = []
                    for row in rows:
                        before = cpu_seconds(row.get('resource_before', {}).get(server, ''))
                        after = cpu_seconds(row.get('resource_after', {}).get(server, ''))
                        if before is not None and after is not None:
                            cpu.append(after-before)
                        before_io = io_bytes(row.get('resource_before', {}).get(server, ''), 'rbytes')
                        after_io = io_bytes(row.get('resource_after', {}).get(server, ''), 'rbytes')
                        if before_io is not None and after_io is not None:
                            reads.append((after_io-before_io)/2**20)
                    result[server+'_median_cpu_s'] = round(statistics.median(cpu), 3) if cpu else None
                    result[server+'_median_read_mib'] = round(statistics.median(reads), 3) if reads else None
            else:
                result['errors'] = [r.get('error', 'missing or repeated trial') for r in rows if not r.get('valid')]
                if not applicable:
                    result['errors'].append('This lane launches no JVM; Java heap is not an experimental variable')
            summaries.append(result)
    return summaries


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    summaries = analyze(args.directory)
    output = Path(args.output)
    output.parent.mkdir(exist_ok=True, parents=True)
    output.with_suffix('.json').write_text(json.dumps(summaries, indent=2)+'\n')
    fields = list(dict.fromkeys(key for row in summaries for key in row))
    with output.with_suffix('.csv').open('w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summaries)
    print(json.dumps({'groups': len(summaries), 'rankable': sum(r['rankable'] for r in summaries)}))

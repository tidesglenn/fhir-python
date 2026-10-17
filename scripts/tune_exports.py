"""Disposable synthetic export tuning. Every ranked trial checks every value."""
import concurrent.futures
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import random
import re
import signal
import statistics
import subprocess
import sys
import time

import psutil
import transfer_bench as b

REPEATS = int(os.environ.get('BENCH_REPEATS', '3'))
MODE = os.environ.get('TUNING_MODE', 'memory')
KEYS = ('globals8kb', 'routines', 'gmheap', 'bbsiz')
BASE = dict(globals8kb=256, routines=64, gmheap=65536 if b.ENGINE == 'cache' else 307200, bbsiz=262144)
DATA = []


def emit(row):
    row['engine'] = b.ENGINE
    DATA.append(row)
    b.record(row)
    print('TUNING_RECORD ' + json.dumps(row, default=str), flush=True)


def sys_session(code):
    args = ['docker', 'exec', '-i', 'source']
    args += ['csession', 'cache', '-U', '%SYS'] if b.ENGINE == 'cache' else ['iris', 'session', 'iris', '-U', '%SYS']
    result = b.command(args, input=(code + '\nhalt\n').encode())
    text = result.stdout.decode(errors='replace')
    (b.OUT / 'memory-session.log').open('a').write(text)
    return text


def config():
    code = 'set sc=##class(Config.config).Get(.p) write "GET_OK|",$system.Status.IsOK(sc),!\n'
    code += '\n'.join(f'write "CFG|{key}|",p("{key}"),!' for key in KEYS)
    code += '\nwrite "HEAP|",$system.Config.SharedMemoryHeap.GetUsageSummary(),!'
    text = sys_session(code)
    assert 'GET_OK|1' in text, text
    values = {k: int(v) for k, v in re.findall(r'CFG\|(\w+)\|(-?\d+)', text)}
    assert set(values) == set(KEYS), text
    heap = re.search(r'HEAP\|([\d,]+)', text)
    assert heap, text
    values['heap_used_allocated_configured_bytes'] = [int(v) for v in heap[1].split(',')]
    return values


def apply_profile(name, values):
    # This exact container is created with a disposable label by the workflow.
    label = b.command(['docker', 'inspect', 'source', '--format', '{{index .Config.Labels "synthetic-export-bench"}}']).stdout.decode().strip()
    assert label == '1', 'Refusing configuration changes without disposable label'
    assignments = ','.join(f'p("{key}")={int(values[key])}' for key in KEYS)
    text = sys_session(f'set {assignments}\nset sc=##class(Config.config).Modify(.p) write "MODIFY_OK|",$system.Status.IsOK(sc),! if $system.Status.IsError(sc) do $system.Status.DisplayError(sc)')
    assert 'MODIFY_OK|1' in text, text
    previous = b.command(['docker', 'inspect', 'source', '--format', '{{.State.StartedAt}}']).stdout.decode().strip()
    subprocess.run(['docker', 'restart', '-t', '60', 'source'], check=True, capture_output=True, timeout=120)
    deadline = time.monotonic()+90
    while True:
        try:
            actual = config()
            with b.source() as c:
                assert c.cursor().execute('SELECT 1').fetchone()[0] == 1
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    assert all(actual[key] == values[key] for key in KEYS), (values, actual)
    started = b.command(['docker', 'inspect', 'source', '--format', '{{.State.StartedAt}}']).stdout.decode().strip()
    assert started != previous, 'Engine container did not restart'
    # GetUsageSummary includes runtime reservations; do not equate it to CPF gmheap.
    assert actual['heap_used_allocated_configured_bytes'][2] >= values['gmheap']*1024
    logs = b.command(['docker', 'logs', '--since', started, 'source'])
    (b.OUT / f'startup-{name}.log').write_bytes(logs.stdout+logs.stderr)
    emit(dict(kind='profile', profile=name, requested=values, actual=actual, started_at=started))


def resources(container):
    # Engine and SQL Server cgroup counters, outside transfer timing. A missing
    # counter is explicit; it does not become a fabricated zero.
    result = b.command(['docker', 'exec', container, 'sh', '-c',
                        'for f in memory.current memory.peak memory.events cpu.stat io.stat; do '
                        'echo "COUNTER:$f"; cat "/sys/fs/cgroup/$f" 2>/dev/null || true; done'])
    return result.stdout.decode(errors='replace')


def drop_target(table):
    with b.target() as c:
        c.execute(f'DROP TABLE IF EXISTS dbo.[{table}]')
        c.commit()


def lane_transfer(shape, lane, batch, table, lower=0, upper=b.N, suffix=''):
    if lane == 'jdbc-bulk':
        start = time.perf_counter()
        args = b.java_args()
        args[args.index('scripts:' + os.environ['JDBC_JAR'])] += ':' + os.environ['MSSQL_JDBC_JAR']
        args[-1] = 'JdbcBulkTransfer'
        b.command(args+[b.ENGINE, b.query(shape, lower, upper), table, str(batch)])
        return dict(rows=upper-lower, elapsed_s=time.perf_counter()-start, spool_bytes=0)
    return b.transfer(shape, lane, batch, table, lower, upper, suffix)


def child_trial(spec):
    if spec['workers'] == 1:
        result = lane_transfer(spec['shape'], spec['lane'], spec['batch'], spec['table'])
    else:
        start = time.perf_counter()
        count = spec['workers']
        with concurrent.futures.ProcessPoolExecutor(max_workers=count, mp_context=multiprocessing.get_context('spawn')) as pool:
            futures = [pool.submit(lane_transfer, spec['shape'], spec['lane'], spec['batch'], spec['table'],
                                   b.N*i//count, b.N*(i+1)//count, str(i)) for i in range(count)]
            outcomes = [f.result() for f in futures]
        result = dict(rows=sum(r['rows'] for r in outcomes), elapsed_s=time.perf_counter()-start,
                      spool_bytes=sum(r.get('spool_bytes', 0) for r in outcomes))
    Path(spec['result']).write_text(json.dumps(result))


def trial(shape, lane, profile, rep, batch=10000, workers=1, bcp_batch=10000, packet=4096, java_buffer=8192, java_xmx='256m'):
    table = 'TuneTrial'
    record = dict(kind='tuning', shape=shape, lane=lane, profile=profile, rep=rep, batch=batch,
                  workers=workers, bcp_batch=bcp_batch, packet=packet, java_buffer=java_buffer, java_xmx=java_xmx)
    result_file = b.OUT / 'trial-result.json'
    result_file.unlink(missing_ok=True)
    env = dict(os.environ, BENCH_BCP_BATCH=str(bcp_batch), BENCH_BCP_PACKET=str(packet),
               BENCH_JAVA_BUFFER=str(java_buffer), BENCH_JAVA_XMX=java_xmx)
    spec = dict(shape=shape, lane=lane, batch=batch, workers=workers, table=table, result=str(result_file))
    before = {name: resources(name) for name in ('source', 'target')}
    proc = None
    try:
        b.create_target(table)
        with (b.OUT / 'trial.log').open('w') as log:
            proc = subprocess.Popen([sys.executable, __file__, '--trial', json.dumps(spec)], env=env,
                                    stdout=log, stderr=log, start_new_session=True)
            deadline = time.monotonic()+210
            peak = 0
            root = psutil.Process(proc.pid)
            while proc.poll() is None:
                rss = 0
                try:
                    processes = [root]+root.children(recursive=True)
                except psutil.Error:
                    processes = []
                for process in processes:
                    try:
                        rss += process.memory_info().rss
                    except psutil.Error:
                        pass
                peak = max(peak, rss)
                if time.monotonic() >= deadline:
                    raise TimeoutError('Transfer exceeded 210 seconds')
                time.sleep(.05)
        if proc.returncode:
            raise RuntimeError((b.OUT / 'trial.log').read_text()[-4000:])
        outcome = json.loads(result_file.read_text())
        after = {name: resources(name) for name in ('source', 'target')}
        begin_verify = time.perf_counter()
        value_hash = b.verify(table, WANTED[shape])
        assert outcome['rows'] == b.N
        record.update(outcome, valid=True, rows_s=b.N/outcome['elapsed_s'], sha256=value_hash,
                      validation_s=time.perf_counter()-begin_verify, client_peak_rss_bytes=peak,
                      resource_before=before, resource_after=after)
    except Exception as exc:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        record.update(valid=False, error=str(exc))
    finally:
        drop_target(table)
    emit(record)
    return record


def first_read(profile, shape):
    h = hashlib.sha256()
    count = 0
    elapsed = 0
    with b.source() as c:
        cur = c.cursor()
        start = time.perf_counter()
        cur.execute(b.query(shape))
        elapsed += time.perf_counter()-start
        while True:
            start = time.perf_counter()
            rows = cur.fetchmany(10000)
            elapsed += time.perf_counter()-start
            if not rows:
                break
            count += len(rows)
            b.digest_rows(rows, h)
    assert count == b.N and h.hexdigest() == WANTED[shape]
    emit(dict(kind='read', shape=shape, profile=profile, driver='odbc', batch=10000,
              cache_state='first-source-scan-after-restart; OS page cache retained', rows=count,
              elapsed_s=elapsed, rows_s=count/elapsed, valid=True, sha256=h.hexdigest()))


def repeats(jobs):
    rng = random.Random(261017)
    all_jobs = [(job, rep) for job in jobs for rep in range(REPEATS)]
    rng.shuffle(all_jobs)
    return [trial(rep=rep, **job) for job, rep in all_jobs]


def best_lane(shape, rows):
    groups = {}
    for row in rows:
        if row.get('valid') and row['shape'] == shape and row['workers'] == 1:
            groups.setdefault(row['lane'], []).append(row['elapsed_s'])
    eligible = [(statistics.median(v), lane) for lane, v in groups.items() if len(v) == REPEATS]
    if not eligible:
        raise AssertionError('No completely validated candidate lane')
    return min(eligible)[1]


def main():
    global WANTED
    original = {k: v for k, v in config().items() if k in KEYS}
    emit(dict(kind='original_config', values=original))
    b.bootstrap()
    WANTED = {shape: b.expected(shape) for shape in ('Narrow', 'Wide')}
    try:
        apply_profile('baseline', BASE)
        for shape in WANTED:
            first_read('baseline', shape)
        lanes = ['jdbc-bulk', 'jdbc-bcp', 'server-bcp'] if MODE == 'memory' else ['jdbc-bulk', 'jdbc-bcp', 'odbc-bcp', 'odbc-direct']
        if MODE == 'confirm':
            lanes = ['jdbc-bulk', 'jdbc-bcp', 'odbc-bcp']
        if MODE == 'export' and b.ENGINE == 'iris':
            lanes += ['native-bcp']
        baseline = repeats([dict(shape=s, lane=l, profile='baseline') for s in WANTED for l in lanes])
        winners = {s: best_lane(s, baseline) for s in WANTED}
        emit(dict(kind='selection', lanes=winners))
        if MODE == 'confirm':
            # Repeated baseline/global pairs on one runner distinguish memory
            # effects from the substantial cross-runner timing differences.
            for name, values in [('globals-confirm-1', dict(BASE, globals8kb=1024)),
                                 ('baseline-confirm-1', BASE),
                                 ('globals-confirm-2', dict(BASE, globals8kb=1024)),
                                 ('baseline-confirm-2', BASE)]:
                apply_profile(name, values)
                for shape in WANTED:
                    first_read(name, shape)
                repeats([dict(shape=s, lane=winners[s], profile=name) for s in WANTED])
                emit(dict(kind='profile_end', profile=name, actual=config()))
            # A combined setting is measured, not inferred from independent
            # improvements in fetch, bulk-commit size and worker count.
            apply_profile('combined-globals', dict(BASE, globals8kb=1024))
            for shape in WANTED:
                first_read('combined-globals', shape)
            repeats([dict(shape=s, lane=winners[s], profile='combined', batch=10000,
                          bcp_batch=50000, workers=workers) for s in WANTED for workers in (1, 2)])
        elif MODE == 'memory':
            profiles = [('heap', dict(BASE, gmheap=BASE['gmheap']*2)),
                        ('globals', dict(BASE, globals8kb=1024)),
                        ('routines', dict(BASE, routines=128)),
                        ('process', dict(BASE, bbsiz=1048576)),
                        ('baseline-return', BASE)]
            for name, values in profiles:
                apply_profile(name, values)
                for shape in WANTED:
                    first_read(name, shape)
                repeats([dict(shape=s, lane=winners[s], profile=name) for s in WANTED])
        else:
            jobs = []
            for shape, lane in winners.items():
                jobs += [dict(shape=shape, lane=lane, profile='fetch', batch=n) for n in (1000, 50000)]
                jobs += [dict(shape=shape, lane=lane, profile='commit', bcp_batch=n) for n in (1000, 50000)]
                jobs += [dict(shape=shape, lane=lane, profile='workers', workers=n) for n in (2, 4)]
                jobs += [dict(shape=shape, lane=lane, profile='java-heap', java_xmx='64m')]
            repeats(jobs)
        rows = [r for r in DATA if r['kind'] == 'tuning']
        invalid = [r for r in rows if not r.get('valid')]
        emit(dict(kind='completion', mode=MODE, measured=len(rows), valid=len(rows)-len(invalid), failed=len(invalid)))
        # Fail the job even if a different lane works; a green job cannot hide failed experiments.
        assert not invalid, f'{len(invalid)} tuning trials failed; see measurements'
    finally:
        apply_profile('restore-original', original)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--trial':
        child_trial(json.loads(sys.argv[2]))
    else:
        main()

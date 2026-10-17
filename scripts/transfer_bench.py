"""Synthetic disposable-database transfer benchmark; no application dependencies."""
import concurrent.futures
import contextlib
import decimal
import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time

import pyodbc

ENGINE = os.environ['SOURCE_ENGINE']
N = int(os.environ.get('BENCH_ROWS', '100000'))
PHASE = os.environ.get('BENCH_PHASE', 'sweep')
CONFIRM = PHASE != 'sweep'
OUT = Path(os.environ.get('RESULT_DIR', 'results'))
OUT.mkdir(exist_ok=True, parents=True)
RECORDS = OUT / 'measurements.jsonl'
SQLPW = os.environ['BENCH_SQL_PASSWORD']
BCP = '/opt/mssql-tools18/bin/bcp'
BCP_BATCH = os.environ.get('BENCH_BCP_BATCH', '10000')
BCP_PACKET = os.environ.get('BENCH_BCP_PACKET', '4096')
pyodbc.pooling = False


def record(item):
    item['engine'] = ENGINE
    with RECORDS.open('a') as f:
        f.write(json.dumps(item, default=str) + '\n')
    print('measurement recorded', flush=True)


def command(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, timeout=180, **kwargs)


def session(code):
    args = ['docker', 'exec', '-i', 'source']
    args += ['csession', 'cache', '-U', 'USER'] if ENGINE == 'cache' else ['iris', 'session', 'iris', '-U', 'USER']
    result = command(args, input=(code + '\nhalt\n').encode())
    (OUT / 'server-session.log').open('ab').write(result.stdout + result.stderr)
    return result.stdout.decode(errors='replace')


def source(driver='odbc'):
    if driver == 'native':
        import iris.dbapi
        return iris.dbapi.connect('127.0.0.1', 1972, 'USER', '_SYSTEM', 'SYS')
    c = pyodbc.connect('DRIVER={BenchSource};SERVER=127.0.0.1;PORT=1972;DATABASE=USER;UID=_SYSTEM;PWD=SYS', autocommit=True, timeout=10)
    if ENGINE == 'cache':
        c.setdecoding(pyodbc.SQL_CHAR, encoding='utf-8')
        c.setdecoding(pyodbc.SQL_WCHAR, encoding='utf-8')
        c.setencoding(encoding='utf-8')
    c.timeout = 90
    return c


def target():
    c = pyodbc.connect('DRIVER={ODBC Driver 18 for SQL Server};SERVER=127.0.0.1,1433;UID=sa;PWD=' + SQLPW + ';Encrypt=yes;TrustServerCertificate=yes;DATABASE=TransferBench', timeout=10)
    c.timeout = 90
    return c


def java_args():
    return ['java', '-Xms32m', '-Xmx' + os.environ.get('BENCH_JAVA_XMX', '256m'),
            '-cp', 'scripts:' + os.environ['JDBC_JAR'], 'TransferExtract']


def query(shape, lower=0, upper=N):
    return f'SELECT ID,Label,Qty,Payload,CAST(Amount AS VARCHAR(32)) AS Amount FROM Bench.{shape} WHERE ID>{lower} AND ID<={upper} ORDER BY ID'


def canonical(row):
    return [int(row[0]), row[1], int(row[2]), row[3], format(decimal.Decimal(str(row[4])).quantize(decimal.Decimal('.01')), 'f')]


def digest_rows(rows, h):
    for row in rows:
        h.update((json.dumps(canonical(row), ensure_ascii=False, separators=(',', ':')) + '\n').encode())


def expected(shape):
    h = hashlib.sha256()
    width = 32 if shape == 'Narrow' else 1024
    for i in range(1, N+1):
        digest_rows([(i, 'row-é中-' + str(i), i % 10000, None if i % 17 == 0 else ('x' * width), decimal.Decimal(i) / 100)], h)
    return h.hexdigest()


def verify(table, wanted):
    c = target()
    h = hashlib.sha256()
    count = 0
    cur = c.cursor().execute(f'SELECT rid,label,qty,payload,amount FROM dbo.[{table}] ORDER BY rid')
    while True:
        rows = cur.fetchmany(10000)
        if not rows: break
        count += len(rows)
        digest_rows(rows, h)
    c.close()
    if count != N or h.hexdigest() != wanted:
        raise AssertionError(f'validation mismatch: rows={count}; hash={h.hexdigest()}; expected={wanted}')
    return h.hexdigest()


def create_target(table):
    c = target()
    c.execute(f'DROP TABLE IF EXISTS dbo.[{table}]')
    c.execute(f'CREATE TABLE dbo.[{table}] (rid int NOT NULL, label nvarchar(100) NULL, qty int NOT NULL, payload nvarchar(2000) NULL, amount decimal(14,2) NOT NULL)')
    c.commit()
    c.close()


def bcp(table, path, unicode=False):
    args = [BCP, f'TransferBench.dbo.{table}', 'in', str(path), '-S', '127.0.0.1,1433', '-U', 'sa', '-P', SQLPW,
            '-w' if unicode else '-c', '-t', '\t', '-r', '\n', '-b', BCP_BATCH, '-a', BCP_PACKET, '-h', 'TABLOCK', '-u', '-e', str(OUT / 'bcp-errors.txt')]
    p = command(args)
    (OUT / 'bcp.log').open('ab').write(p.stdout+p.stderr)


def transfer(shape, lane, batch, table, lower=0, upper=N, suffix=''):
    with contextlib.ExitStack() as cleanup:
        return _transfer(shape, lane, batch, table, lower, upper, suffix, cleanup)


def _transfer(shape, lane, batch, table, lower, upper, suffix, cleanup):
    driver = 'native' if lane.startswith('native') else 'odbc'
    t0 = time.perf_counter()
    fetched = 0
    fetch_s = 0.0
    load_s = 0.0
    if lane in ('odbc-stream', 'jdbc-stream'):
        log = (OUT / 'stream.log').open('ab')
        cleanup.callback(log.close)
        producer = None
        if lane == 'jdbc-stream':
            producer = subprocess.Popen(java_args() + [ENGINE, query(shape, lower, upper), '-', str(batch)], stdout=subprocess.PIPE, stderr=log)
        proc = subprocess.Popen([BCP, f'TransferBench.dbo.{table}', 'in', '/dev/stdin', '-S', '127.0.0.1,1433', '-U', 'sa', '-P', SQLPW, '-w', '-t', '\t', '-r', '\n', '-b', BCP_BATCH, '-a', BCP_PACKET, '-h', 'TABLOCK', '-u'], stdin=producer.stdout if producer else subprocess.PIPE, stdout=log, stderr=log)
        try:
            if producer:
                producer.stdout.close()
                if producer.wait(timeout=180) != 0: raise RuntimeError('JDBC stream producer failed')
                fetched = upper-lower
            else:
                c = source(); cleanup.callback(c.close)
                cur = c.cursor(); cur.arraysize=batch; cur.execute(query(shape, lower, upper))
                with io.TextIOWrapper(proc.stdin, encoding='utf-16le', newline='') as pipe:
                    while True:
                        rows=cur.fetchmany(batch)
                        if not rows: break
                        fetched += len(rows)
                        pipe.writelines('\t'.join('' if v is None else str(v) for v in row)+'\n' for row in rows)
            if proc.wait(timeout=180) != 0: raise RuntimeError('BCP stream consumer failed')
        finally:
            for child in (producer,proc):
                if child is not None and child.poll() is None:
                    child.kill(); child.wait()
        return dict(rows=fetched,elapsed_s=time.perf_counter()-t0,spool_bytes=0)
    if lane == 'jdbc-bcp':
        path = OUT / f'{table}-{suffix}.tsv'
        command(java_args() + [ENGINE, query(shape, lower, upper), str(path), str(batch)])
        fetch_s = time.perf_counter()-t0
        size = path.stat().st_size
        t = time.perf_counter()
        bcp(table, path, unicode=True)
        load_s = time.perf_counter()-t
        path.unlink()
        return dict(rows=upper-lower, elapsed_s=time.perf_counter()-t0, fetch_s=fetch_s, load_s=load_s, spool_bytes=size)
    if lane == 'server-bcp':
        # Query semantics are preserved: no direct global scan is timed.
        code = f'''set s=##class(%Stream.FileCharacter).%New(),s.Filename="/tmp/bench-{shape}.tsv",s.TranslateTable="UTF8"
set q=##class(%SQL.Statement).%New(),sc=q.%Prepare("{query(shape, lower, upper)}")
set r=q.%Execute() while r.%Next() {{ do s.WriteLine(r.%Get("ID")_$char(9)_r.%Get("Label")_$char(9)_r.%Get("Qty")_$char(9)_r.%Get("Payload")_$char(9)_r.%Get("Amount")) }}
do s.%Save() write "EXPORT DONE",!
'''
        session(code)
        path = OUT / f'{table}-{suffix}.tsv'
        command(['docker', 'cp', f'source:/tmp/bench-{shape}.tsv', str(path)])
        fetch_s = time.perf_counter()-t0
        t = time.perf_counter()
        bcp(table, path)
        load_s = time.perf_counter()-t
        size = path.stat().st_size
        path.unlink()
        return dict(rows=upper-lower, elapsed_s=time.perf_counter()-t0, fetch_s=fetch_s, load_s=load_s, spool_bytes=size)
    c = source(driver)
    cleanup.callback(c.close)
    cur = c.cursor()
    cur.arraysize = batch
    cur.execute(query(shape, lower, upper))
    dest = target() if lane.endswith('direct') else None
    if dest: cleanup.callback(dest.close)
    dc = dest.cursor() if dest else None
    if dc:
        dc.fast_executemany = True
        dc.setinputsizes([(pyodbc.SQL_INTEGER,0,0),(pyodbc.SQL_WVARCHAR,100,0),(pyodbc.SQL_INTEGER,0,0),(pyodbc.SQL_WVARCHAR,2000,0),(pyodbc.SQL_DECIMAL,14,2)])
    path = OUT / f'{table}-{suffix}.tsv'
    f = path.open('w', encoding='utf-16le', newline='') if lane.endswith('bcp') else None
    if f: cleanup.callback(f.close)
    while True:
        t = time.perf_counter()
        rows = cur.fetchmany(batch)
        fetch_s += time.perf_counter()-t
        if not rows: break
        fetched += len(rows)
        if dc:
            t = time.perf_counter()
            dc.executemany(f'INSERT INTO dbo.[{table}] VALUES (?,?,?,?,?)', [tuple(row[:4]) + (decimal.Decimal(str(row[4])),) for row in rows])
            dest.commit()
            load_s += time.perf_counter()-t
        elif f:
            f.writelines('\t'.join('' if v is None else str(v) for v in row)+'\n' for row in rows)
    cur.close()
    # Connections and files close through the cleanup stack on success or failure.
    size = 0
    if f:
        f.close()
        size = path.stat().st_size
        t = time.perf_counter()
        bcp(table, path, unicode=True)
        load_s += time.perf_counter()-t
        path.unlink()
    return dict(rows=fetched, elapsed_s=time.perf_counter()-t0, fetch_s=fetch_s, load_s=load_s, spool_bytes=size)


def bootstrap():
    # Only the source container and a database created in this disposable job are mutated.
    for shape in ['Narrow', 'Wide']:
        definition = f'''Class Bench.{shape} Extends %Persistent {{
Property Label As %String(MAXLEN = 100);
Property Qty As %Integer;
Property Payload As %String(MAXLEN = 2000);
Property Amount As %Numeric(SCALE = 2);
}}'''
        p = OUT / f'{shape}.cls'
        p.write_text(definition)
        command(['docker', 'cp', str(p), f'source:/tmp/{shape}.cls'])
        width = 32 if shape == 'Narrow' else 1024
        text = session(f'''set sc=$system.OBJ.Load("/tmp/{shape}.cls","ck") write "LOAD ",$system.Status.IsOK(sc),!
kill ^Bench.{shape}D,^Bench.{shape}I
for i=1:1:{N} {{ set ^Bench.{shape}D(i)=$lb("","row-"_$char(233)_$char(20013)_"-"_i,i#10000,$select(i#17=0:"",1:$translate($justify("",{width})," ","x")),i/100) }}
set ^Bench.{shape}D={N}
write "SEEDED ",^Bench.{shape}D,!
''')
        if f'SEEDED {N}' not in text: raise RuntimeError('source seed failed')
    c = pyodbc.connect('DRIVER={ODBC Driver 18 for SQL Server};SERVER=127.0.0.1,1433;UID=sa;PWD='+SQLPW+';Encrypt=yes;TrustServerCertificate=yes', autocommit=True)
    c.execute('CREATE DATABASE TransferBench')
    c.close()
    record({'kind':'environment', 'rows_per_shape':N, 'python':sys.version, 'pyodbc':pyodbc.version,
            'cpus':os.cpu_count(), 'source_version':session('write $zv,!'),
            'sql_version':str(target().execute('SELECT @@VERSION').fetchone()[0])})


def main():
    bootstrap()
    wants = {shape:expected(shape) for shape in ['Narrow','Wide']}
    # Full value checks establish that a quick source read isn't just decoding blanks.
    for shape in ([] if CONFIRM else wants):
        for driver in (['odbc','native'] if ENGINE == 'iris' else ['odbc']):
            for batch in [1000,10000,50000]:
                for rep in range(3):
                    c = None
                    try:
                        c = source(driver); cur = c.cursor(); cur.arraysize=batch
                        t=time.perf_counter(); cur.execute(query(shape)); count=0; chunks=[]
                        # Timing excludes hashing; capped fixture size bounds this diagnostic list.
                        while True:
                            rows=cur.fetchmany(batch)
                            if not rows: break
                            count+=len(rows); chunks.append(rows)
                        elapsed=time.perf_counter()-t
                        h=hashlib.sha256()
                        for rows in chunks: digest_rows(rows,h)
                        assert count==N and h.hexdigest()==wants[shape], 'source content mismatch'
                        record(dict(kind='read',shape=shape,driver=driver,batch=batch,rep=rep,rows=count,elapsed_s=elapsed,rows_s=count/elapsed,valid=True))
                    except Exception as e:
                        record(dict(kind='read',shape=shape,driver=driver,batch=batch,rep=rep,valid=False,error=str(e)))
                    finally:
                        if c is not None: c.close()
    lanes=[('odbc-direct',1000),('odbc-direct',10000),('odbc-direct',50000),('odbc-bcp',10000),('server-bcp',10000),('jdbc-bcp',10000)]
    if ENGINE=='iris' and not CONFIRM: lanes += [('native-direct',10000),('native-bcp',10000)]
    if CONFIRM: lanes=[('odbc-direct',10000),('odbc-bcp',10000),('server-bcp',10000),('jdbc-bcp',10000)]
    if PHASE=='stream': lanes=[('jdbc-bcp',10000),('odbc-bcp',10000),('odbc-stream',10000),('jdbc-stream',10000)]
    jobs=[(s,l,b,r) for s in wants for l,b in lanes for r in range(3)]
    random.Random(6017).shuffle(jobs)
    for index,(shape,lane,batch,rep) in enumerate(jobs):
        table=f'T{index}'
        try:
            create_target(table)
            result=transfer(shape,lane,batch,table)
            value_hash=verify(table,wants[shape])
            record(dict(kind='transfer',shape=shape,lane=lane,batch=batch,rep=rep,workers=1,valid=True,sha256=value_hash,rows_s=N/result['elapsed_s'],**result))
        except Exception as e:
            record(dict(kind='transfer',shape=shape,lane=lane,batch=batch,rep=rep,workers=1,valid=False,error=str(e)))
        finally:
            c=target(); c.execute(f'DROP TABLE IF EXISTS dbo.[{table}]'); c.commit(); c.close()
    # Re-test the fastest fully validated direct configuration with disjoint key ranges.
    data=[json.loads(x) for x in RECORDS.read_text().splitlines()]
    for shape in wants:
        groups={}
        for x in data:
            if x.get('kind')=='transfer' and x.get('valid') and x['shape']==shape and x['lane'].endswith('direct'):
                groups.setdefault((x['lane'],x['batch']),[]).append(x['elapsed_s'])
        eligible=[(statistics.median(v),k) for k,v in groups.items() if len(v)==3]
        if not eligible: continue
        _,(lane,batch)=min(eligible)
        for workers in [2,4]:
            for rep in range(3):
                table='Parallel'
                try:
                    create_target(table); t=time.perf_counter()
                    executor = concurrent.futures.ProcessPoolExecutor if CONFIRM else concurrent.futures.ThreadPoolExecutor
                    options = {'mp_context':multiprocessing.get_context('spawn')} if CONFIRM else {}
                    with executor(max_workers=workers, **options) as pool:
                        futures=[pool.submit(transfer,shape,lane,batch,table,N*i//workers,N*(i+1)//workers,str(i)) for i in range(workers)]
                        outcomes=[f.result() for f in futures]
                    elapsed=time.perf_counter()-t
                    value_hash=verify(table,wants[shape])
                    record(dict(kind='transfer',shape=shape,lane=lane,batch=batch,rep=rep,workers=workers,worker_mode='process' if CONFIRM else 'thread',rows=N,elapsed_s=elapsed,rows_s=N/elapsed,valid=True,sha256=value_hash))
                except Exception as e:
                    record(dict(kind='transfer',shape=shape,lane=lane,batch=batch,rep=rep,workers=workers,worker_mode='process' if CONFIRM else 'thread',valid=False,error=str(e)))
                finally:
                    c=target(); c.execute('DROP TABLE IF EXISTS dbo.Parallel'); c.commit(); c.close()

if __name__=='__main__': main()

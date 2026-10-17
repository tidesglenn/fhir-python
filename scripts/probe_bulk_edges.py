"""Scalar correctness gates, including values that cannot use plain TSV."""
import decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pyodbc
import transfer_bench as b

D = decimal.Decimal
ROWS = [
    (1, 'é中\tline\nnext\r"quote"', 7, 'x'*4000, D('.0001')),
    (2, '', 0, None, None),
    (3, None, -7, '', D('-99999999999999.9999')),
    (4, '界', -2147483648, 'tabs\tand\nlines\r"quotes"', D('99999999999999.9999')),
    (5, 'zero', 2147483647, None, D('0.0000')),
    (6, 'negative', 1, 'é中', D('-1234.5678')),
]
QUERY = 'SELECT ID,Label,Qty,Payload,Amount FROM Bench.BulkEdges ORDER BY ID'


def digest(rows):
    h = hashlib.sha256()
    for row in rows:
        values = list(row)
        values[4] = None if row[4] is None else format(D(str(row[4])).quantize(D('.0001')), 'f')
        h.update((json.dumps(values, ensure_ascii=False, separators=(',', ':'))+'\n').encode())
    return h.hexdigest()


def seed():
    path = b.OUT/'BulkEdges.cls'
    path.write_text('''Class Bench.BulkEdges Extends %Persistent {
Property Label As %String(MAXLEN=1000);
Property Qty As %Integer;
Property Payload As %String(MAXLEN=4000);
Property Amount As %Numeric(SCALE=4);
}''')
    b.command(['docker','cp',str(path),'source:/tmp/BulkEdges.cls'])
    code = 'set sc=$system.OBJ.Load("/tmp/BulkEdges.cls","ck") write "LOAD|",$system.Status.IsOK(sc),!\nkill ^Bench.BulkEdgesD,^Bench.BulkEdgesI\n'
    # SQL represents an empty string as $char(0), separately from NULL ("").
    # All input is generated here; convert Unicode into ObjectScript $char()
    # expressions so source files do not depend on terminal encoding.
    def scalar(value):
        if value is None:
            return '""'
        if isinstance(value, str):
            if value == '':
                return '$char(0)'
            if value == 'x'*4000:
                return '$translate($justify("",4000)," ","x")'
            return '_'.join('$char('+','.join(str(ord(c)) for c in value[i:i+100])+')' for i in range(0, len(value), 100))
        return str(value)
    for row in ROWS:
        code += f'set ^Bench.BulkEdgesD({row[0]})=$lb("",'+','.join(scalar(v) for v in row[1:])+')\n'
    code += f'set ^Bench.BulkEdgesD={len(ROWS)}\nwrite "SEEDED|",^Bench.BulkEdgesD,!'
    text = b.session(code)
    assert 'LOAD|1' in text and f'SEEDED|{len(ROWS)}' in text, text


def source_gate():
    wanted = digest(ROWS)
    for _ in range(5):
        with b.source() as con:
            rows = con.execute(QUERY).fetchall()
            assert len(rows) == len(ROWS) and digest(rows) == wanted, repr(rows)
            projected = con.execute(QUERY.replace('Payload,Amount', 'Payload,CAST(Amount AS VARCHAR(40)) AS Amount')).fetchall()
            assert digest(projected) == wanted, repr(projected)
    return wanted


def main():
    seed()
    wanted = source_gate()
    for _ in range(4):
        subprocess.run([sys.executable, __file__, '--source-worker'], check=True, capture_output=True, timeout=30)
    emit = dict(kind='correctness', engine=b.ENGINE, source_connections=25, isolated_source_workers=4,
                rows=len(ROWS), typed_decimals=True, projected_decimals=True, null_and_empty_distinct=True,
                Unicode=True, delimiter_text=True, precision_scale='18,4',
                bulk_decimal_metadata='explicit DECIMAL(18,4)', sha256=wanted)
    admin = pyodbc.connect('DRIVER={ODBC Driver 18 for SQL Server};SERVER=127.0.0.1,1433;UID=sa;PWD='+b.SQLPW+';Encrypt=yes;TrustServerCertificate=yes', autocommit=True)
    admin.execute('CREATE DATABASE TransferBench')
    admin.close()
    try:
        with b.target() as con:
            con.execute('CREATE TABLE dbo.BulkEdges (rid int NOT NULL, label nvarchar(1000) NULL, qty int NOT NULL, payload nvarchar(max) NULL, amount decimal(18,4) NULL)')
            con.commit()
        errors = []
        for projection in ('typed', 'text'):
            with b.target() as con:
                con.execute('TRUNCATE TABLE dbo.BulkEdges')
                con.commit()
            # Supply the actual precision explicitly. The raw source property
            # metadata can advertise less precision than its stored values.
            expression = 'CAST(Amount AS DECIMAL(18,4))' if projection == 'typed' else 'CAST(Amount AS VARCHAR(40))'
            query = QUERY.replace('Payload,Amount', 'Payload,'+expression+' AS Amount')
            args = b.java_args()
            args[args.index('scripts:'+os.environ['JDBC_JAR'])] += ':'+os.environ['MSSQL_JDBC_JAR']
            args[-1] = 'JdbcBulkTransfer'
            try:
                b.command(args+[b.ENGINE, query, 'BulkEdges', '1000'])
            except Exception as exc:
                print('BULK_DIAGNOSTIC '+projection+' '+str(exc), flush=True)
                errors.append(projection+': '+str(exc))
                continue
            with b.target() as con:
                got = con.execute('SELECT rid,label,qty,payload,amount FROM dbo.BulkEdges ORDER BY rid').fetchall()
                assert len(got) == len(ROWS) and digest(got) == wanted, repr(got)
            emit['bulk_'+projection] = True
        emit['valid'] = not errors
        emit['errors'] = errors
    finally:
        with b.target() as con:
            con.execute('DROP TABLE IF EXISTS dbo.BulkEdges')
            con.commit()
    b.record(emit)
    print('EDGE_RESULT '+json.dumps(emit), flush=True)
    assert emit['valid'], 'Bulk scalar correctness gate failed'


if __name__ == '__main__':
    if '--source-worker' in sys.argv:
        source_gate()
    elif '--source-only' in sys.argv:
        seed()
        print(source_gate())
    else:
        main()

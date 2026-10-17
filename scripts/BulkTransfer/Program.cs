using System.Data;
using System.Data.Odbc;
using Microsoft.Data.SqlClient;

// Stream one forward-only source reader through the native SQL Server bulk API.
using var source = new OdbcConnection("DRIVER={BenchSource};SERVER=127.0.0.1;PORT=1972;DATABASE=USER;UID=_SYSTEM;PWD=SYS");
source.Open();
using var query = source.CreateCommand();
query.CommandText = args[0];
query.CommandTimeout = 120;
using var reader = query.ExecuteReader(CommandBehavior.SequentialAccess);
var destination = new SqlConnectionStringBuilder {
    DataSource = "127.0.0.1,1433",
    InitialCatalog = "TransferBench",
    UserID = "sa",
    Password = Environment.GetEnvironmentVariable("BENCH_SQL_PASSWORD"),
    Encrypt = true,
    TrustServerCertificate = true
};
using var bulk = new SqlBulkCopy(destination.ConnectionString,
    SqlBulkCopyOptions.TableLock | SqlBulkCopyOptions.KeepNulls | SqlBulkCopyOptions.UseInternalTransaction);
bulk.DestinationTableName = args[1];
bulk.BatchSize = int.Parse(args[2]);
bulk.BulkCopyTimeout = 120;
bulk.EnableStreaming = true;
for (int i = 0; i < 5; i++) bulk.ColumnMappings.Add(i, i);
bulk.WriteToServer(reader);

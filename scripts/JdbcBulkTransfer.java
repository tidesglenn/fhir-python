import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import com.microsoft.sqlserver.jdbc.SQLServerBulkCopy;
import com.microsoft.sqlserver.jdbc.SQLServerBulkCopyOptions;

/** Forward-only source cursor into SQL Server's bulk protocol, without a data file. */
public class JdbcBulkTransfer {
    public static void main(String[] args) throws Exception {
        String engine = args[0];
        Class.forName(engine.equals("cache") ? "com.intersys.jdbc.CacheDriver" : "com.intersystems.jdbc.IRISDriver");
        String url = (engine.equals("cache") ? "jdbc:Cache" : "jdbc:IRIS") + "://127.0.0.1:1972/USER";
        String target = "jdbc:sqlserver://127.0.0.1:1433;databaseName=TransferBench;encrypt=true;trustServerCertificate=true";
        try (Connection source = DriverManager.getConnection(url, "_SYSTEM", "SYS");
             Connection dest = DriverManager.getConnection(target, "sa", System.getenv("BENCH_SQL_PASSWORD"));
             Statement st = source.createStatement(ResultSet.TYPE_FORWARD_ONLY, ResultSet.CONCUR_READ_ONLY);
             SQLServerBulkCopy bulk = new SQLServerBulkCopy(dest)) {
            st.setFetchSize(Integer.parseInt(args[3]));
            st.setQueryTimeout(90);
            SQLServerBulkCopyOptions options = new SQLServerBulkCopyOptions();
            options.setBatchSize(Integer.parseInt(System.getenv().getOrDefault("BENCH_BCP_BATCH", "10000")));
            options.setBulkCopyTimeout(90);
            options.setTableLock(true);
            options.setKeepNulls(true);
            options.setUseInternalTransaction(true);
            bulk.setBulkCopyOptions(options);
            bulk.setDestinationTableName("dbo." + args[2]);
            for (int i = 1; i <= 5; i++) bulk.addColumnMapping(i, i);
            try (ResultSet rows = st.executeQuery(args[1])) {
                bulk.writeToServer(rows);
            }
        }
    }
}

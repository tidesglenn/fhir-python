import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import java.io.StringReader;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Proxy;
import com.microsoft.sqlserver.jdbc.SQLServerBulkCopy;
import com.microsoft.sqlserver.jdbc.SQLServerBulkCopyOptions;

/** Forward-only source cursor into SQL Server's bulk protocol, without a data file. */
public class JdbcBulkTransfer {
    private static ResultSet scalarStreams(ResultSet rows) {
        // The tested source drivers throw on getCharacterStream(NULL). Preserve
        // JDBC NULL semantics explicitly for these bounded scalar fixtures.
        return (ResultSet) Proxy.newProxyInstance(JdbcBulkTransfer.class.getClassLoader(),
            new Class<?>[] {ResultSet.class}, (proxy, method, args) -> {
                if (method.getName().equals("getCharacterStream") || method.getName().equals("getNCharacterStream")) {
                    String value = args[0] instanceof Integer ? rows.getString((Integer) args[0]) : rows.getString((String) args[0]);
                    return value == null ? null : new StringReader(value);
                }
                try {
                    return method.invoke(rows, args);
                } catch (InvocationTargetException ex) {
                    throw ex.getCause();
                }
            });
    }

    public static void main(String[] args) throws Exception {
        String engine = args[0];
        Class.forName(engine.equals("cache") ? "com.intersys.jdbc.CacheDriver" : "com.intersystems.jdbc.IRISDriver");
        String url = (engine.equals("cache") ? "jdbc:Cache" : "jdbc:IRIS") + "://127.0.0.1:1972/USER";
        String target = "jdbc:sqlserver://127.0.0.1:1433;databaseName=TransferBench;encrypt=true;trustServerCertificate=true";
        try (Connection source = DriverManager.getConnection(url, "_SYSTEM", "SYS");
             Statement st = source.createStatement(ResultSet.TYPE_FORWARD_ONLY, ResultSet.CONCUR_READ_ONLY);
             // Per-batch internal transactions require bulk copy to own its
             // destination connection. The Connection constructor forbids them.
             SQLServerBulkCopy bulk = new SQLServerBulkCopy(target + ";user=sa;password=" + System.getenv("BENCH_SQL_PASSWORD"))) {
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
                bulk.writeToServer(scalarStreams(rows));
            }
        }
    }
}

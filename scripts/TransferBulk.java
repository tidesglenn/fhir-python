import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import com.microsoft.sqlserver.jdbc.SQLServerBulkCopy;
import com.microsoft.sqlserver.jdbc.SQLServerBulkCopyOptions;

/** Forward-only JDBC ResultSet directly into the SQL Server bulk-copy API. */
public class TransferBulk {
    public static void main(String[] args) throws Exception {
        String engine = args[0];
        Class.forName(engine.equals("cache") ? "com.intersys.jdbc.CacheDriver" : "com.intersystems.jdbc.IRISDriver");
        String url = (engine.equals("cache") ? "jdbc:Cache" : "jdbc:IRIS") + "://127.0.0.1:1972/USER";
        String destination = "jdbc:sqlserver://127.0.0.1:1433;databaseName=TransferBench;user=sa;password="
            + System.getenv("BENCH_SQL_PASSWORD") + ";encrypt=true;trustServerCertificate=true";
        try (Connection con = DriverManager.getConnection(url, "_SYSTEM", "SYS");
             Statement st = con.createStatement(ResultSet.TYPE_FORWARD_ONLY, ResultSet.CONCUR_READ_ONLY);
             SQLServerBulkCopy bulk = new SQLServerBulkCopy(destination)) {
            st.setFetchSize(10000);
            st.setQueryTimeout(120);
            SQLServerBulkCopyOptions options = new SQLServerBulkCopyOptions();
            options.setBatchSize(Integer.parseInt(args[3]));
            options.setBulkCopyTimeout(120);
            options.setTableLock(true);
            options.setKeepNulls(true);
            options.setUseInternalTransaction(true);
            bulk.setBulkCopyOptions(options);
            bulk.setDestinationTableName(args[2]);
            for (int col=1; col<=5; col++) bulk.addColumnMapping(col,col);
            try (ResultSet rs = st.executeQuery(args[1])) {
                bulk.writeToServer(rs);
            }
        }
    }
}

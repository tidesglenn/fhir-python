import java.io.BufferedWriter;
import java.io.OutputStreamWriter;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;

/** Generic scalar JDBC-to-Unicode-file probe for disposable synthetic tables. */
public class TransferExtract {
    public static void main(String[] args) throws Exception {
        String engine = args[0];
        Class.forName(engine.equals("cache") ? "com.intersys.jdbc.CacheDriver" : "com.intersystems.jdbc.IRISDriver");
        String url = (engine.equals("cache") ? "jdbc:Cache" : "jdbc:IRIS") + "://127.0.0.1:1972/USER";
        int bufferChars = Integer.parseInt(System.getenv().getOrDefault("BENCH_JAVA_BUFFER", "8192"));
        try (Connection con = DriverManager.getConnection(url, "_SYSTEM", "SYS");
             Statement st = con.createStatement(ResultSet.TYPE_FORWARD_ONLY, ResultSet.CONCUR_READ_ONLY);
             OutputStream bytes = args[2].equals("-") ? System.out : Files.newOutputStream(Paths.get(args[2]));
             BufferedWriter out = new BufferedWriter(new OutputStreamWriter(bytes, StandardCharsets.UTF_16LE), bufferChars)) {
            st.setFetchSize(Integer.parseInt(args[3]));
            st.setQueryTimeout(90);
            try (ResultSet rs = st.executeQuery(args[1])) {
                while (rs.next()) {
                    for (int col = 1; col <= 5; col++) {
                        if (col > 1) out.write('\t');
                        String value = rs.getString(col);
                        if (value != null) {
                            if (value.indexOf('\t') >= 0 || value.indexOf('\n') >= 0 || value.indexOf('\r') >= 0)
                                throw new IllegalArgumentException("Delimiter-bearing scalar requires a different file format");
                            out.write(value);
                        }
                    }
                    out.write('\n');
                }
            }
        }
    }
}

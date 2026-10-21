package port;
import com.sd.common.util.ExportUtil;
import org.apache.poi.hssf.usermodel.HSSFWorkbook;
import java.nio.file.*;
import java.io.*;
public final class OriginalExportProbe {
 public static void main(String[] args) throws Exception {
  Path out = Path.of(args[0]).toAbsolutePath();
  if (Files.exists(out)) throw new IllegalArgumentException("Refuse overwrite");
  String[][] rows = {{"当票号","业务编号","金额","备注"},{"000012","TEST-E001","10600.00","仅合成移植测试"},{"000013","TEST-E002","0.01","中文测试"}};
  ExportUtil util = new ExportUtil();
  String result = util.writeXLS(out.toString(),rows,"Linux移植测试");
  if (!ExportUtil.exportSuccessTag.equals(result)) throw new IllegalStateException("Vendor export returned failure");
  try (InputStream in = Files.newInputStream(out); HSSFWorkbook book = new HSSFWorkbook(in)) {
   for(int r=0;r<rows.length;r++) for(int c=0;c<rows[r].length;c++)
    if(!rows[r][c].equals(book.getSheetAt(0).getRow(r).getCell(c).getStringCellValue())) throw new IllegalStateException("Cell mismatch");
   System.out.println("ORIGINAL_EXPORT_ROUNDTRIP_PASS: 12 cells; UTF-8 text; leading zero IDs; decimal strings; no financial data.");
  }
 }
}

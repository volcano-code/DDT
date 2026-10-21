package port;

import com.sd.common.util.ExportUtil;
import org.apache.poi.hssf.usermodel.HSSFWorkbook;
import java.nio.file.*;
import java.io.*;
import java.util.*;

/** Executes the original exporter, not a reimplementation, using only synthetic data. */
public final class ExportMatrixProbe {
    private static int cells = 0;
    private static int files = 0;

    private static void check(Path dir, String name, String[][] expected) throws Exception {
        Path out = dir.resolve(name + ".xls");
        if (Files.exists(out)) throw new IllegalArgumentException("Refuse overwrite");
        String result = new ExportUtil().writeXLS(out.toString(), expected, "移植验证");
        if (!ExportUtil.exportSuccessTag.equals(result)) throw new IllegalStateException("Original export failed: " + name);
        try (InputStream in = Files.newInputStream(out); HSSFWorkbook wb = new HSSFWorkbook(in)) {
            if (wb.getNumberOfSheets() != 1) throw new IllegalStateException("Sheet count mismatch");
            if (wb.getSheetAt(0).getLastRowNum() != expected.length) throw new IllegalStateException("Row count mismatch");
            for (int r = 0; r < expected.length; r++) {
                if (wb.getSheetAt(0).getRow(r).getLastCellNum() != expected[r].length)
                    throw new IllegalStateException("Column count mismatch");
                for (int c = 0; c < expected[r].length; c++) {
                    if (wb.getSheetAt(0).getRow(r).getCell(c).getCellType() != 1)
                        throw new IllegalStateException("Expected string cell, not formula or number");
                    String actual = wb.getSheetAt(0).getRow(r).getCell(c).getStringCellValue();
                    if (!expected[r][c].equals(actual)) throw new IllegalStateException("Cell mismatch " + name + ":" + r + ":" + c);
                    cells++;
                }
            }
        }
        // The original exporter appends one styled blank border row. It is not a business record.
        try (InputStream in = Files.newInputStream(out); HSSFWorkbook wb = new HSSFWorkbook(in)) {
            var border = wb.getSheetAt(0).getRow(expected.length);
            for (int c = 0; c < expected[0].length; c++)
                if (border.getCell(c).getCellType() != 3) throw new IllegalStateException("Unexpected trailing data");
        }
        files++;
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) throw new IllegalArgumentException("Expected fresh synthetic output directory");
        Path dir = Path.of(args[0]).toAbsolutePath();
        Files.createDirectory(dir);
        check(dir, "01-unicode", new String[][] {
            {"编号", "文本", "金额字符串", "备注"},
            {"000012", "典当通 Linux", "10600.00", "仅合成样例"},
            {"000000000001", "汉字 Ω 😀", "-0.01", "换行\n第二行"},
            {"000013", "", "123456789012345678.90", "tab\tvalue"}
        });
        check(dir, "02-formula-like-strings", new String[][] {
            {"输入", "预期类型"}, {"=1+1", "字符串，不是公式"},
            {"+SUM(A1:A2)", "字符串"}, {"@SUM(1)", "字符串"},
            {"-1+2", "字符串"}, {"https://example.invalid/", "普通文本"}
        });
        String[][] batch = new String[301][5];
        batch[0] = new String[] {"编号", "业务", "金额字符串", "状态", "说明"};
        for (int i = 1; i < batch.length; i++) {
            batch[i] = new String[] {String.format(Locale.ROOT, "%08d", i), "SYNTH-" + i,
                i + ".00", "合成", "批量行 " + i};
        }
        check(dir, "03-batch", batch);
        check(dir, "04-empty-values", new String[][] {{"A", "B", "C"}, {"", "", ""}, {"x", "", "z"}});
        check(dir, "05-long-text", new String[][] {{"文本", "说明"}, {"测".repeat(32767), "XLS长度边界"}});
        check(dir, "06-中文文件名", new String[][] {{"票号", "备注"}, {"000001", "空格与中文文件路径"}});
        System.out.println("EXPORT_MATRIX_PASS files=" + files + " cells=" + cells);
        ProbeEvidence.emit("{\"kind\":\"export-matrix\",\"files\":" + files +
            ",\"cells\":" + cells + ",\"original_exporter\":true,\"synthetic_only\":true,\"gui_tested\":false,\"verified_blank_border_rows\":6}");
    }
}

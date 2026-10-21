package port;

import java.nio.file.*;
import java.security.SecureRandom;
import java.util.HexFormat;
import org.eclipse.swt.SWT;
import org.eclipse.swt.browser.*;
import org.eclipse.swt.layout.FillLayout;
import org.eclipse.swt.widgets.*;

/** Real native browser probe. No substituted HTML login and no vendor endpoint. */
public final class NativeProbe {
    private static void oneCycle(Display display, int cycle) throws Exception {
        Shell shell = new Shell(display);
        shell.setLayout(new FillLayout());
        shell.setText("Linux SWT/WebKit 本地桥接探针（不是典当通登录）");
        shell.setSize(720, 400);
        String nonce = HexFormat.of().formatHex(new SecureRandom().generateSeed(16));
        final int[] called = {0};
        final boolean[] sent = {false};
        try {
            Browser browser = new Browser(shell, SWT.WEBKIT);
            if (!"webkit".equalsIgnoreCase(browser.getBrowserType())) throw new IllegalStateException("Not WebKit");
            BrowserFunction function = new BrowserFunction(browser, "ddtPortProbe") {
                @Override public Object function(Object[] values) {
                    if (values.length != 3 || !nonce.equals(values[0]) || !Boolean.TRUE.equals(values[1]) ||
                            !(values[2] instanceof Object[])) throw new IllegalArgumentException("Probe argument types mismatch");
                    Object[] row = (Object[])values[2];
                    if (row.length != 3 || !"000012".equals(row[0]) || !"10600.00".equals(row[1]) || !"汉字".equals(row[2]))
                        throw new IllegalArgumentException("Unicode / identifier / decimal string did not survive bridge");
                    called[0]++;
                    return new Object[] {"ACK", row[0], row[1]};
                }
            };
            browser.addProgressListener(new ProgressAdapter() {
                @Override public void completed(ProgressEvent event) {
                    if (sent[0]) return;
                    sent[0] = true;
                    String script = "try { var a=window.ddtPortProbe('"+nonce+"',true,['000012','10600.00','汉字']);" +
                        "window.ddtPortVerified = (a.length===3 && a[0]==='ACK' && a[1]==='000012' && a[2]==='10600.00') ? '"+nonce+"' : 'bad-return';" +
                        "document.body.dataset.result=window.ddtPortVerified; } catch(e) { window.ddtPortVerified='bridge-error'; }";
                    if (!browser.execute(script)) throw new IllegalStateException("Browser refused bridge script");
                }
            });
            if (!browser.setText("<!doctype html><meta charset='utf-8'><h1>Linux WebKit 本地探针</h1>" +
                    "<p>平台测试，不是典当通业务界面。重建浏览器周期："+cycle+" / 2</p>"))
                throw new IllegalStateException("Browser refused local HTML");
            shell.open();
            long deadline = System.nanoTime() + 12_000_000_000L;
            while (!shell.isDisposed() && called[0] == 0 && System.nanoTime() < deadline) {
                if (!display.readAndDispatch()) Thread.sleep(10);
            }
            if (called[0] != 1) throw new IllegalStateException("Callback absent or duplicated");
            Object returned = browser.evaluate("return window.ddtPortVerified;");
            if (!nonce.equals(returned)) throw new IllegalStateException("JavaScript did not verify the Java return value");
            function.dispose();
        } finally {
            if (!shell.isDisposed()) shell.dispose();
        }
    }

    public static void main(String[] args) throws Exception {
        RuntimeIntegrity.verify();
        if (!"gtk".equals(SWT.getPlatform())) throw new IllegalStateException("Native probe requires genuine SWT GTK");
        Display display = new Display();
        try {
            oneCycle(display, 1);
            oneCycle(display, 2);
            OriginalExportProbe.main(new String[] {args[0]});
            System.out.println("NATIVE_RENDERER_BRIDGE_EXPORT_PASS; synthetic platform test, not vendor login");
            ProbeEvidence.emit("{\"kind\":\"native\",\"render_cycles\":2,\"bridge_arguments\":true," +
                "\"bridge_return\":true,\"original_export\":true,\"synthetic_only\":true,\"vendor_login\":false}");
        } finally {
            if (!display.isDisposed()) display.dispose();
        }
    }
}

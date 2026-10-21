import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;
import org.eclipse.swt.SWT;
import org.eclipse.swt.browser.Browser;
import org.eclipse.swt.browser.BrowserFunction;
import org.eclipse.swt.browser.ProgressAdapter;
import org.eclipse.swt.browser.ProgressEvent;
import org.eclipse.swt.layout.FillLayout;
import org.eclipse.swt.widgets.Display;
import org.eclipse.swt.widgets.Event;
import org.eclipse.swt.widgets.Shell;

/** Self-written platform test. Never loads the proprietary client or a remote page. */
public final class PublicPlatformProbe {
    public static void main(String[] args) throws Exception {
        if (args.length != 2 || !args[0].matches("[a-f0-9]{32}")) {
            throw new IllegalArgumentException("Run identifier and new JSON output required");
        }
        Path output = Path.of(args[1]);
        if (Files.exists(output)) throw new IllegalArgumentException("Refusing to overwrite evidence");
        if (!"gtk".equals(SWT.getPlatform())) throw new IllegalStateException("SWT GTK required");
        Display display = new Display();
        int events = 0;
        try {
            for (int i = 0; i < 2; i++) {
                Shell shell = new Shell(display);
                final int[] observed = {0};
                try {
                    shell.setText("DDT public platform probe - synthetic, not vendor login");
                    shell.setSize(640, 360);
                    shell.addListener(SWT.Selection, e -> observed[0]++);
                    shell.open();
                    shell.notifyListeners(SWT.Selection, new Event());
                    while (display.readAndDispatch()) { }
                    if (!shell.isVisible() || observed[0] != 1) {
                        throw new AssertionError("GTK visibility/event check failed");
                    }
                    events += observed[0];
                } finally { if (!shell.isDisposed()) shell.dispose(); }
            }
            for (int cycle = 0; cycle < 2; cycle++) browserCycle(display);
            if (display.getShells().length != 0) throw new AssertionError("Leaked shell");
        } finally { if (!display.isDisposed()) display.dispose(); }
        String json = "{\"schema\":1,\"status\":\"passed\",\"run_id\":\"" + args[0]
            + "\",\"pid\":" + ProcessHandle.current().pid()
            + ",\"gtk_cycles\":2,\"gtk_events\":" + events
            + ",\"webkit_cycles\":2,\"bridge_arguments\":true,\"bridge_return\":true"
            + ",\"synthetic_only\":true,\"vendor_client_loaded\":false"
            + ",\"original_export_tested\":false,\"business_verified\":false}\n";
        Files.writeString(output, json, StandardCharsets.UTF_8,
            java.nio.file.StandardOpenOption.CREATE_NEW);
        System.out.println("PUBLIC_PLATFORM_PASS run_id=" + args[0]);
    }

    private static void browserCycle(Display display) throws Exception {
        Shell shell = new Shell(display);
        String nonce = UUID.randomUUID().toString();
        final int[] callbacks = {0};
        final boolean[] started = {false};
        try {
            shell.setLayout(new FillLayout());
            shell.setSize(640, 360);
            Browser browser = new Browser(shell, SWT.WEBKIT);
            if (!"webkit".equalsIgnoreCase(browser.getBrowserType())) {
                throw new IllegalStateException("Real WebKit required");
            }
            BrowserFunction callback = new BrowserFunction(browser, "publicDDTProbe") {
                @Override public Object function(Object[] args) {
                    if (args.length != 4 || !nonce.equals(args[0])
                        || !"000012".equals(args[1]) || !"10600.00".equals(args[2])
                        || !"汉字".equals(args[3])) throw new AssertionError("Bridge arguments differ");
                    callbacks[0]++;
                    return new Object[] {"ACK", args[1], args[2]};
                }
            };
            browser.addProgressListener(new ProgressAdapter() {
                @Override public void completed(ProgressEvent event) {
                    if (started[0]) return;
                    started[0] = true;
                    String script = "try{var a=window.publicDDTProbe('" + nonce
                        + "','000012','10600.00','汉字');window.ddtResult="
                        + "a.length===3&&a[0]==='ACK'&&a[1]==='000012'&&a[2]==='10600.00'"
                        + "?'" + nonce + "':'bad';}catch(e){window.ddtResult='error';}";
                    if (!browser.execute(script)) throw new IllegalStateException("JavaScript refused");
                }
            });
            if (!browser.setText("<!doctype html><meta charset='utf-8'><h1>Local synthetic platform test</h1>")) {
                throw new IllegalStateException("Local page refused");
            }
            shell.open();
            long deadline = System.nanoTime() + 15_000_000_000L;
            while (callbacks[0] == 0 && !shell.isDisposed() && System.nanoTime() < deadline) {
                if (!display.readAndDispatch()) Thread.sleep(10);
            }
            if (callbacks[0] != 1 || !nonce.equals(browser.evaluate("return window.ddtResult;"))) {
                throw new AssertionError("Bidirectional bridge did not complete");
            }
            callback.dispose();
        } finally { if (!shell.isDisposed()) shell.dispose(); }
    }
}

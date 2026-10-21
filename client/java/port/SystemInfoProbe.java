package port;
import com.sd.common.util.device.SystemInfoUtil;
import java.awt.GraphicsEnvironment;

/** Headless, namespace-only adapter probe; never calls vendor Start or any endpoint. */
public final class SystemInfoProbe {
    public static void main(String[] args) {
        if (!GraphicsEnvironment.isHeadless()) throw new IllegalStateException("Headless probe required");
        if (args.length != 1 || !"--expect-offline-empty".equals(args[0]))
            throw new IllegalArgumentException("Explicit offline-empty assertion required");
        SystemInfoUtil util = new SystemInfoUtil();
        util.init();
        if (!SystemInfoUtil.osName.equals(System.getProperty("os.name", "")))
            throw new AssertionError("Incorrect OS name");
        if (!SystemInfoUtil.osVersion.equals(System.getProperty("os.version", "")))
            throw new AssertionError("Incorrect OS version");
        if (!SystemInfoUtil.ip.isEmpty() || !SystemInfoUtil.mac.isEmpty() || !SystemInfoUtil.hostName.isEmpty())
            throw new AssertionError("Probe is not in an empty network namespace");
        if (SystemInfoUtil.isWinXP()) throw new AssertionError("Linux must not report Windows XP");
        // Verify reconnect/refresh semantics: missing data must not retain stale values.
        SystemInfoUtil.ip = "stale-test-only";
        SystemInfoUtil.mac = "stale-test-only";
        SystemInfoUtil.hostName = "stale-test-only";
        util.init();
        if (!SystemInfoUtil.ip.isEmpty() || !SystemInfoUtil.mac.isEmpty() || !SystemInfoUtil.hostName.isEmpty())
            throw new AssertionError("Stale network identity retained");
        if (!SystemInfoUtil.dpi.isEmpty()) throw new AssertionError("Headless dimensions must remain unknown");
        System.out.println("SYSTEMINFO_OFFLINE_PASS");
        System.out.println("DDT_SYSTEMINFO_JSON={\"os_properties_match\":true,\"missing_network_values_empty\":true,\"stale_values_cleared\":true,\"headless_dimensions_unknown\":true,\"identity_fabricated\":false,\"vendor_started\":false,\"gui_tested\":false}");
    }
}

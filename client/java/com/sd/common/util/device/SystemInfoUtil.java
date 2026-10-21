package com.sd.common.util.device;

import java.awt.Dimension;
import java.awt.GraphicsConfiguration;
import java.awt.GraphicsEnvironment;
import java.awt.HeadlessException;
import java.awt.Toolkit;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.SocketException;
import java.net.UnknownHostException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Enumeration;
import java.util.List;
import java.util.Locale;
import com.sd.ddt.base.ComParams;

/**
 * Linux system information adapter, not a license or device-identity emulator.
 * Only observes interfaces visible in THIS process's network namespace.
 * Missing values stay empty; no shell, sysfs escape, fabricated ID or backend call.
 */
public class SystemInfoUtil {
    public static volatile String osName = "";
    public static volatile String osVersion = "";
    public static volatile String osArch = "";
    public static volatile String ip = "";
    public static volatile String hostName = "";
    public static volatile String mac = "";
    // Original field is screen dimensions, despite its name. Preserve that meaning.
    public static volatile String dpi = "";

    private static volatile String networkState = "not_collected";
    private static volatile String displayState = "not_collected";

    public SystemInfoUtil() {}

    public void init() {
        getSystemInfo();
        getDPIScale();
        getNetworkInfo();
        getScreenDPI();
        // No actual IP/MAC/hostname is written to the adapter log.
        System.out.println("LINUX_SYSTEMINFO_COLLECTED; network=" + networkState
            + "; display=" + displayState
            + "; missing_values_not_synthesized=true; vendor_login_not_verified=true");
    }

    public void getSystemInfo() {
        osName = property("os.name");
        osVersion = property("os.version");
        String arch = property("os.arch");
        String bits = property("sun.arch.data.model");
        osArch = "64".equals(bits) ? "64bit" : "32".equals(bits) ? "32bit" : arch;
    }

    public void getDPIScale() {
        if (GraphicsEnvironment.isHeadless()) {
            displayState = "headless_unavailable";
            return;
        }
        try {
            GraphicsConfiguration gc = GraphicsEnvironment.getLocalGraphicsEnvironment()
                .getDefaultScreenDevice().getDefaultConfiguration();
            double scale = gc.getDefaultTransform().getScaleX();
            if (!Double.isFinite(scale) || scale <= 0) {
                displayState = "invalid_scale_unavailable";
                return;
            }
            // Keep the vendor float/2-decimal scaling convention; don't guess a DPI.
            ComParams.DPI_SCALE = Math.round(scale * 100.0) / 100.0f;
            displayState = "available";
        } catch (HeadlessException | SecurityException ex) {
            displayState = "display_unavailable";
        }
    }

    private void getScreenDPI() {
        dpi = "";
        if (GraphicsEnvironment.isHeadless()) return;
        try {
            Dimension size = Toolkit.getDefaultToolkit().getScreenSize();
            if (size.width > 0 && size.height > 0) dpi = size.width + "*" + size.height;
        } catch (HeadlessException | SecurityException ex) {
            displayState = "display_unavailable";
        }
    }

    private static String property(String key) {
        try { return System.getProperty(key, ""); }
        catch (SecurityException ex) { return ""; }
    }

    private void getNetworkInfo() {
        // Never retain an old address after disconnect or namespace changes.
        ip = ""; hostName = ""; mac = "";
        List<Nic> choices = new ArrayList<>();
        boolean unavailable = false;
        try {
            Enumeration<NetworkInterface> all = NetworkInterface.getNetworkInterfaces();
            if (all != null) while (all.hasMoreElements()) {
                NetworkInterface nic = all.nextElement();
                if (nic == null) continue;
                try {
                    if (!nic.isUp() || nic.isLoopback()) continue;
                    byte[] hardware = null;
                    try { hardware = nic.getHardwareAddress(); }
                    catch (SocketException | SecurityException ex) { unavailable = true; }
                    Enumeration<InetAddress> addresses = nic.getInetAddresses();
                    while (addresses != null && addresses.hasMoreElements()) {
                        InetAddress address = addresses.nextElement();
                        if (!usable(address)) continue;
                        choices.add(new Nic(nic.getName(), address, hardware));
                    }
                } catch (SocketException | SecurityException ex) { unavailable = true; }
            }
        } catch (SocketException | SecurityException ex) { unavailable = true; }
        if (choices.isEmpty()) {
            networkState = unavailable ? "network_information_unavailable" : "no_active_non_loopback_address";
            return;
        }
        // getLocalHost can resolve an address that is NOT bound in this namespace.
        // Only use it as a preference after matching it to an observed interface.
        InetAddress local = null;
        try { local = InetAddress.getLocalHost(); }
        catch (UnknownHostException | SecurityException ex) { /* no guessed hostname */ }
        Nic selected = choose(choices, local);
        if (selected == null) {
            networkState = "network_information_unavailable";
            return;
        }
        ip = selected.address.getHostAddress();
        mac = formatHardware(selected.hardware);
        if (local != null && local.equals(selected.address)) {
            // InetAddress.getLocalHost has already provided the local host label.
            hostName = local.getHostName();
        }
        networkState = mac.isEmpty() ? "address_available_mac_unavailable" : "address_and_mac_available";
    }

    static boolean usable(InetAddress address) {
        return address != null && !address.isAnyLocalAddress() && !address.isLoopbackAddress()
            && !address.isMulticastAddress() && !address.isLinkLocalAddress();
    }

    static final class Nic {
        final String name;
        final InetAddress address;
        final byte[] hardware;
        Nic(String name, InetAddress address, byte[] hardware) {
            this.name = name == null ? "" : name;
            this.address = address;
            this.hardware = hardware == null ? null : hardware.clone();
        }
    }

    static Nic choose(List<Nic> choices, InetAddress preferred) {
        if (choices == null) return null;
        return choices.stream().filter(n -> n != null && usable(n.address))
            .min(Comparator.comparingInt((Nic n) -> preferred != null && n.address.equals(preferred) ? 0 : 1)
                .thenComparingInt(n -> n.address.getAddress().length == 4 ? 0 : 1)
                .thenComparing(n -> n.name).thenComparing(n -> n.address.getHostAddress()))
            .orElse(null);
    }

    static String formatHardware(byte[] address) {
        if (address == null || address.length == 0) return "";
        boolean nonzero = false;
        for (byte b : address) if (b != 0) nonzero = true;
        if (!nonzero) return "";
        StringBuilder result = new StringBuilder();
        for (byte b : address) {
            if (result.length() > 0) result.append('-');
            result.append(String.format(Locale.ROOT, "%02x", b & 0xff));
        }
        return result.toString();
    }

    public static boolean isWinXP() {
        String name = osName == null ? "" : osName.toLowerCase(Locale.ROOT);
        // Truthful OS check: a Linux kernel version containing '5.1' is not Windows XP.
        return name.startsWith("windows") && (name.contains("xp") || "5.1".equals(osVersion));
    }

    public static void main(String[] args) { new SystemInfoUtil().init(); }
}

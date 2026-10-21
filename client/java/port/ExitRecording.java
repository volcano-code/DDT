package port;

import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermission;
import java.util.EnumSet;
import java.util.Set;
import java.util.regex.Pattern;
import jdk.jfr.Event;
import jdk.jfr.FlightRecorder;
import jdk.jfr.Label;
import jdk.jfr.Name;
import jdk.jfr.Recording;
import jdk.jfr.StackTrace;

/** Minimal, opt-in exit evidence. Does NOT intercept or change System.exit/login. */
public final class ExitRecording {
    private static Recording recording;
    private ExitRecording() {}
    @Name("ddt.RunIdentity") @Label("DDT private run binding") @StackTrace(false)
    public static final class Identity extends Event {
        public String runId;
        public long processId;
    }
    public static synchronized void start() {
        String name = System.getProperty("ddt.port.exit.file", "");
        if (name.isEmpty()) return; // Development tests without this diagnostic keep their behaviour.
        String id = System.getProperty("ddt.port.run.id", "");
        try {
            if (!Pattern.matches("[a-f0-9]{32}", id)) throw new IllegalArgumentException("run identity");
            Path dest = Path.of(name).toAbsolutePath();
            Path parent = dest.getParent();
            if (!Files.isDirectory(parent, LinkOption.NOFOLLOW_LINKS)
                || Files.isSymbolicLink(parent) || Files.exists(dest, LinkOption.NOFOLLOW_LINKS))
                throw new IllegalArgumentException("private destination");
            Set<PosixFilePermission> perms = Files.getPosixFilePermissions(parent);
            if (!EnumSet.of(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE,
                            PosixFilePermission.OWNER_EXECUTE).containsAll(perms))
                throw new IllegalArgumentException("private permissions");
            if (recording != null || !FlightRecorder.isAvailable()) throw new IllegalStateException("recording state");
            Recording r = new Recording(); // No default/profile settings: only these TWO events.
            r.setName("ddt-exit-only");
            r.enable("jdk.Shutdown").withStackTrace();
            r.enable(Identity.class).withoutStackTrace();
            r.setMaxSize(2_000_000L);
            r.setDestination(dest);
            r.setDumpOnExit(true);
            r.start();
            Identity e = new Identity(); e.runId = id; e.processId = ProcessHandle.current().pid(); e.commit();
            recording = r; // Keep it live until VM shutdown; no explicit close before shutdown event.
            System.out.println("DDT_EXIT_CAPTURE_READY");
        } catch (Exception | LinkageError ex) {
            // No paths, arguments, credentials or unchecked exception text go into shared evidence.
            System.out.println("DDT_EXIT_CAPTURE_UNAVAILABLE");
        }
    }
}

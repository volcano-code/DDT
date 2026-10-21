package port;

import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import jdk.jfr.consumer.RecordedEvent;
import jdk.jfr.consumer.RecordedFrame;
import jdk.jfr.consumer.RecordedStackTrace;
import jdk.jfr.consumer.RecordingFile;

/** Reads only a supervisor-created private recording, returns allowlisted metadata. */
public final class ExitRecordingReader {
    private ExitRecordingReader() {}
    public static void main(String[] args) {
        try {
            if (args.length != 3 || !args[1].matches("[a-f0-9]{32}")) throw new IllegalArgumentException();
            long pid = Long.parseLong(args[2]);
            if (pid <= 0) throw new IllegalArgumentException();
            Path path = Path.of(args[0]);
            if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS) || Files.isSymbolicLink(path)
                || Files.size(path) > 16_000_000L) throw new IllegalArgumentException();
            int identities = 0, shutdowns = 0, total = 0;
            boolean bound = false, terminate = false, systemExit = false;
            String mechanism = "unknown";
            List<String> frames = new ArrayList<>();
            try (RecordingFile file = new RecordingFile(path)) {
                while (file.hasMoreEvents()) {
                    if (++total > 64) throw new IllegalArgumentException();
                    RecordedEvent event = file.readEvent();
                    String name = event.getEventType().getName();
                    if ("ddt.RunIdentity".equals(name)) {
                        identities++;
                        bound = args[1].equals(event.getString("runId")) && pid == event.getLong("processId");
                    } else if ("jdk.Shutdown".equals(name)) {
                        shutdowns++;
                        String reason = event.getString("reason");
                        if ("No remaining non-daemon Java threads".equals(reason)) mechanism = "non_daemon_threads_finished";
                        else if ("Shutdown requested from Java".equals(reason)) mechanism = "java_shutdown_requested";
                        RecordedStackTrace trace = event.getStackTrace();
                        if (trace != null) for (RecordedFrame f : trace.getFrames()) {
                            String c = f.getMethod().getType().getName();
                            String m = f.getMethod().getName();
                            if (c.startsWith("java.lang.Terminator") || c.startsWith("jdk.internal.misc.Signal")) terminate = true;
                            if (("java.lang.System".equals(c) || "java.lang.Runtime".equals(c)) && "exit".equals(m)) systemExit = true;
                            if (frames.size() < 12 && (c.startsWith("com.sd.") || c.startsWith("port.") || c.startsWith("java.lang.") || c.startsWith("jdk.internal.misc.Signal"))
                                && c.matches("[A-Za-z0-9_.$]+") && m.matches("[A-Za-z0-9_$<>]+")) {
                                frames.add("{\"class\":\""+c+"\",\"method\":\""+m+"\",\"line\":"+f.getLineNumber()+"}");
                            }
                        }
                    } else { throw new IllegalArgumentException(); } // Do not consume general performance recordings.
                }
            }
            if (identities != 1 || !bound || shutdowns > 1) throw new IllegalArgumentException();
            if (shutdowns == 0) mechanism = "shutdown_event_absent";
            else if (terminate) mechanism = "signal_handler_shutdown";
            else if (systemExit) mechanism = "explicit_system_exit";
            System.out.println("DDT_EXIT_JSON={\"schema\":1,\"run_id\":\""+args[1]+"\",\"pid\":"+pid
                +",\"mechanism\":\""+mechanism+"\",\"identity_bound\":true,\"shutdown_event_present\":"+(shutdowns==1)
                +",\"frames\":["+String.join(",",frames)+"],\"business_verified\":false}");
        } catch (Exception | LinkageError ex) {
            System.out.println("DDT_EXIT_RECORDING_INVALID");
            System.exit(2);
        }
    }
}

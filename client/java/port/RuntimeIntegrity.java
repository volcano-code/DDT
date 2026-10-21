package port;
import java.nio.file.*;
import java.security.MessageDigest;
import java.io.*;
import java.util.*;
/** Local integrity guard, not a licence checker or protection against a local administrator. */
public final class RuntimeIntegrity {
 private RuntimeIntegrity() {}
 public static Path root() {
  String text=System.getProperty("ddt.port.root");
  if(text==null) throw new IllegalStateException("Missing ddt.port.root");
  return Path.of(text).toAbsolutePath().normalize();
 }
 public static void verify() {
  try {
   Path root=root().toRealPath(); Path lock=root.resolve("runtime.sha256");
   if(!Files.isRegularFile(lock)) throw new IOException("Runtime not sealed: install Linux SWT and build");
   List<String> lines=Files.readAllLines(lock); int n=0;
   Set<String> seen=new HashSet<>();
   for(String line:lines) {
    if(line.isBlank()) continue;
    if(!line.matches("[0-9a-f]{64}  [A-Za-z0-9_./-]+")) throw new IOException("Invalid runtime manifest");
    String rel=line.substring(66); Path file=root.resolve(rel).normalize();
    if(!file.startsWith(root)||!seen.add(rel)||Files.isSymbolicLink(file)||!Files.isRegularFile(file)||!file.toRealPath().startsWith(root)) throw new IOException("Unsafe runtime member");
    String actual=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(file)));
    if(!actual.equals(line.substring(0,64))) throw new IOException("Runtime checksum mismatch: "+rel);
    n++;
   }
   if(n<24||!seen.contains("native/swt.jar")||!seen.contains("patches/platform-patches.jar")||!seen.contains("overlay/linux-compat.jar")||!seen.contains("apps/ddtclient9.jar")) throw new IOException("Incomplete runtime manifest");
  } catch(Exception e) {throw new IllegalStateException("Linux runtime integrity failed: "+e.getMessage(),e);}
 }
}

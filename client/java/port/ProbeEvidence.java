package port;
/** Binds local test evidence to this JVM and its supervisor-selected run. Not remote attestation. */
public final class ProbeEvidence {
 private ProbeEvidence() {}
 public static String identityFields() {
  String id=System.getProperty("ddt.port.run.id", "");
  if(!id.matches("[0-9a-f]{32}")) throw new IllegalStateException("Missing supervisor run identity");
  return "\"schema\":2,\"pid\":"+ProcessHandle.current().pid()+",\"run_id\":\""+id+"\"";
 }
 public static void emit(String object) {
  if(object==null || !object.startsWith("{") || !object.endsWith("}"))
   throw new IllegalArgumentException("Expected internal JSON object");
  System.out.println("DDT_PROBE_JSON={"+identityFields()+","+object.substring(1));
 }
}

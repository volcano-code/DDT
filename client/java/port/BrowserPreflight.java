package port;
/** Actual WebKit creation, in a disposable JVM; never enters the vendor client. */
public final class BrowserPreflight {
 public static void main(String[] args) {
  if(args.length!=0) throw new IllegalArgumentException("No preflight arguments allowed");
  RuntimeIntegrity.verify();
  try { BrowserHealth.require(); } finally { BrowserHealth.releaseOwned(); }
  System.out.println("DDT_NATIVE_PREFLIGHT_JSON={\"schema\":1,\"kind\":\"browser_preflight\",\"native_browser_created\":true,\"vendor_started\":false,\"pid\":"+ProcessHandle.current().pid()+"}");
 }
}

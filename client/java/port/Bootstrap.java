package port;
/** Phase telemetry around, not inside, the original client. No login/exit interception. */
public final class Bootstrap {
 private static void phase(String name) {
  System.out.println("DDT_STARTUP_EVENT={\"phase\":\"" + name + "\"}");
 }
 public static void main(String[] args) throws Exception {
  ExitRecording.start();
  phase("bootstrap_entered");
  Runtime.getRuntime().addShutdownHook(new Thread(() -> { WindowTelemetry.shutdownSnapshot(); phase("jvm_shutdown"); }, "ddt-phase-only"));
  RuntimeIntegrity.verify(); phase("integrity_verified");
  // Native preflight runs in a separate JVM under the Python supervisor.
  // Do not create and dispose a Display here before the vendor creates its own.
  System.out.println("LINUX_INTEGRITY_PASSED; entering original client Start.main; external native preflight is launcher-verified; not proof of login");
  Class<?> start=Class.forName("com.sd.ddt.windows.Start");
  phase("original_main_entered");
  WindowTelemetry.begin();
  try {
   start.getMethod("main",String[].class).invoke(null,(Object)args);
   WindowTelemetry.mainReturned();
   phase("original_main_returned");
  } catch(java.lang.reflect.InvocationTargetException ex) {
   phase("original_main_exception");
   throw new IllegalStateException("Original client startup failed",ex.getCause());
  } finally {
   WindowTelemetry.finish();
   BrowserHealth.releaseOwned();
   phase("owned_display_cleanup_completed");
  }
 }
}

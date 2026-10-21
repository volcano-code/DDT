package port;
/** Verify that an absent runtime manifest doesn't get a forged Windows-compatible success code. */
public final class GuardProbe {
 public static void main(String[] args){
  try{new com.sd.ddt.util.DependencyChecker().isMatchedLibs();throw new AssertionError("Guard unexpectedly passed");}
  catch(IllegalStateException expected){System.out.println("MISSING_RUNTIME_BLOCKED");}
  int expectedHeap=(int)Math.min(Integer.MAX_VALUE,Runtime.getRuntime().maxMemory()/1048576L);
  if(new com.sd.ddt.util.JavaEnvUtil().getCurrentJVM()!=expectedHeap)throw new AssertionError("Legacy heap-size API changed");
  System.out.println("LEGACY_HEAP_SIZE_SEMANTICS_PASS");
  try{BrowserHealth.require();throw new AssertionError("Windows compile API unexpectedly passed GTK health");}
  catch(IllegalStateException expected){
   if(!expected.getMessage().contains("Not the SWT GTK"))throw expected;
   System.out.println("WINDOWS_COMPILE_API_REJECTED_AS_LINUX_RUNTIME");
  }
 }
}

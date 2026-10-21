package port;

import org.eclipse.swt.SWT;
import org.eclipse.swt.widgets.Display;
import org.eclipse.swt.widgets.Shell;
import org.eclipse.swt.widgets.Listener;
import org.eclipse.swt.graphics.Rectangle;

/** Passive UI-thread observation. Never reads titles, URLs, controls, credentials or files.
 * Does not close windows, change event.doit, intercept System.exit or assert login.
 * A Shell visibility observation is NOT proof of the vendor login page or business readiness.
 */
public final class WindowTelemetry {
 private static volatile boolean active,returned,observed,failed;
 private static volatile int peak,closeEvents,disposeEvents;
 private static volatile boolean displaySizeKnown,displayMinimumMet;
 private static Display watched;
 private static Listener listener;
 private static Runnable ticker;
 private static int sequence;
 private WindowTelemetry() {}
 public static void begin() { active=true; }
 public static void mainReturned() { returned=true; }
 public static void watch(Display display) {
  if(!active || display==null || display.isDisposed() || display==watched)return;
  try {
   if(display.getThread()!=Thread.currentThread())throw new IllegalStateException("UI thread required");
   detach();watched=display;
   Rectangle b=display.getBounds();displaySizeKnown=true;
   displayMinimumMet=b.width>=1024 && b.height>=768;
   listener=e -> {
    if(!active || !(e.widget instanceof Shell))return;
    if(e.type==SWT.Close)closeEvents++;
    if(e.type==SWT.Dispose)disposeEvents++;
    // SWT.Show may be dispatched before visibility is committed. Sample in the
    // UI queue, and also periodically; never infer visibility from a Show label.
    if(!display.isDisposed())display.asyncExec(() -> sample(display));
   };
   display.addFilter(SWT.Show,listener);display.addFilter(SWT.Close,listener);display.addFilter(SWT.Dispose,listener);
   ticker=new Runnable(){public void run(){
    if(!active || display.isDisposed() || watched!=display)return;
    sample(display);
    if(active && !display.isDisposed())display.timerExec(100,this);
   }};
   display.timerExec(100,ticker);sample(display);emit("attached");
  } catch(RuntimeException ex){failed=true;emit("observer_error");}
 }
 private static void sample(Display display) {
  if(!active || display==null || display.isDisposed())return;
  try {
   int count=0;
   for(Shell shell:display.getShells())if(!shell.isDisposed() && shell.isVisible())count++;
   boolean first=!observed && count>0;
   if(count>0)observed=true;
   peak=Math.max(peak,count);
   if(first)emit("window_visible");
  } catch(RuntimeException ex){failed=true;}
 }
 private static void detach(){
  if(watched!=null && !watched.isDisposed()){
   if(watched.getThread()!=Thread.currentThread())throw new IllegalStateException("UI thread required");
   if(ticker!=null)watched.timerExec(-1,ticker);
   if(listener!=null){watched.removeFilter(SWT.Show,listener);watched.removeFilter(SWT.Close,listener);watched.removeFilter(SWT.Dispose,listener);}
  }
  watched=null;listener=null;ticker=null;
 }
 public static void finish(){
  sample(watched);active=false;
  try{detach();}catch(RuntimeException ex){failed=true;}
  emit("main_finished");
 }
 public static void shutdownSnapshot(){
  // Shutdown hooks do not access SWT: only the last observed primitive values.
  emit("shutdown");
 }
 private static synchronized void emit(String stage){
  if(sequence>=32)return;
  sequence++;
  System.out.println("DDT_WINDOW_OBSERVATION_JSON={"+ProbeEvidence.identityFields()+
   ",\"sequence\":"+sequence+",\"stage\":\""+stage+"\",\"source\":\"swt_shell_visibility\""+
   ",\"window_observed\":"+observed+",\"visible_shells_peak\":"+peak+
   ",\"close_events\":"+closeEvents+",\"dispose_events\":"+disposeEvents+
   ",\"main_returned\":"+returned+",\"observer_error\":"+failed+
   ",\"display_size_known\":"+displaySizeKnown+",\"display_minimum_met\":"+displayMinimumMet+
   ",\"vendor_login_verified\":false}");
 }
}

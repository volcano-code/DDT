package port;
import java.util.Locale;
import org.eclipse.swt.SWT;
import org.eclipse.swt.browser.Browser;
import org.eclipse.swt.widgets.Display;
import org.eclipse.swt.widgets.Shell;
/**
 * A positive answer requires actual WebKit creation. The early vendor environment
 * check occurs before LoginWindow obtains Display.getCurrent(). Do not dispose
 * that Display and cause another native initialization in the same JVM.
 * Only a Display created here is owned here; never dispose a borrowed vendor UI.
 */
public final class BrowserHealth {
 private static Display ownedDisplay;
 private static Display verifiedDisplay;
 private BrowserHealth() {}
 public static synchronized void require() {
  if(!System.getProperty("os.name","").toLowerCase(Locale.ROOT).contains("linux"))
   throw new IllegalStateException("Linux-only compatibility layer");
  if(!"gtk".equals(SWT.getPlatform()))
   throw new IllegalStateException("Not the SWT GTK runtime; Windows SWT is compile-only");
  Display display=Display.getCurrent();
  if(display==null && ownedDisplay!=null && !ownedDisplay.isDisposed())
   throw new IllegalStateException("Browser check must run on the owning SWT UI thread");
  boolean created=false;Shell shell=null;
  try {
   if(display==null){ display=new Display();ownedDisplay=display;created=true; }
   if(display==verifiedDisplay && !display.isDisposed())return;
   shell=new Shell(display);Browser browser=new Browser(shell,SWT.WEBKIT);
   if(!"webkit".equalsIgnoreCase(browser.getBrowserType()))
    throw new IllegalStateException("Unexpected browser backend");
   verifiedDisplay=display;
  } catch(Throwable ex) {
   if(shell!=null&&!shell.isDisposed())shell.dispose();
   if(created&&display!=null&&!display.isDisposed())display.dispose();
   if(created){ownedDisplay=null;verifiedDisplay=null;}
   throw new IllegalStateException("WebKitGTK native creation failed; Windows installer fallback blocked.",ex);
  } finally {
   if(shell!=null&&!shell.isDisposed())shell.dispose();
  }
  // Diagnostic shells have been disposed before observation starts.
  WindowTelemetry.watch(display);
 }
 /** Called on the original UI thread when the probe/client main returns. */
 public static synchronized void releaseOwned() {
  if(ownedDisplay!=null&&!ownedDisplay.isDisposed()) {
   if(ownedDisplay.getThread()!=Thread.currentThread())
    throw new IllegalStateException("Cannot release SWT Display from another thread");
   ownedDisplay.dispose();
  }
  ownedDisplay=null;verifiedDisplay=null;
 }
}

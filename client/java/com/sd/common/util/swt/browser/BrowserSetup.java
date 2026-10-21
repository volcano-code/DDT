package com.sd.common.util.swt.browser;
import java.io.File;
import java.nio.file.*;
import org.eclipse.swt.SWT;
import org.eclipse.swt.widgets.*;
import port.BrowserHealth;
import port.RuntimeIntegrity;
/** Independent Linux environment setup; never downloads or executes a Windows runtime. */
public class BrowserSetup {
 public BrowserSetup() {}
 public static void showMsg(String title,String message) {
  Display d=Display.getCurrent();if(d==null){System.err.println(title+": "+message);return;}
  Shell s=new Shell(d);try{MessageBox box=new MessageBox(s,SWT.OK|SWT.ICON_INFORMATION);box.setText(title);box.setMessage(message);box.open();}finally{s.dispose();}
 }
 public static boolean checkBrowserEnv() {
  try {Files.createDirectories(RuntimeIntegrity.root().resolve("session/cache"));}
  catch(Exception ex){throw new IllegalStateException("Cannot create Linux cache",ex);}
  BrowserHealth.require();return true;
 }
 public static boolean checkBrowserCreation() {BrowserHealth.require();return true;}
 public static void checkBrowserType() {BrowserHealth.require();}
 public static boolean deleteDirectory(File directory) {
  // Legacy cleanup is deliberately disabled: it may refer to shared/user data.
  // A failed deletion is reported honestly. No synthetic 'deleted' result.
  return false;
 }
 public static void main(String[] args) {try {checkBrowserEnv();} finally {BrowserHealth.releaseOwned();}}
}

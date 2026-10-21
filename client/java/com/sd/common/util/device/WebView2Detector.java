package com.sd.common.util.device;
import port.BrowserHealth;
/** Legacy method name retained for binary compatibility; verifies WebKitGTK rather than WebView2. */
public class WebView2Detector {
 public WebView2Detector() {}
 public static boolean isWebView2Installed() {BrowserHealth.require();return true;}
 public static void main(String[] args) {try {BrowserHealth.require();System.out.println("LINUX_WEBKIT_NATIVE_CREATION_PASS");} finally {BrowserHealth.releaseOwned();}}
}

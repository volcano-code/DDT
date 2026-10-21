package com.sd.ddt.util;
import java.nio.file.*;
import port.RuntimeIntegrity;
/** Replaces Windows/reflective classpath mutation with the verified launch-time classpath. */
public class JavaEnvUtil {
 public JavaEnvUtil() {}
 public void loadJar() {RuntimeIntegrity.verify();}
 public void addJarToClasspath() throws Exception {RuntimeIntegrity.verify();}
 public static void addJarToClasspath(Path path) throws Exception {
  RuntimeIntegrity.verify();
  Path wanted=path.toRealPath(); boolean found=false;
  for(String part:System.getProperty("java.class.path","").split(java.io.File.pathSeparator))
   if(Files.exists(Path.of(part))&&Path.of(part).toRealPath().equals(wanted)) found=true;
  if(!found) throw new UnsupportedOperationException("Dynamic classpath insertion is not supported; rebuild the verified Linux launch manifest");
 }
 public static void loadJarAsModule(String path,String module) {throw new UnsupportedOperationException("Dynamic modules are not supported by the port");}
 public void addLibraryDir(String ignoredWindowsDirectory) throws Exception {
  RuntimeIntegrity.verify(); // Linux SWT extracts its own Linux natives; do not insert a Windows DLL directory.
  if(!Files.isRegularFile(RuntimeIntegrity.root().resolve("native/swt.jar")))throw new IllegalStateException("Missing Linux SWT");
 }
 public int getCurrentJVM() {return (int)Math.min(Integer.MAX_VALUE,Runtime.getRuntime().maxMemory()/1048576L);}
 public static void main(String[] args) {new JavaEnvUtil().loadJar();}
}

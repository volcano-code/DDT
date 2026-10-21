package com.sd.ddt.util;
import port.RuntimeIntegrity;
/** File-integrity compatibility check only. Original CheckUtil/licence/account code is not replaced. */
public class DependencyChecker {
 public DependencyChecker() {}
 public static void main(String[] args) {new DependencyChecker().isMatchedLibs();}
 public int isMatchedLibs() {RuntimeIntegrity.verify();return 100;}
}

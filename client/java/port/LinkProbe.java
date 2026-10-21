package port;
import java.nio.file.*;
import java.util.*;
import java.util.jar.*;
/** Non-initializing class load. NOT a native execution, login, or GUI test. */
public final class LinkProbe {
 public static void main(String[] args)throws Exception {
  int ok=0,failed=0; Map<String,Integer> origins=new TreeMap<>();
  try(JarFile jar=new JarFile(args[0])){
   var names=jar.stream().map(JarEntry::getName).filter(n->n.endsWith(".class")&&!n.equals("module-info.class")).sorted().toList();
   for(String name:names){
    try{Class<?> type=Class.forName(name.substring(0,name.length()-6).replace('/','.'),false,LinkProbe.class.getClassLoader());
        String file=Path.of(type.getProtectionDomain().getCodeSource().getLocation().toURI()).getFileName().toString();
        origins.merge(file,1,Integer::sum);ok++;}
    catch(Throwable t){failed++;System.out.println("LINK_FAILURE "+name+" "+t.getClass().getName());}
   }
  }
  System.out.println("NON_INITIALIZING_LINK_RESULT loaded="+ok+" failed="+failed+"; no static initialization or GUI");
  System.out.println("CLASS_SOURCES "+origins+"; counts use original class-name list, including compatibility replacements");
  if(failed>0)System.exit(3);
 }
}

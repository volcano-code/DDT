package port;

import org.eclipse.swt.SWT;
import org.eclipse.swt.graphics.*;
import org.eclipse.swt.layout.FillLayout;
import org.eclipse.swt.widgets.*;

/** Real GTK-only stage, intentionally independent of WebKit and vendor login. */
public final class GtkProbe {
 public static void main(String[] args) throws Exception {
  RuntimeIntegrity.verify();
  if (!"gtk".equals(SWT.getPlatform())) throw new IllegalStateException("Genuine SWT GTK required");
  Display display = new Display();
  int selections = 0;
  try {
   for (int cycle = 0; cycle < 2; cycle++) {
    Shell shell = new Shell(display);
    shell.setText("Linux GTK 窗口测试 — 非典当通业务窗口");
    shell.setLayout(new FillLayout()); shell.setSize(760, 340);
    final boolean[] selected = {false};
    Button button = new Button(shell, SWT.PUSH);
    button.setText("中文 / 000012 / 10600.00 — GTK 事件与窗口生命周期测试");
    button.addListener(SWT.Selection, event -> selected[0] = true);
    shell.open();
    try {
     display.timerExec(100, () -> {if (!button.isDisposed()) button.notifyListeners(SWT.Selection,new Event());});
     long end = System.nanoTime() + 4_000_000_000L;
     while (!selected[0] && !shell.isDisposed() && System.nanoTime() < end) {
      if (!display.readAndDispatch()) Thread.sleep(10);
     }
     if (!selected[0] || !shell.isVisible() || button.getSize().x <= 0) throw new IllegalStateException("GTK event/layout check failed");
     if (!button.getText().contains("000012 / 10600.00")) throw new IllegalStateException("GTK text mismatch");
     selections++;
     if (cycle == 1 && args.length == 1) {
      Rectangle bounds = shell.getBounds(); Image image = new Image(display,bounds.width,bounds.height);
      GC gc = new GC(display);
      try {gc.copyArea(image,bounds.x,bounds.y);ImageLoader loader=new ImageLoader();loader.data=new ImageData[]{image.getImageData()};loader.save(args[0],SWT.IMAGE_PNG);}
      finally {gc.dispose();image.dispose();}
     }
    } finally {if (!shell.isDisposed()) shell.dispose();}
   }
   if (display.getShells().length != 0) throw new IllegalStateException("GTK shell leaked");
   System.out.println("GTK_WIDGET_LIFECYCLE_PASS");
   ProbeEvidence.emit("{\"kind\":\"gtk\",\"window_cycles\":2,\"events\":"+selections+",\"shells_remaining\":0,\"synthetic_only\":true,\"webkit_tested\":false,\"vendor_login\":false}");
  } finally {if (!display.isDisposed()) display.dispose();}
 }
}

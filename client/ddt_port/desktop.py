"""Native Linux deployment launcher. This is NOT the vendor's login window.

Tk is used only for this installer/diagnostic UI; the actual client continues to
require the real SWT GTK / WebKit runtime. Work happens in one cancellable worker
so the UI remains responsive. Credentials are never requested here.
"""
from __future__ import annotations

import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__, runtime, validation, dependencies, toolchain, upgrade


class Launcher:
    def __init__(self, window: tk.Tk, root: Path | None = None, installer: Path | None = None):
        self.window = window
        self.busy = False
        self.cancel = threading.Event()
        self.messages = queue.Queue()
        self.last_result = None
        self.completed_actions = []
        self.closed = False
        self.buttons = {}
        self.root_var = tk.StringVar(value=str(root or Path.home() / '.local/share/ddt-linux-port/client-v9-test'))
        self.installer_var = tk.StringVar(value=str(installer) if installer else '')
        self.sdk_var = tk.StringVar()
        self.status_var = tk.StringVar(value='尚未检测 · 先选择目录，然后检查环境')
        self.summary_var = tk.StringVar(value='原客户端窗口、登录和业务流程：尚未验证')
        window.title(f'典当通 Linux 移植控制台 · {__version__}')
        window.geometry('1140x960')
        window.minsize(1000, 760)
        window.configure(bg='#eef2f6')
        style = ttk.Style(window)
        style.theme_use('clam')
        style.configure('.', font=('Noto Sans CJK SC', 10), background='#eef2f6')
        style.configure('TButton', padding=(12, 7))
        style.configure('Primary.TButton', background='#164e63', foreground='white')
        style.configure('Treeview', rowheight=25, background='white', fieldbackground='white')
        style.configure('Treeview.Heading', padding=(6, 6), font=('Noto Sans CJK SC', 10, 'bold'))
        style.configure('Card.TFrame', background='white')
        style.configure('Card.TLabel', background='white')
        self._layout()
        window.protocol('WM_DELETE_WINDOW', self.close)
        self._after_id = window.after(70, self._poll)

    def _layout(self):
        header = tk.Frame(self.window, bg='#123345', padx=26, pady=17)
        header.pack(fill='x')
        tk.Label(header, text='典当通  /  LINUX 移植控制台', font=('Noto Sans CJK SC', 21, 'bold'),
                 bg='#123345', fg='white').pack(anchor='w')
        tk.Label(header, text=f'{__version__}  ·  实验性部署与诊断工具  ·  不收集账号或密码',
                 font=('Noto Sans CJK SC', 10), bg='#123345', fg='#c2d9e6').pack(anchor='w', pady=(4, 0))
        warn = tk.Label(self.window, text='这是移植启动器，不是典当通登录页。原生窗口、账号登录、业务导出须分别验收。',
                        bg='#fff3d6', fg='#744d08', font=('Noto Sans CJK SC', 10), anchor='w', padx=24, pady=9)
        warn.pack(fill='x')
        body = ttk.Frame(self.window, padding=(24, 15))
        body.pack(fill='both', expand=True)
        config = ttk.LabelFrame(body, text=' 01  本地文件与运行目录 ', padding=12)
        config.pack(fill='x')
        self.entries = []
        for r, (label, var, kind) in enumerate([
            ('独立运行目录', self.root_var, 'root'),
            ('原始安装包', self.installer_var, 'installer'),
            ('签名 SWT JAR', self.sdk_var, 'sdk')
        ]):
            ttk.Label(config, text=label, width=13).grid(row=r, column=0, sticky='w', pady=3)
            entry = ttk.Entry(config, textvariable=var)
            entry.grid(row=r, column=1, sticky='ew', padx=(8, 10), pady=3)
            self.entries.append(entry)
            button = ttk.Button(config, text='选择…', command=lambda k=kind: self._choose(k))
            button.grid(row=r, column=2, pady=3)
            self.buttons['select-' + kind] = button
        config.columnconfigure(1, weight=1)
        actions = ttk.Frame(body)
        actions.pack(fill='x', pady=(12, 9))
        for text, name in [('检查环境', 'doctor'), ('准备 / 安装预编译层', 'setup'), ('安装 SWT＋兼容层', 'install-swt'),
                           ('离线分层自检', 'self-test'), ('导出最小诊断', 'support')]:
            b = ttk.Button(actions, text=text, command=lambda n=name: self._action(n),
                           style='Primary.TButton' if name == 'self-test' else 'TButton')
            b.pack(side='left', padx=(0, 7))
            self.buttons[name] = b
        dependency_actions=ttk.Frame(body)
        dependency_actions.pack(fill='x',pady=(0,8))
        for i,(label,name) in enumerate([('保存已验证环境','configure'),('升级预览','upgrade-plan'),('应用整合升级…','upgrade-apply'),('查看依赖方案','deps'),('下载签名 SWT…','fetch-swt'),('仅测 GTK','gtk')]):
            button=ttk.Button(dependency_actions,text=label,command=lambda n=name:self._action(n))
            button.grid(row=i//3,column=i%3,sticky='ew',padx=(0,7),pady=3);self.buttons[name]=button
            dependency_actions.columnconfigure(i%3,weight=1)
        ttk.Label(dependency_actions,text='内部开发里程碑，非可安装发布候选；本工具不会执行 sudo。').grid(row=2,column=0,columnspan=3,sticky='w')
        self.stop_button = ttk.Button(actions, text='停止运行', command=self.stop, state='disabled')
        self.stop_button.pack(side='right')
        ttk.Label(body, textvariable=self.status_var, font=('Noto Sans CJK SC', 11, 'bold')).pack(anchor='w', pady=(1, 5))
        self.tabs = ttk.Notebook(body)
        self.tabs.pack(fill='both', expand=True)
        checks_tab = ttk.Frame(self.tabs, padding=8)
        log_tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(checks_tab, text=' 环境与验收状态 ')
        self.tabs.add(log_tab, text=' 本地执行结果 ')
        self.checks = ttk.Treeview(checks_tab, columns=('status', 'component', 'action'), show='headings', height=9)
        for key, text, width in [('status', '状态', 76), ('component', '检查项', 205), ('action', '缺项处理 / 说明', 660)]:
            self.checks.heading(key, text=text)
            self.checks.column(key, width=width, minwidth=width if key != 'action' else 240, stretch=key == 'action')
        self.checks.tag_configure('passed', foreground='#166534')
        self.checks.tag_configure('blocked', foreground='#9a3412')
        scroll = ttk.Scrollbar(checks_tab, orient='vertical', command=self.checks.yview)
        scroll.pack(side='right', fill='y')
        self.checks.configure(yscrollcommand=scroll.set)
        self.checks.pack(side='left', fill='both', expand=True)
        self.log = tk.Text(log_tab, wrap='word', font=('DejaVu Sans Mono', 10), bg='white', relief='flat')
        self.log.pack(fill='both', expand=True)
        self.log.insert('end', '只在本地保存结果；诊断包不会包含原始客户端日志或业务数据。\n')
        self.log.configure(state='disabled')
        bottom = ttk.Frame(body)
        bottom.pack(side='bottom', fill='x', pady=(12, 0), before=self.tabs)
        ttk.Label(bottom, textvariable=self.summary_var, foreground='#475569').pack(side='left')
        for label, name in [('原客户端离线启动', 'launch-offline'), ('授权联网启动…', 'launch-online')]:
            b = ttk.Button(bottom, text=label, command=lambda n=name: self._action(n))
            b.pack(side='right', padx=(8, 0))
            self.buttons[name] = b

    def _choose(self, kind: str):
        if kind == 'root':
            value = filedialog.askdirectory(title='选择已准备目录；新目录也可直接在输入框填写')
            if value:
                self.root_var.set(value)
        else:
            value = filedialog.askopenfilename(title='选择原安装包' if kind == 'installer' else '选择固定签名 SWT JAR',
                                              filetypes=[('Installer', '*.exe')] if kind == 'installer' else [('Signed SWT JAR', '*.jar')])
            if value:
                (self.installer_var if kind == 'installer' else self.sdk_var).set(value)

    def _action(self, name: str):
        if name=='fetch-swt' and not messagebox.askyesno('确认下载公开依赖',
                '将连接 Eclipse 官方 HTTPS 地址，下载并校验同版本签名 SWT JAR。\n不连接典当通，不发送账号或业务数据；准备好的运行目录会以同一事务安装该依赖并升级自写兼容层，不修改会话。\n确认继续？'):
            return
        if name == 'upgrade-apply' and not messagebox.askyesno('内部开发里程碑，非发布候选', '此版本尚未通过云端图形及目标 Ubuntu 验收。将备份并更新白名单工具文件，不修改会话。仅在开发测试环境继续？'):
            return
        if name == 'support':
            value = filedialog.asksaveasfilename(title='保存最小诊断包（不含原始日志）',
                                                defaultextension='.zip', initialfile=f'ddt-diagnostics-{time.time_ns()}.zip')
            if value:
                self.submit(name, output=Path(value))
            return
        if name.startswith('launch-'):
            online = name == 'launch-online'
            text = ('此操作会运行原客户端并允许其正常联网。\n' if online else '此操作只尝试原客户端离线启动。\n')
            text += '需要独立测试环境和相应授权；本适配层不是只读权限隔离器。\n确认继续？'
            if not messagebox.askyesno('确认实验性客户端启动', text):
                return
        self.submit(name)

    def submit(self, name: str, *, output: Path | None = None):
        if self.busy:
            return
        root_text = self.root_var.get().strip()
        if not root_text:
            self.status_var.set('请选择独立运行目录')
            return
        root = Path(root_text).expanduser().absolute()
        installer_text = self.installer_var.get().strip()
        sdk_text = self.sdk_var.get().strip()
        source = Path(installer_text).expanduser() if installer_text else None
        sdk = Path(sdk_text).expanduser() if sdk_text else None
        self.busy = True
        self.cancel.clear()
        self.status_var.set('正在执行：' + name + '（本地）')
        for b in self.buttons.values():
            b.configure(state='disabled')
        for e in self.entries:
            e.configure(state='disabled')
        self.stop_button.configure(state='normal')
        def worker():
            try:
                if name=='configure':
                    result=toolchain.configure(root)
                elif name=='upgrade-plan':
                    result=upgrade.plan(root)
                elif name=='upgrade-apply':
                    result=upgrade.apply_upgrade(root)
                elif name=='deps':
                    result=dependencies.plan(candidates=True)
                elif name=='fetch-swt':
                    receipt=runtime.fetch_swt(root,cancel=self.cancel)
                    result={'status':'installed','transaction':receipt,'diagnostics':validation.diagnose(root)}
                elif name=='gtk':
                    result=runtime.probe(root,'gtk',cancel=self.cancel)
                    result['vendor_window']='not_tested'
                elif name == 'doctor':
                    result = validation.diagnose(root)
                elif name == 'setup':
                    result = validation.setup(root, source, sdk, self.cancel)
                elif name == 'install-swt':
                    if sdk is None:
                        raise ValueError('请先选择校验匹配的签名 SWT JAR 文件')
                    receipt=runtime.install_swt(root, sdk)
                    result = {'status': 'installed','transaction':receipt, 'diagnostics': validation.diagnose(root)}
                elif name == 'self-test':
                    result = validation.self_test(root, cancel=self.cancel)
                elif name == 'support':
                    if output is None:
                        raise ValueError('请选择新的输出 ZIP 文件')
                    result = validation.support_bundle(root, output)
                elif name in ('launch-offline', 'launch-online'):
                    result = runtime.run_java(root, 'port.Bootstrap', [], headless=False,
                                              offline=name != 'launch-online', timeout=0, cancel=self.cancel)
                    result['vendor_login'] = 'not_verified_by_launcher'
                else:
                    raise ValueError('Unknown launcher action')
                self.messages.put((name, result, None))
            except runtime.DownloadCancelled:
                self.messages.put((name,{'status':'cancelled'},None))
            except Exception as ex:
                self.messages.put((name, None, f'{type(ex).__name__}: {ex}'))
        threading.Thread(target=worker, name='ddt-port-operation', daemon=False).start()

    def _poll(self):
        if self.closed:
            return
        try:
            name, result, error = self.messages.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.last_result = result if error is None else {'status': 'blocked', 'error': error}
            self.completed_actions.append(name)
            for b in self.buttons.values():
                b.configure(state='normal')
            for e in self.entries:
                e.configure(state='normal')
            self.stop_button.configure(state='disabled')
            diag = result.get('diagnostics', result) if result else {}
            if 'checks' in diag:
                self._show_checks(diag)
            if name=='deps' and not error:
                status='系统依赖方案已生成 · 未执行安装、未启动客户端'
                self.tabs.select(1)
            elif error:
                status = '已阻断 · ' + error[:110]
            elif result.get('overall') == 'headless_passed_native_pending':
                status = '无界面测试通过 · 原生浏览器未通过 / 待补条件'
            elif result.get('overall') == 'incomplete':
                status = '自检未完成 / 已停止 · 没有记录为通过'
            elif result.get('overall') == 'local_platform_passed':
                status = '本地平台测试通过 · 厂商登录与业务仍未验收'
            elif result.get('cancelled') or result.get('status') == 'cancelled':
                status = '已停止 · 没有记录为测试通过'
            elif result.get('checks') and not result.get('static_ready'):
                status = '检测完成 · 存在阻塞项'
            elif result.get('startup',{}).get('classification')=='startup_error':
                status='原客户端启动异常 · 退出码0也不代表通过'
            elif result.get('returncode') not in (0, None) or result.get('timed_out'):
                status = '运行失败 / 超时 · 查看本地执行结果'
            else:
                status = '操作完成 · 不代表原客户端登录成功'
            self.status_var.set(status)
            self.log.configure(state='normal')
            self.log.delete('1.0', 'end')
            self.log.insert('end', json.dumps(self.last_result, ensure_ascii=False, indent=2))
            self.log.configure(state='disabled')
        self._after_id = self.window.after(70, self._poll)

    def _show_checks(self, report: dict):
        for item in self.checks.get_children():
            self.checks.delete(item)
        for item in report['checks']:
            status = item['status']
            self.checks.insert('', 'end', iid=item['code'], values=(
                '通过' if status == 'passed' else '无需' if status=='not_required' else '阻断', item['label'], item['action'] or '静态条件满足'), tags=(status,))

    def stop(self):
        self.cancel.set()
        self.status_var.set('正在停止 · 编译 / 提取完成当前安全步骤后退出')

    def close(self):
        if self.busy:
            if messagebox.askyesno('操作仍在运行', '先停止当前操作；完成退出后再关闭窗口？'):
                self.stop()
            return
        self.closed = True
        self.window.after_cancel(self._after_id)
        self.window.destroy()


def launch_gui(root: Path | None = None, installer: Path | None = None) -> None:
    window = tk.Tk()
    Launcher(window, root, installer)
    window.mainloop()

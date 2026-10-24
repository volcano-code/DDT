# 会计辅助工作台：完整公开源码入口

版本基线 `0.2.1-dev.1`。FastAPI + 原生 TypeScript + SQLite；不是典当通官方客户端，也未连接厂商 API。使用合成或获准脱敏数据，不直接承担正式记账。

## 从源码运行

在普通 Linux 用户下进入本目录，Python 3.10+、Node/npm 可用后：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci --ignore-scripts --prefix frontend
npm run build --prefix frontend
bash scripts/start.sh
```

服务只监听 `127.0.0.1:8000`。在本机浏览器打开该地址；首次启动随机生成演示身份，令牌仅写入本地 `state/access-tokens.txt`。使用 reviewer 对应值登录，不要分享或提交 `state/`。`DDT_STATE_DIR` 可指定独立状态目录；`PORT` 可指定本机端口。

前端编译产物不提交 Git，必须先构建。启动脚本可以从其他工作目录调用，并在缺少编译产物时明确停止；不会自动安装软件、调用模型或登录典当通。联网冷安装依赖仍须在有网络的目标环境验证，不把开发环境预装依赖等同于冷安装通过。

## 合成样例

```bash
.venv/bin/python scripts/generate_public_fixtures.py
```

从可阅读的 `scripts/sample_data.py` 生成12份小型静态值 CSV/XLS/XLSX/JSON 样例。没有编码隐藏的商业二进制、客户数据、宏或外链执行。`synthetic_xls.py` 仅是小型测试样例写入器，不是生产报表导出器。重复生成字节一致；目标已有不同内容或符号链接时拒绝覆盖。旧样例目录有差异时应使用 `--output` 指向新的空测试目录，不删除实际业务资料。

演示业务及资金各9条；另提供2条业务/1笔合并收款。所有账号、标识、金额和科目仅用于合成演示，不是企业核算政策。

## 可重复测试

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/generate_public_fixtures.py
.venv/bin/python -m pytest -q
.venv/bin/python scripts/http_smoke_public.py --report /tmp/ddt-http-new.json
```

HTTP检查实际启动服务，测试上传、复核、下载、更正失效、重启和任务恢复；不是 TestClient 替代网络的测试。报告路径必须是新的，测试数据库和身份均临时生成并清理。

浏览器测试另行执行，不能用 HTTP 检查代替：

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/browser_smoke.py --report /tmp/ddt-browser-new.json
```

默认用 Playwright 对应 Chromium；只在明确测试已安装浏览器时使用 `--chromium /绝对路径`。测试必须普通用户运行，显式开启 Chromium 沙箱；不修改浏览器管理策略、不用模拟接口或绕过 CSP。页面被策略阻断时返回失败/阻塞，不能算界面通过。报告只含白名单阶段和计数，不发布令牌、原始日志、数据库或截图。

## 功能边界

导入、确定性核对、分配守恒、来源追溯、人工审核、只读任务助手和辅助凭证草稿已经实现。模型默认不连接；协议测试中的模拟模型响应不是真实推理验收。凭证 reviewed 不是正式 posted；没有付款、报税或财务系统写入接口。

XLS仍是受限静态值BIFF8路径，复杂SST续块、合并、公式或未知模板会拒绝；生成合成XLS成功不证明真实典当通报表兼容。真实模板、来源完整性、真实模型、企业权限与生产安全需独立验收。

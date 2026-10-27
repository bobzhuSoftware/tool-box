# ServiceNow 数据表（记录数采集）

通过 Edge 自动化会话读取 DSV ServiceNow 列表的记录数，按行/列填入表格。

## 功能与文件

| 部分 | 文件 |
|---|---|
| Worker（Playwright 子进程） | `servicenow_worker.py` |
| 后端路由 | `app/routers/servicenow.py`（在 `app/main.py` 注册） |
| 前端页面 | `frontend/src/ServiceNowTables.jsx` / `.css`，入口卡片在 `HomePage.jsx`，路由在 `App.jsx`（tool id `servicenow`） |

- 表格定义按用户保存在 `UserSetting`（key `servicenow_tables`）：每个表格有标题、行名、列名，以及 `"行,列" -> 列表 URL` 的映射。
- 默认表格 `All`：行 `Blue/Red`，列 `Opened/Unassigned Requested/Unassigned Pre-Sales`，仅 `(0,0)` 配了链接（`Cases Received in past 7 days Blue`）。
- 前端点“刷新数据”后，只读取当前表格里有链接的单元格；结果通过 SSE 逐个返回并实时填入。上次结果缓存在 `localStorage`（`sn_counts`）。
- “复制表格”会同时写入 HTML 和制表符文本，可粘贴到 Excel / Outlook / Teams。
- 新增单元格只需在“编辑表格”里填该格的列表 URL，无需改代码。

### Worker 协议

```
python servicenow_worker.py count <url> [<url> ...]
python servicenow_worker.py count --file <json 列表>   # 后端使用
```

stdout：`STATUS:` 进度，`RESULT:{url,count,source[,error]}` 每个 URL 一条，`DONE:` 结束，`ERROR:` 失败。`count` 为 `null` 表示该格读取失败。

### 后端接口

- `GET/POST /api/servicenow/config`：读取/保存表格定义。
- `POST /api/servicenow/run/stream {urls:[...]}`：SSE，事件类型 `status` / `result` / `done` / `error`。
  - 只接受 `https://*.service-now.com`（防止被当作任意 URL 打开器）。
  - 同一时间只允许一个任务（共用同一个 Edge profile），第二个请求返回 409。

## 取数方式

URL 形如 `.../x/dsva2/<app>/simplelist/<table>/params/list-title/<title>/query/<encoded query>`。

1. 从 URL 解析出表名 `<table>` 和 encoded query（URL 解码后即可直接用）。
2. 在已登录页面内执行
   `fetch('/api/now/stats/<table>?sysparm_count=true&sysparm_query=<query>')`，带上页面自己的 `X-UserToken`（拦截请求头或读 `window.g_ck`）。
   - `javascript:gs.beginningOfLast7Days()` 这类写法服务端可以直接解析。
   - 能在界面看到这个列表就有读权限。
3. 兜底：REST 失败时读 DOM 里列表标题后面的数字徽章（会穿透 shadow DOM，对改版较脆弱）。

已验证：REST 结果为 13，与页面徽章一致。

## 登录：经验与坑

登录链路：`dsv.service-now.com` → `sso.dsv.com`（Keycloak）→ `sts.dsv.com`（ADFS）→ Entrust 一次性口令（2FA）。

1. **不能复用日常 Edge profile**
   - Edge 运行时对 `Network/Cookies` 独占加锁，无法复制。
   - Edge 136+ 不再响应对默认数据目录的调试端口/管道。
   - 因此使用专用自动化 profile：`%LOCALAPPDATA%\VideoTranscript\edge-automation`（与 Teams worker 共用，见 `browser_utils.ensure_automation_profile`），登录一次后复用。
2. **`HeadlessChrome` UA 会让 ADFS 退化成密码表单**（本次的真正根因）
   - 无头模式的 UA 含 `HeadlessChrome`，ADFS 不再做 Windows 静默登录（WIA），而是弹“Sign in”密码表单；用户在可见窗口里登录后，后续无头运行又被要求登录，最终出现
     “For security reasons, we require additional information to verify your account / Access denied”。
   - 修复（`_launch`）：
     - 通过 CDP `Emulation.setUserAgentOverride` 把 UA 里的 `HeadlessChrome` 改成 `Chrome`；
     - `ignore_default_args=["--enable-automation"]`；
     - 加 `--disable-blink-features=AutomationControlled`。
   - 对比实验：默认无头 UA → 密码表单；改 UA 后 → 静默 WIA，只剩 Entrust 口令页。
3. **会话 cookie 不会被 Edge 持久化**
   - Keycloak / ADFS / ServiceNow 的会话 cookie 没有过期时间，Edge 关闭后丢失，所以每次运行都会重新要求登录。
   - 因此登录成功后和每次读取完成后，把 `dsv.com` / `service-now.com` / `microsoftonline.com` 域的 cookie 存到 `%LOCALAPPDATA%\VideoTranscript\servicenow-session.json`，下次启动时恢复（`_save_session` / `_restore_session`）。
   - **该文件等同于登录凭据，勿提交、勿共享。**
4. **登录流程**：先无头检查（`HEADLESS_SSO_SECS` = 30 秒）；失败则弹出可见 Edge 窗口，等待最多 `LOGIN_WAIT_SECS` = 300 秒（给 2FA 留时间），检测到进入 `service-now.com` 后自动继续。
5. 账号要选 DSV 账号（本机 Edge `Profile 1`）；后端会把用户设置里的 `edge_profile` 传给 worker 的 `VT_EDGE_PROFILE`。

## 本地测试

- 前端开发：`npm run dev`（后端 8000，前端 5173）；或 `npm run app`（桌面窗口）。
- 首页点“ServiceNow 数据表” → “刷新数据”。首次会弹出 Edge 窗口，登录并输入 2FA 口令。
- 直接跑 worker（PowerShell）：

```powershell
$env:VT_EDGE_PROFILE='Profile 1'; $env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe servicenow_worker.py count "<列表 URL>"
```

- 排查登录问题：用可见窗口手动打开 `https://dsv.service-now.com/`，观察跳转到哪一步（Keycloak / ADFS / Entrust）。
- 开发时 `uvicorn --reload` 会监视项目目录，不要把临时测试脚本放进仓库目录（放 `$env:TEMP`）。

## 后续扩展提示

- 取数口径（比如 `Unassigned Requested`）不同的格子，只要给出对应的 ServiceNow 列表 URL 即可；如果不是 `simplelist` 页面，需要在 `parse_list_url` 里增加解析，或改为直接填表名 + encoded query。
- 如果公司策略要求更频繁的重新验证，会再次弹出登录窗口，属于预期行为。
- 若要完全脱离浏览器，可向 ServiceNow 管理员申请只读 API 账号（OAuth）。

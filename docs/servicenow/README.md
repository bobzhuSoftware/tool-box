# ServiceNow 数据表（记录数采集）

通过 Edge 自动化会话读取 DSV ServiceNow 列表的记录数，按行/列填入表格，可一键刷新并复制。支持多个互相独立的登录身份。

文档目录：

- 本文：功能、文件、接口、取数方式、测试。
- [authentication.md](authentication.md)：登录链路、会话与浏览器状态如何保存、登录身份、排查与重置。
- [experiments.md](experiments.md)：所有实验的过程与结果（含已被证伪的假设），改动登录相关代码前先看这篇。

## 文件

| 部分 | 文件 |
|---|---|
| Worker（Playwright 子进程） | `servicenow_worker.py` |
| 后端路由 | `app/routers/servicenow.py`（在 `app/main.py` 注册） |
| 前端页面 | `frontend/src/ServiceNowTables.jsx` / `.css`；入口卡片在 `HomePage.jsx`，路由在 `App.jsx`（tool id `servicenow`） |
| 常用链接、公式、组合条件 | `frontend/src/ServiceNowLinks.jsx`、`LinkCellInput.jsx`、`LinkComposer.jsx`、`useSnLinks.js`、`formula.js`；后端 `app/core/sn_query.py` |

## 功能

- **表格**：定义按软件用户保存在 `UserSetting`（key `servicenow_tables`）：标题、行名、列名，以及 `"行,列" -> 列表 URL`。默认表格 `All`：行 `Blue/Red`，列 `Opened/Unassigned Requested/Unassigned Pre-Sales`，仅 `(0,0)` 配了链接（`Cases Received in past 7 days Blue`）。
- **刷新数据**：只读取当前表格里有链接的单元格；结果通过 SSE 逐个返回并实时填入。数字可点击，在 ServiceNow 打开对应列表；失败的格显示 `⚠ 原因`，工具栏出现“重试失败项”。
- **复制结果**：同时写入 HTML 和制表符文本，可粘贴到 Excel / Outlook / Teams。
- **编辑内容**：只作用于当前表格，可改表格标题、行名、列名和各格列表 URL（格式不对会标红，刷新时跳过）。没有“保存”按钮，改动停止输入约 0.6 秒后自动保存，工具栏显示保存状态，失败可点击重试。切换表格或点刷新会退出编辑态。
- **表格管理**：标签旁的 `⋯` 菜单（重命名、复制一份、删除），双击标签也可重命名；新建表格先输入名称（默认自动编号，如“新表格 2”），默认 2×2，直接进入编辑。删除含链接的行或列会先确认。
- **常用链接**：登录身份栏下面一条默认折叠的收藏列表（`🔖 常用链接 (N)`），按软件用户保存（key `servicenow_links`），每条有名称、链接、备注和可选的缩写。可打开、复制、编辑、删除。
- **单元格引用与公式**：编辑内容时，单元格可以填普通 URL，也可以引用常用链接：输入 `=` 开头的公式 `=[PB]+[PR]`（`[缩写]` 是链接上设置的缩写，支持加法、括号、整数），或用 🔖 按钮整格引用某条链接。设计见 [link-references.md](link-references.md)。
- **组合条件**：常用链接里的「组合」可以把几条链接的筛选条件勾选后重组成新链接，经典或 simplelist 格式任选，见 [condition-composer.md](condition-composer.md)。
- **数字来源与可信度**：数字悬停显示来源（REST 接口 / 页面）。REST 返回 0、页面又没能确认时显示橙色的 `0 ⚠`，说明见 [count-consistency-check.md](count-consistency-check.md)。
- **登录身份**：页面顶部的下拉框。每个身份有独立的 Edge 数据目录和 cookie 文件，可添加、重命名、删除、清除登录状态、重新检查登录，并显示当前登录的用户名。数字缓存（浏览器 `localStorage`，key `sn_counts:<身份id>`）按身份分开。表格定义（链接）在各身份之间共用。
- 目前**所有身份实际都会以运行服务那台电脑的 Windows 用户登录**，见 [authentication.md](authentication.md) 的“登录身份的限制”。

## Worker 协议

```
python servicenow_worker.py count <url> [<url> ...]
python servicenow_worker.py count --file <json 列表>   # 后端使用
python servicenow_worker.py login                        # 只登录并读取身份
```

环境变量（后端按身份设置）：`VT_EDGE_AUTOMATION_DIR`（该身份的 Edge 数据目录）、`VT_SN_SESSION_FILE`（该身份的 cookie 文件）、`VT_EDGE_PROFILE`（只决定从哪个真实 Edge profile 复制初始配置文件）。

stdout：

- `STATUS:<文本>`：进度。
- `STAGE:check|window|working`：阶段标记，后端转成 SSE `stage` 事件。`check` 是用已保存的登录静默检查，`window` 是需要用户在弹出的 Edge 窗口里登录，`working` 是已登录。前端只在 `window` 阶段显示“请在 Edge 窗口中登录”的提示。
- `RESULT:{url,count,source[,unconfirmed][,error]}`：每个 URL 一条；`count` 为 `null` 表示该格失败，`source` 为 `rest` 或 `dom`；`unconfirmed` 为 `true` 表示 REST 返回 0 而页面没能确认。
- `IDENTITY:{user_name,display_name}`：当前会话登录的用户。
- `DONE:{...}`：结束。`ERROR:<文本>`：整体失败。

## 后端接口

表格：

- `GET/POST /api/servicenow/config`：读取/保存表格定义。

常用链接：

- `GET/POST /api/servicenow/links`：读取/保存收藏列表 `{links:[{id,name,url,note,alias}]}`。只接受 `https://*.service-now.com`，最多 200 条；缩写只能含字母、数字、下划线、不以数字开头、最长 20 位，同一用户下不区分大小写、不能重复。
- `GET /api/servicenow/links/conditions`、`POST /api/servicenow/links/build`、`POST /api/servicenow/links/condition-names`：组合条件，见 [condition-composer.md](condition-composer.md)。

登录身份（均按当前软件用户隔离）：

- `GET /api/servicenow/accounts`：`{selected, accounts:[{id,name,user_name,display_name,logged_in}]}`。第一次访问会把旧版的 cookie 文件迁移给“默认”身份，且只迁移给最早创建的软件用户。
- `POST /api/servicenow/accounts {name}`：新建并选中。
- `POST /api/servicenow/accounts/select {id}`：切换。
- `PUT /api/servicenow/accounts/{id} {name}`：重命名。
- `DELETE /api/servicenow/accounts/{id}`：删除身份及其磁盘数据（至少保留一个；有任务运行时返回 409）。
- `POST /api/servicenow/accounts/{id}/logout`：清除该身份的登录状态和 Edge 数据。
- `POST /api/servicenow/accounts/{id}/login/stream`：SSE，执行 `login`，事件 `status` / `stage` / `identity` / `done` / `error`。

读取：

- `POST /api/servicenow/run/stream`，请求体 `{urls:[...], account?}`（不填 `account` 用当前选中的身份），SSE 事件 `status` / `stage` / `result` / `identity` / `done` / `error`。
  - 只接受 `https://*.service-now.com`，其它返回 400；最多 200 个链接，重复链接会去重。
  - 同一时间只允许一个任务（登录、读取、删除、清除互斥），否则 409。最后的 `done`/`error` 事件在 worker 退出、锁释放之后才发送，所以收到后可以立刻发起下一个操作。

## 取数方式

URL 形如 `.../x/dsva2/<app>/simplelist/<表名>/params/list-title/<标题>/query/<encoded query>`，或经典链接 `.../classic/params/target/<表名>_list.do%3Fsysparm_query%3D...`（解析规则见 [filters.md](filters.md)）。

1. 解析出表名和 encoded query（URL 解码后即可使用）。
2. 在已登录页面内调用 `/api/now/stats/<表名>?sysparm_count=true&sysparm_query=<查询>`，带页面自己的 `X-UserToken`。`javascript:gs.beginningOfLast7Days()` 这类写法服务端可直接解析。
3. REST 失败，**或返回 0** 时，读页面自己显示的数字：经典页读 `gsft_main` 里列表表格的 `grand_total_rows`；simplelist 优先取页面自己的 GraphQL 响应（`nowRecordListConnected` 的 `layoutQuery.count`），取不到再读列表标题后面的数字徽章（会穿透 shadow DOM，对页面改版较脆弱）。REST 返回 0 也要确认，是因为 query 含 `sys_tags` 等 REST 不可读的字段时，stats 接口会静默返回 0（见 [filters.md](filters.md)、[count-consistency-check.md](count-consistency-check.md)）。

已验证：REST 结果 13，与页面徽章一致。

当前用户通过 `/api/now/ui/user/current_user` 读取（返回 `user_name`、`user_display_name`），见 [experiments.md](experiments.md)。

## 测试

- 开发：`npm run dev`（后端 8000，前端 5173）；或 `npm run app`（桌面窗口）。
- 首页点“ServiceNow 数据表” → “刷新数据”。首次会弹出 Edge 窗口，登录完成后自动继续。
- 直接跑 worker：见 [authentication.md](authentication.md) 的“重置与诊断”。
- 不依赖前端测后端：用 `fastapi.testclient.TestClient`，设置 `app.dependency_overrides[require_user]`；要测迁移和 `_is_first_user`，需要用数据库里真实存在的最早用户 id。`httpx` 不在项目 venv 里，可以装到临时目录再加入 `sys.path`。
- 临时测试脚本放 `$env:TEMP`，不要放仓库目录，否则 `uvicorn --reload` 会重启服务。
- 控制台输出中文时设置 `PYTHONIOENCODING=utf-8`，并注意 PowerShell 重定向会生成 UTF-16 文件。

## 后续扩展

- Sub phase 等筛选、经典链接适配与合计需求见 [filters.md](filters.md)：Workspace 页面里改筛选器不会写入 URL，建议改用经典列表链接（筛选直接体现在 URL）；选项字段用内部值（Pre-Sales=1，Requests=2）。
- 取数口径不同的格子，只要给出对应的 ServiceNow 列表 URL 即可；如果不是 `simplelist` 页面，需要扩展 `parse_list_url`，或改成直接填表名 + encoded query。
- 同事（不同账号）的身份登录尚未实现，方案和已知事实见 [authentication.md](authentication.md) 的“未实现：用别人的账号登录”。
- 若要完全脱离浏览器，可向 ServiceNow 管理员申请只读 API 账号（OAuth）。

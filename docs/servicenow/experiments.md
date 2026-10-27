# ServiceNow 登录相关实验记录

记录 2026-10 做过的实验、结果和结论，包括后来被证伪的假设。改动登录、profile、cookie 相关代码前先看这里。
所有实验脚本都写在 `$env:TEMP`，用同一套方法：用 Playwright 启动 Edge，打开 `https://dsv.service-now.com/`，每 2 秒打印主框架 URL，等待固定时间后打印标题、正文和 `<input>` 列表。

统一的环境：Windows 域电脑，Edge 156，`VT_EDGE_PROFILE=Profile 1`（bob.zhu@dsv.com）。表中“全新目录”指每个变体用一个新建的临时 Edge 数据目录，并先调用 `ensure_automation_profile()` 复制初始配置文件（不含 cookie）。

## 实验 1：重定向链

默认无头启动访问 `https://dsv.service-now.com/`：

```
dsv.service-now.com/auth_redirect.do
→ sso.dsv.com/auth/realms/dsvinternal/broker/sts.dsv.com/login   (Keycloak)
→ sts.dsv.com/adfs/ls/                                           (ADFS)
```

User-Agent 为 `...HeadlessChrome/156.0.0.0 ... Edg/156.0.0.0`。

## 实验 2：已有登录痕迹的目录里对比浏览器标识

目录：当时已经登录过一次的自动化 profile。

| 变体 | 设置 | 结果 |
|---|---|---|
| A | 默认标识（含 HeadlessChrome），`--headless=new` | ADFS 的 “Sign in” 密码表单 |
| B | 去掉 HeadlessChrome、去掉 `--enable-automation`、加 `--disable-blink-features=AutomationControlled`，无头 | 到达 2FA 页（正文 “For security reasons, we require additional information…”） |
| C | 同 B，可见窗口 | Entrust 一次性口令页 |

当时据此认为改标识就能让 ADFS 做静默登录。**这个结论后来被实验 3 证伪**，只能说在已有登录痕迹的目录里标识会影响 ADFS 的处理方式，原因不明。

## 实验 3：全新目录 + 无头

每个变体都到达 `sts.dsv.com/adfs/ls/`，标题 `Sign In`，出现 `UserName`/`Password` 输入框，**没有自动登录**：

| 变体 | 设置 |
|---|---|
| A | 默认标识 |
| D0 | 改标识 |
| D3 | 改标识 + `--auth-server-allowlist=nothing.invalid` + `--auth-schemes=basic` |

结论：无头模式下 ADFS 总是给表单；改标识和这些参数都不影响。

## 实验 4：全新目录 + 可见窗口

| 变体 | 设置 | 结果 |
|---|---|---|
| E | 改标识，去掉 `--enable-automation` | 40 秒内到达 Entrust 口令页（没有输入任何账号） |
| F | 默认标识 | 读取页面超时，无结论 |

## 实验 5：可见窗口里能否禁用 Windows 自动登录

全新目录，可见窗口，只在额外参数上不同：

| 变体 | 额外参数 | 结果 |
|---|---|---|
| G1 | `--auth-server-allowlist=nothing.invalid` | 40 秒时在 Keycloak `endpoint` 回调中（即已认证成功） |
| G2 | `--auth-schemes=basic` | 第一次出现密码表单；重复三次：口令页、直接进入 ServiceNow、直接进入 ServiceNow |
| G3 | 上面两个都加 | 直接进入 ServiceNow |
| G4 | 再加 `--auth-negotiate-delegate-allowlist`、`--disable-features=AuthNegotiateCnameLookup` | 直接进入 ServiceNow |
| W1 | 无额外参数 | 直接进入 ServiceNow |
| H1 | Playwright 自带 Chromium（非 Edge） | 直接进入 ServiceNow |
| H3 | Edge `--inprivate` | 直接进入 ServiceNow |
| H4 | 禁用 Edge SSO 相关特性并清空证书自动选择 | 直接进入 ServiceNow |
| H2 | Google Chrome | 本机未安装 |

结论：

- 可见窗口里 ADFS 总是自动用当前 Windows 用户登录，换成 Chromium、InPrivate、各种认证参数都没有改变。参数 `--auth-server-allowlist` 和 `--auth-schemes` 无法禁用。
- 有时需要 2FA 口令、有时不需要，触发条件不清楚。
- 具体用的是哪种机制（Kerberos / 证书 / 其它）没有查清。
- 同一个页面在 40 秒时取样结果取决于时间，必要时延长等待。

## 实验 6：读取当前用户

已登录会话里，在页面内执行：

| 方式 | 结果 |
|---|---|
| `window.g_user` | `null` |
| `window.NOW.user_name` / `user_display_name` | `null`；`NOW.user` 只有 `userID`、`isImpersonating` |
| `/api/now/ui/user/current` | 200，但 `result` 为空 |
| `/api/now/table/sys_user?sysparm_query=sys_id=javascript:gs.getUserID()&sysparm_fields=name,user_name,email` | 200，能读到 |
| **`/api/now/ui/user/current_user`** | 200，返回 `user_name`、`user_display_name`、`user_sys_id`（**采用**） |

## 实验 7：新建的 Edge 目录第二次启动失败

现象：新建目录，第一次启动成功，之后所有启动都失败：
`Protocol error (Browser.getWindowForTarget): Browser window not found`（无头、可见都一样，重试、等待 4 秒、10 秒都没用）。

排查：

1. 用 `--remote-debugging-port` 手动启动，列出目标：除了 `about:blank`，还有 **`edge://force-signin/`** 页面。
2. 对比 `Preferences`：长期可用的自动化 profile 里 `account_info` 有 `bob.zhu@dsv.com`；失败的新目录里是空的。`Local State` 没有明显差异。
3. 单独验证：全新目录先可见启动一次，`account_info` 有 1 个账号；紧接着的无头启动失败，之后 `account_info` 变成 0。

验证修复（从长期可用的目录复制浏览器登录状态）：

| 场景 | 结果 |
|---|---|
| 全新目录，只做“可见 → 无头 → 无头” | 可见成功；无头 ×2 失败 |
| 全新目录 + 复制登录状态文件，“无头 → 无头 → 可见 → 无头” | 4 次都成功 |
| 全新目录 + 复制 → 可见登录（含 ServiceNow 登录）→ 无头 | 无头失败并清空账号；**再复制一次登录状态后**，之后连续无头 ×2 成功 |

最终做法见 [authentication.md](authentication.md) 第 7 节：复制的文件是 `Preferences`、`Secure Preferences`、`Web Data`（含 journal）、`Local State`、`OneAuth\`，不复制 cookie 和站点存储；每次走无头前强制重新复制。

日志里的 `https://portal.office.com/ ... failed to install` 只出现在失败的启动里，但不是原因（是公司策略强制安装的 Web 应用在关闭过程中安装失败）。

## 实验 8：端到端（后端接口）

用 `TestClient` 覆盖 `require_user`，使用数据库里最早创建的真实用户：

- 默认身份：迁移旧 cookie 后，连续两次无头读取成功，读到 13，并读到身份 `bob.zhu / Bob Zhu`。
- 新建身份：创建 → 重命名 → 登录（可见窗口自动登录，读到身份）→ 读取（无头成功）→ 删除，目录被删除。
- 路径片段含 `../` 的身份 id 被拒绝（路由不匹配返回 405，另有正则校验）。
- 修复前出现过：流发出 `error` 后立刻调用删除，返回 409（任务线程还没释放锁）。现在最终事件在锁释放后才发送。

## 实验 9：会话 cookie 备份的价值

没有 `session.json` 时，每次无头启动都因为会话 cookie 被 Edge 丢弃而重新登录；保存并恢复 cookie 后，第二次运行完全无头、无弹窗，读到 13。这才是让重复登录消失的真正原因（而不是浏览器标识）。

## 实验 10：新建身份登录后状态仍显示“尚未登录”

**现象**：新建身份并登录，登录窗口完成后页面仍显示“尚未登录”，点“刷新数据”读取成功后才变成已登录。

**证据**：
- 磁盘上该身份的 `session.json` 首次写入时间（登录时）早于读取时间，说明登录本身成功、cookie 已保存。
- 用全新的临时身份目录（`VT_EDGE_AUTOMATION_DIR` / `VT_SN_SESSION_FILE` 指向 `$env:TEMP`）直接运行 `python servicenow_worker.py login` 可复现：输出 `登录成功，继续…` 约 50 秒后出现 `ERROR:已登录，但无法读取当前用户信息`，且没有 `IDENTITY:` 行。
- 探针脚本逐秒记录 URL、抓到的 token、`window.g_ck`、`current_user` 状态码：

| 时刻（可见窗口刚进入首页） | 抓到的 token | `window.g_ck` | `current_user` |
|---|---|---|---|
| 第一次检查 | 有（登录前的旧值） | 空 | 401 |
| 约 1 秒后 | 有 | 有 | 200 |

- 对照：用已保存的会话在无头模式下直接打开首页，token、`g_ck`、`current_user` 一开始就都正常（200）。问题只出在“可见窗口刚登录完”的这一刻。

**根因**：
1. `_get_token` 看到 `holder["token"]` 有值就直接返回，但这是登录跳转时匿名页面请求带的旧 token。
2. 用它请求 `current_user` 得到 401，`_JS_IDENTITY` 非 200 就返回 null，`_identity` 又吞掉所有异常，只试一次，所以没有 `IDENTITY:`。
3. 前端只有收到 `identity` 事件才把身份标记为已登录，`error` 时不会重新读取身份列表。
4. 读取数据时能成功，是因为取身份发生在多个页面加载之后，token 早已被新值覆盖。

**其它观察**：刚进入首页时 `page.evaluate` 会被页面加载卡住几十秒才返回，所以登录命令在“登录成功”后可能很久才报错。

**修复**：见 [authentication.md](authentication.md) 第 9 节（优先 `g_ck`、身份读取重试、登录结束后前端重新同步身份列表）。

## 其它记录

- 复现类实验用临时身份目录，不要碰真实身份目录；跑完删除 `$env:TEMP` 下的临时目录和脚本。
- 运行 worker 要用仓库的 `.venv\Scripts\python.exe`，系统 Python 里没有 playwright。
- 从 `$env:TEMP` 里的脚本导入仓库代码时，要用 `sys.path.insert` 或设置 `PYTHONPATH`。

- 临时工具的清理：脚本结束后要检查并清理命令行里带临时目录名的 `msedge.exe` 进程。
- 用 `Out-File` 重定向会生成 UTF-16 文件，读取时用 `Get-Content` 或指定编码。
- 控制台打印中文需要 `PYTHONIOENCODING=utf-8`，否则会出现 `UnicodeEncodeError`（cp1252）。

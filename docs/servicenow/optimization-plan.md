# ServiceNow 数据表工具 — 优化方案

> 目标：在不改变现有功能与数据准确性的前提下，提升刷新性能、消除重复、拆分过大文件、改善前端渲染与错误处理。
> 每个阶段相互独立，可单独合并与验证。按 P0 → P3 的顺序推进。

## 背景与涉及文件

| 层 | 文件 | 行数 | 职责 |
|----|------|------|------|
| Worker | `servicenow_worker.py` | ~760 | Edge 自动化：登录、计数、解析已保存列表、读名称 |
| 路由 | `app/routers/servicenow.py` | ~970 | 配置/链接/条件库/导入/构建/身份/SSE |
| 纯函数 | `app/core/sn_query.py` | ~250 | URL 解析、条件拆分、可读标签 |
| 前端 | `frontend/src/ServiceNowTables.jsx` | ~1100 | 表格编辑、刷新、账号选择 |
| 前端 | `frontend/src/formula.js` | ~90 | 单元格公式解析/求值 |

数据流：前端 `POST /api/servicenow/run/stream` → 路由 `_stream_worker` 起子进程 `servicenow_worker.py count --file <json>` → worker 逐个链接读数 → `RESULT:` 行经 SSE 回传 → 前端写入 `counts` 并落 localStorage。

---

## P0 — 刷新计数性能重构（收益最大）

### 现状问题
`cmd_count` 串行遍历链接，`_count_page` 对**每个**链接执行 `_open(page, url)`（完整加载 Next Experience 列表 SPA，最长等 60s）后才调用 REST 统计接口。N 个单元格 = N 次重量级 SPA 导航。

### 关键洞察
`_JS_STATS` 是页面内 `fetch('/api/now/stats/<table>?...')`（`same-origin` 带 cookie）。只要页面停在任一 `service-now.com` 源、且已拿到 `X-UserToken`，REST 调用**无需**先把目标列表页渲染出来。真正需要导航页面的只有 REST 读不到的条件（如 `sys_tags`，stats 会静默返回 0，现用 GraphQL `nowRecordListConnected` 兜底）。

### 目标流程（REST 优先 + 页面兜底）
1. 登录后导航一次到 `HOME_URL`，拿到 token、建立 service-now 源。
2. **第一轮**：对所有链接解析出 `table/query`，在页面内并发跑 `stats` REST；返回 `>0` 的直接定稿（`source: "rest"`）。
3. **第二轮**：仅对「REST 返回 0 或失败」的链接，逐个 `_open` 列表页 + 捕获 GraphQL `count` 兜底（保留现有 `_capture_list_count` / `_read_page_count` 逻辑）。
4. 分类链接：经典 `_list.do`（无 title）仍走 `gsft_main` 帧读取，不适用 REST 快路的归入第二轮。

### 验收标准
- 一个 10~20 单元格、全部可 REST 读的表格，刷新时间从「数分钟」降到「数秒」。
- `sys_tags` 这类链接仍返回与现在一致的数字（兜底命中）。
- `source`/`unconfirmed` 语义不变；前端无需改动即可工作。

### 风险 / 注意
- 并发度需限制（如一次 5~8 个 fetch），避免 ServiceNow 侧限流。
- 首轮必须确认页面已在 service-now 源且 token 有效，否则 fetch 会被重定向到 SSO。
- 保留逐链接 `RESULT:` 回传节奏，前端进度条体验不变。

---

## P1 — 消除重复、收敛样板

### 1.1 统一 `parse_list_url` / `_classic_target`
`servicenow_worker.py` 内有一份简化版，`app/core/sn_query.py` 有完整版（含 `kind/host/app_path`）。worker 的 resolve/names 路径已 `from app.core import sn_query`。
- **动作**：计数路径改用 `sn_query.parse_list_url`，删除 worker 内的本地副本，消除逻辑漂移风险。
- **验收**：计数、解析、名称三条路径行为不变。

### 1.2 收敛设置读写样板
路由里约 8 个 `_*_KEY`，每个都重复 `_get_user_setting` + `json.loads` + `try/except (TypeError, ValueError)` + 默认值。
- **动作**：抽一个 `_load_json_setting(user_id, key, default, model=None)` 辅助函数，统一解析与容错。
- **验收**：各 GET/POST 端点返回不变。

---

## P2 — 文件/组件拆分（可维护性）

### 2.1 路由拆分
`app/routers/servicenow.py`（~970 行）按领域拆为子模块，各自 `APIRouter` 再在 `__init__` 汇总挂载：
- `servicenow/tables.py` — 配置表（config）
- `servicenow/links.py` — 常用链接（links）
- `servicenow/conditions.py` — 条件拆分 + 条件库 + 导入 + 构建
- `servicenow/accounts.py` — 登录身份
- `servicenow/worker.py` — `_stream_worker` + SSE 相关端点
- **验收**：对外 `/api/servicenow/*` 路径与行为完全不变。

### 2.2 前端组件拆分
`ServiceNowTables.jsx`（~1100 行）抽离自定义 hook / 子组件：
- `useSnConfig` — 表格配置加载 + 防抖自动保存
- `useSnCounts` — counts 状态 + localStorage 持久化
- `useSnRefresh` — 刷新 SSE 流程
- 表格网格、工具栏、账号选择器拆为子组件
- **验收**：UI 与交互无回归（参考现有 `useSnLinks` / `useSSEStream` 模式）。

---

## P3 — 前端渲染与错误处理

### 3.1 链接查找 O(1) 化
`resolveCell` / `resolveFormula` / `linkUsage` 在渲染中对每个单元格做 `savedLinks.find(...)`，整体 O(单元格 × 链接)。
- **动作**：用 `useMemo` 建 `id→link` 与 `alias→link` 两个 Map。
- **验收**：大表格渲染更流畅，结果一致。

### 3.2 localStorage 写入节流
刷新时每条 `result` 都同步 `localStorage.setItem`，一次刷新可能写数十次。
- **动作**：改为 `done` 时统一落盘，或节流写入。

### 3.3 Worker 错误处理
大量 `except Exception: pass` / `return None`（`_page_token`、`_get_json`、`_capture_list_count` 等）静默吞异常。
- **动作**：失败时至少 `status()` 记一行；`_count_page` 回传给前端的 REST 错误归一为用户友好文案，避免泄漏内部异常细节。

---

## 安全现状（无需紧急处理，仅记录）
- `_is_servicenow_url`、账号/用户 id 正则、路径拼接前的 `_USER_ID_RE` / `_ACCOUNT_ID_RE` 均到位。
- worker 入参走临时 JSON 文件（非 argv），已规避命令注入。
- 继续保持现有校验即可。

---

## 验证与约束（通用）
- Playwright 仅在 `.venv\Scripts\python.exe` 可用（conda base 无）；worker 真实验证须用它。
- 测试脚本放 `$env:TEMP`，不要放仓库根目录（uvicorn `--reload` 会监听并重启、打断在途请求）。
- worker 需 `PYTHONIOENCODING=utf-8`。
- SSE 的最终事件必须在释放 `_run_lock` **之后**发送（否则紧随的下一请求会 409）。
- 每阶段完成后：前端 `npm run build` 通过 + 一次真实链接的端到端刷新验证。

---

## 推进顺序小结
1. **P0** 计数性能重构（只改 worker 计数路径，保留 GraphQL 兜底）— ✅ 已完成
2. **P1.1** 消除 `parse_list_url` 重复 — ✅ 已完成
3. **P3.1** 前端 link Map 缓存 — ✅ 已完成
4. **P1.2 / P2 / P3.2 / P3.3** 按需推进
   - P1.2 设置读写样板收敛 — ✅ 已完成
   - P3.2 localStorage 写入节流 — ✅ 已完成
   - P3.3 worker 错误文案归一 — ✅ 已完成
   - **P2.1 后端路由拆分 — ✅ 已完成**（`app/routers/servicenow/` 包：common / tables / links / accounts / worker；对外 `from app.routers.servicenow import router` 不变，23 条路由一致）
   - **P2.2 前端组件拆分 — ✅ 已完成（结构抽离部分）**：抽出 `serviceNowUtils.js`（纯函数）+ `SnActionMenu.jsx` + `SnSumPicker.jsx`，主组件减约 180 行，bundle 字节数不变（零行为风险）。`useSnConfig/useSnCounts/useSnRefresh` 三个 hook 因与组件状态/effect 时序强耦合、回归风险高于收益，**暂缓**，如需再单独一轮并做运行时验证。

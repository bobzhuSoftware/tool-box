# 新平台（ServiceCentral）适配

`docs/servicenow/` 下的文档都是基于旧平台 `dsv.service-now.com` 写的。新平台上线后，链接换成了 `https://servicecentral.dsv.com/...`，本目录记录新平台的探测结果、已做的适配和待办。

- 本文：结论、已做改动、待办。
- [probe-2026-11-15.md](probe-2026-11-15.md)：探测过程与原始数据（链接格式、`tiny-id`、接口、新旧域名对比）。

## 结论

1. **新平台和旧平台是同一套应用数据结构**：表名（`x_dsva2_enterpri_0_edi_case`）、字段、条件里的 sys_id（`division=420b…`、`assignment_group!=2daf…`）都一样。同一组条件在新平台上就是同一个筛选，所以 **旧链接迁移成新链接只需要换域名**，path 和 query 不变。
2. **旧域名还能打开，但数据已经不再更新**：同一条 7 天筛选，新平台是 **17**，旧平台是 **0**（表总数新 8567、旧 8424）。继续用旧链接得到的数字是错的（而且是看起来合理的 0），所以迁移是必须做的，不只是"以后用新链接"。
3. **新平台的登录和取数方式没变**：沿用旧的 Edge 会话即可静默登录（cookie 域名 `dsv.com` 已覆盖），`g_ck`、`/api/now/ui/user/current_user`、`/api/now/stats/<表>` 都可用，无头模式也能跑，所以现有的"快速 REST"通道在新平台上直接可用。
4. **已保存列表链接 `/list/params/list-id/<id>/tiny-id/<短码>` 的含义**：
   - `list-id`：已保存列表的 id。
   - `tiny-id`：ServiceNow 的 tiny URL 短码（页面设置 `g_uxr_tiny_url.use_tiny = true`，参数太长时被缩成短码）。页面用它调用 GraphQL `getTinyListLayout(tiny: …)`，服务端返回这条列表的完整定义。
   - 返回的 `query` 与旧平台 simplelist 链接里的条件完全相同，`layoutQuery.table` 是表名，`layoutQuery.count` 就是页面显示的记录数（实测 17，与 REST 一致）。
   - 同一个筛选在新平台的 simplelist 链接和 saved-list 链接里得到同样的数字。
5. 页面里还有一个 `databroker/exec/macroponent` 请求返回 `Cannot read property "mappingGroups" from null`（内部 status 500），但页面随后仍然通过 GraphQL 正常加载了列表，这是页面自己的噪音，不影响读数。

## 已做的改动

只放开了域名，使新域名的链接可以被接受、登录判断能通过：

| 位置 | 改动 |
|---|---|
| `app/core/sn_query.py` | 新增 `is_servicenow_host`：`service-now.com`、`*.service-now.com`，以及精确匹配的 `servicecentral.dsv.com`（`servicecentral.dsv.com.evil.com` 之类不通过） |
| `app/routers/servicenow/common.py` | `_is_servicenow_url` 改用上面的函数；新增统一错误提示 `_HOST_PROBLEM`，`links.py`、`worker.py` 里的提示都改用它 |
| `servicenow_worker.py` | `_on_servicenow` 用 `is_servicenow_host`（否则新域名会一直等到 60 秒超时）；`HOME_URL` 改为 `https://servicecentral.dsv.com/now/nav/ui/home` |
| `frontend/src/serviceNowUtils.js`、`ServiceNowLinks.jsx` | 前端的域名规则与后端保持一致，`ServiceNowLinks.jsx` 复用 `serviceNowUtils.js` 里的函数和提示 |
| `test_sn_migrate.py` | `test_servicenow_url_hosts`：新旧域名通过，http、伪造域名、`dsv.com` 被拒 |

用真实 worker 跑过：新域名的 simplelist 链接走快速 REST 通道；旧域名的同一链接 REST 返回 0，页面确认后仍为 0；saved-list 链接目前仍报"URL 无法解析表名"。

## 待办

1. **saved-list 链接统计**（`list-id/tiny-id` 链接）：
   - worker 的 `_count_one` 对 `kind=savedlist` 直接拒绝。可复用已有的 `_capture_tiny_layout` 思路：打开页面，从 `getTinyListLayout` 取 `layoutQuery.table`、`fixedQuery` 与 `query`、`layoutQuery.count`，再走 REST 校验。
   - 前端 `urlProblem` 仍只接受 `/simplelist/` 和 `_list.do`；`linkKind` 也需要增加 saved-list 类型。
   - 条件库的"从链接导入"已支持 saved-list（`resolve` 命令），拼出的 `resolved_url` 用的是 saved-list 链接自己的 host，所以在新平台上拼出来的就是新域名。
2. **旧链接迁移**（已完成）：把 `dsv.service-now.com` 的链接改为 `servicecentral.dsv.com`，路径和 query 不变。入口：表格页「⇅ 迁移 → 把旧链接迁移到新平台…」。
   - 改写：统计表单元格里的 URL、常用链接、条件库的表来源（`host`）。
   - 不用改：`ref:<id>` 引用和 `[缩写]` 公式（按 id 引用）、条件库的条件本身（与域名无关）。
   - 先预览（`POST /api/servicenow/migrate/rehost/preview`，不写库）再执行（`POST /api/servicenow/migrate/rehost`）；执行前前端会自动下载一份 `servicenow-backup-*.json`。
   - 只处理 `https://dsv.service-now.com`，其他 `*.service-now.com` 实例的链接不动。
   - 换域名后两条链接变成同一个 URL 时，保留靠前的一条，被去掉那条的 `ref:` 引用和公式里的缩写会改指向保留的链接（缩写只在保留链接没有缩写时才继承）。
   - 代码：`app/routers/servicenow/rehost.py`（纯函数 `_rehost_state`），界面是 `ServiceNowMigrate.jsx` 的 `rehost` 模式，测试 `test_sn_migrate.py::test_rehost_moves_urls_and_merges_duplicate_links`。
   - 数字不会带过来：刷新得到的数字按旧 URL 保存在浏览器里，迁移后 URL 变了，需要重新点「刷新」，读到的就是新平台的数据。
   - 导入旧电脑导出的旧文件后，里面的链接仍是旧域名；导入后再执行一次这个迁移即可（相同链接会自动合并）。
3. 旧域名下线后，`_COOKIE_DOMAINS` 里的 `service-now.com` 以及 `pdf.py` 里的 `dsv.service-now.com` 写法需要复查（PDF 功能用的是旧域名的 classic 链接，本次未验证）。

# 读数一致性实测：REST 与页面数字（2026-10-28）

在做"组合条件"之前，先核实 [filters.md](filters.md) 里"带 `sys_tags` 的链接 REST 会静默返回 0"的结论是否仍然成立，以及 simplelist 链接是否可靠。本文档只记录实测结果和待办，**还没有修改任何读数逻辑**。

## 背景

用户反馈：同一条件下，经典链接多出两条记录，而 simplelist 只有一条；用户用 simplelist 一直没发现读数问题，对 filters.md 里的结论存疑，希望用数据说话。

## 当前读数逻辑（[servicenow_worker.py](../../servicenow_worker.py) 的 `_count_one`）

1. 先调 REST `/api/now/stats/<表>`。
2. REST 返回 **大于 0**：直接采用，不和页面对比（`source=rest`）。
3. REST 返回 **0** 或失败：再读页面数字。
   - 经典链接：读 `gsft_main` 里列表的 `grand_total_rows`。
   - simplelist：读标题旁的数字徽章。
4. 页面也没读到、REST 是 0：仍返回 0（`source=rest`），**界面没有任何提示**。

## 实测方法

用一个临时脚本（未放入项目），复用 worker 的函数和用户的登录会话，对"常用链接"里的 10 条链接各自同时取：

- REST 数字：不提前返回，直接取 stats 接口的 count；
- 页面数字：经典读 `gsft_main`，simplelist 读标题徽章（最多等 20 秒）。

## 结果

| 链接 | 类型 | 带 `sys_tags` | REST | 页面 | 结论 |
|---|---|---|---|---|---|
| Cases Received in past 7 days Blue | simplelist | 否 | 17 | 未读到 | REST 可信 |
| Cases Received in past 7 days Red | simplelist | 否 | 5 | 未读到 | REST 可信 |
| Pending to be assigned Blue | simplelist | 是 | 0 | 未读到 | **工具显示 0，实际不是 0** |
| Pending to be assigned Red | simplelist | 是 | 0 | 未读到 | **工具显示 0，实际不是 0** |
| Cases … Blue Classic | 经典 | 否 | 17 | 17 | 一致 |
| Cases … Red Classic | 经典 | 否 | 5 | 5 | 一致 |
| Pending … Blue Classic | 经典 | 是 | 0 | 9 | **不一致** |
| Pending … Red Classic | 经典 | 是 | 0 | 1 | **不一致** |
| Pending … Blue Presales Classic | 经典 | 否 | 12 | 12 | 一致 |
| Pending … Red Presales Classic | 经典 | 否 | 3 | 3 | 一致 |

## 结论

1. **不带标签条件的链接**（simplelist 和经典）：REST 数字可信。simplelist 的 17、5 与对应经典页面的 17、5 完全一致。这解释了为什么用户用 simplelist 一直没发现问题。
2. **带 `sys_tags` 的链接**：REST 一律返回 0，filters.md 的结论成立。经典页面读数正确（Blue 9，Red 1）。
3. **simplelist 的页面徽章这次一个都没读到**，所以带标签的 simplelist 链接在表格里会显示成 **0**，与页面不符，且没有任何提示。徽章读不到的原因尚未查明（可能是页面结构、等待时间或标题匹配问题）。
4. 没有观察到"REST 大于 0 但与页面不一致"的情况。这个情况本次没有样本，不能排除。
5. 经典 + 缺标签条件会多出记录：`Pending … Presales Classic` 两条链接的查询里没有 `sys_tags`，所以 Red 是 3 条，而带标签条件的正确结果是 1 条（见 [filters.md](filters.md) 和对话中的排查）。

## 待办与进展

1. ✅ **simplelist 读到 0 时，读页面自己的数字。** 最终没有采用"改走经典页面"，而是用了实验 A（见下）：监听页面自己的 GraphQL 响应，取列表总数。已在 [servicenow_worker.py](../../servicenow_worker.py) 实现（`_capture_list_count`、`_read_page_count`）。
2. ✅ **页面也没读到的 0，在界面上显示 `0 ⚠`。** worker 在"REST 返回 0 且页面读不到"时给结果加 `unconfirmed: true`；单元格显示橙色的 `0 ⚠`，悬停说明"REST 返回 0，页面未能确认，数字可能不准，请在 ServiceNow 里核对"，仍可点击打开链接核对。参与合计时按"缺数据"处理（合计带 ⚠），复制结果时留空；公式里引用它也按缺数据处理。
3. ✅ **单元格悬停显示数字来源。** 数字单元格悬停显示"数据来源（REST 接口 / 页面）"；公式单元格悬停逐个列出变量的值和来源。
4. （可选）"校验读数"手动对比功能。**未做**；手头的临时脚本可以复现，必要时再做成 worker 子命令。
5. ✅ "组合条件"功能，对 simplelist 不加限制提示，见 [condition-composer.md](condition-composer.md)。
6. ✅ [filters.md](filters.md) 里"页面徽章没读到"的说法已更正。

## 补充实验：带标签条件的链接怎么读到正确数字

选项与结论：

| 方案 | 做法 | 实测 | 结论 |
|---|---|---|---|
| **A. 页面 GraphQL 响应** | 监听 `/api/now/graphql` 中 `operationName=nowRecordListConnected` 的响应，取 `data.GlideListLayout_Query.getListLayout.layoutQuery.count` | Red 1、Blue 9，均与页面一致 | **已采用** |
| C'. REST 相减 | 先数不含 `sys_tags` 条件的（REST 正常），取这批记录的 id，减去其中带标签的（从 `label_entry` 读出带 STC 标签的记录 id） | Red 3−2=1、Blue 12−3=9，均一致 | 可行，备选；只对标签条件有效，依赖 `label_entry` 读权限 |
| C. `sys_idNOT IN` 列表 | 用 692 个 id 替换标签条件，走 REST | HTTP 414（地址过长） | 不可行 |
| JSONv2 | `<表>_list.do?JSONv2&sysparm_query=…` | 两条都返回 0 条记录 | 不可用 |
| 经典页面读 `grand_total_rows` | 原有办法（`_JS_CLASSIC`） | Blue 9、Red 1 | 可靠，经典链接继续使用 |

选择 A 的理由：页面能显示的数字它都能取到（不止标签条件）；worker 本来就要打开页面，没有额外开销；比读 DOM 徽章更稳。风险是依赖 ServiceNow 前端的内部接口名，系统升级后可能变化，所以保留了读徽章的兜底。

## 实现后的验证

用真正的 worker 对系统里 10 条链接端到端读数：

| 链接 | 结果 | 来源 |
|---|---|---|
| Cases Received in past 7 days Blue / Red | 17 / 5 | rest |
| Pending to be assigned Blue / Red（simplelist，带标签） | **9 / 1** | dom（页面 GraphQL） |
| Cases … Blue / Red Classic | 17 / 5 | rest |
| Pending … Blue / Red Classic（带标签） | 9 / 1 | dom |
| Pending … Blue / Red Presales Classic（缺标签条件） | 12 / 3 | rest |

此前同样的带标签 simplelist 链接显示的是 0。`Presales Classic` 两条因为缺少标签条件，数字本身就不对，需要换成带标签条件的链接（见排查记录）。

## 复现

临时脚本放在会话目录，没有加入项目。要复现需要：用同一个登录身份的 Edge 配置目录，对每条链接同时取 REST 和页面数字（实现方式见上文"实测方法"）。如果以后需要正式的"校验读数"功能，可以把它做成 worker 的一个子命令。

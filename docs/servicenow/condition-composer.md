# 组合条件（常用链接）

从已收藏的链接里拆出筛选条件，勾选后重新组合成一条新的链接，可以保存回「常用链接」，也可以直接复制。

## 为什么需要

ServiceNow 的筛选条件本质上是用 `^`（AND）连接的一串小条件。同一张表的几条链接往往只差一两个条件（例如 Pre-Sales 与否、Blue 与 Red）。手工改链接容易写错，在经典页面的筛选器里改又会丢掉标签条件（见 [filters.md](filters.md)），所以直接在查询层面拆分和重组。

## 使用方法

1. 展开「常用链接」，在某条链接上点 **组合**。
2. 面板里分两部分：
   - **基础链接的条件**：默认全部勾选，取消勾选就是去掉这个条件。
   - **其他链接里的条件**：来自同一张表的其他收藏链接，勾选就加入，旁边标出来源链接。
3. 选择 **生成格式**：经典 或 simplelist（默认与基础链接相同）。
4. 填名称（simplelist 的列表标题也用它）、可选的缩写和备注，下面实时显示生成的链接。
5. **保存为新链接**，或 **复制链接**。

## 规则

- **只组合同一张表的条件**；条件之间都是 AND。
- **同一字段冲突时提示**：例如 `division = A` 与 `division = B`、`sub_phase = 1` 与 `sub_phase = 2`，或 `x = v` 与 `x != v`（结果一定为空）。勾选其中一个时，会弹出确认「用它替换已选的条件？」，确认才替换。`state != 1000` 与 `state != 1300` 这种不矛盾的不会提示。
- **相同条件自动去重**（也解决了 `sub_phase=1` 重复出现的问题）。
- **含 `^OR` 的条件组整体处理**：`^OR` 只和它前面紧挨着的那一条组成 OR 组（`A^B^C^ORD` = A 且 B 且 (C 或 D)）。拆分时 `C^ORD` 作为**一个条件**显示（如「short_description：以 AMER 结尾 或 以 APAC 结尾」），只能整组勾选或取消，不能拆开，也不参与冲突检测；生成的链接里保持 `C^ORD` 原样相邻。
- **含 `^NQ`（多组查询）的链接不支持拆分**，面板会提示原因，避免拼出错误的结果。
- `^ORDERBY…`、`^GROUPBY…` 不影响记录数，拆分时丢弃，生成的链接里不再带排序。
- 条件显示名称：内置了一批已知条件的友好名称（如 `sub_phase=1` → Sub phase = Pre-Sales）；未知条件显示原始写法。每个条件旁的 ✎ 可以改名，名称按用户保存。优先级：自定义名称 > 内置名称 > 读取到的名称 > 原文。
- **可读名称（只影响显示）**：条件里的 sys_id（如 `business_analyst=<32 位 id>`）、选项值（如 `state=1300`）和字段名，可以用已登录的 Edge 从 ServiceNow 读出名称，显示成 Business analyst：Srishti Dhingra、State = Closed Complete。条件原文、勾选、去重、导入校验、生成的链接都不受影响，读不到时显示不变。点条件库里的「读取名称 (N)」读取（N 是还没读过的条件数，读完变成「刷新名称」，用于人员改名后更新）；导入链接时会自动读取。worker 的 `names` 子命令用 `/api/now/ui/meta/<表>` 取字段标签和选项，再用 `/api/now/stats/<表>` 按字段分组（`sysparm_display_value=all`）取引用字段的名称，统计里没有的再按引用表补查；查不到的值记为空，不再重复请求。结果按用户保存在设置键 `servicenow_value_names`。标签（`sys_tags`）暂不读取，仍用自定义名称或内置名称。
- 日期函数显示成中文（如 `javascript:gs.beginningOfLastWeek()` → 上周开始），不需要登录。
- **原文的可读显示**：编辑区和悬停提示里显示的条件原文，会把读到名称的 ID 和代码换成名称（`business_analyst=Srishti Dhingra^ORbusiness_analyst=Eileen De La Cruz`），字段名、`^`、运算符和日期函数不变，读不到的保持原样。后端用 `raw_display` 字段返回，只用于显示（不是合法条件）；编辑区的「复制原文」复制真实的条件。
- 生成的链接仍然只允许 `https://*.service-now.com`，长度不超过 4000，一次最多组合 60 个条件。

## 两种格式

| 格式 | 形式 | 说明 |
|---|---|---|
| 经典 | `…/now/nav/ui/classic/params/target/<表>_list.do%3Fsysparm_query%3D<双重编码的条件>%26sysparm_first_row%3D1%26sysparm_view%3D` | 条件直接体现在地址里；标签条件靠写进查询实现，不经过筛选器界面，不会丢 |
| simplelist | `…/<应用路径>/simplelist/<表>/params/list-title/<标题>/query/<条件>` | 应用路径取自基础链接；基础链接是经典格式时，借用同一主机上已收藏的 simplelist 链接的应用路径，找不到则用默认的 `/x/dsva2/enterprise-integrations` |

两种格式在表格里都可以读数：带标签条件的 simplelist 也能读对（见 [count-consistency-check.md](count-consistency-check.md)）。

## 条件库

「常用链接」标题栏的 **🧩 条件** 打开条件库，集中管理所有条件，并且不需要先选基础链接就能直接生成链接。

- **自动收录**：每次保存链接（添加、编辑、组合、删除）时，服务端把链接里拆出的条件按「表 + 条件原文」去重后加入条件库；保存前会先收录被替换掉的旧链接的条件，所以**删除链接不会丢条件**。第一次打开时会把已有链接的条件一次性导入。条件库只增不减：删除链接后，它独有的条件会留在库里，显示为「未使用」。
- **条件来源**：每个条件记一个来源——`链接`（自动收录）、`导入`（从链接导入）、`手动`（手动新增）。条件库里按来源显示标签，也可以按来源筛选。
- **管理**：按表切换、搜索；每个条件显示被几条链接使用（悬停看名单）；✎ 修改显示名称和备注；✕ 删除（被链接使用的条件不能删，否则会被再次收录）；**清理未使用** 只删除来源是`链接`、且现在没有链接使用的条件，`导入`和`手动`的条件永远不会被它删掉。
- **从链接导入条件**：点「⇩ 从链接导入」，粘贴一条或多条 simplelist / 经典链接（每行一条），点「解析链接」。预览按链接列出里面的条件，库里已有的标「已在库中」，默认只勾选新的，可以取消勾选。确认后**只把条件写入条件库，链接本身不保存**。导入的条件备注自动写「来自链接：<列表标题>」（经典链接没有标题，写表名）。已有条件被勾选导入时会改标为`导入`，从此不会被清理。`^NQ`、非 ServiceNow、无法识别的链接会提示原因，不会导入；一次最多 20 条。
- **新增条件**：手动输入条件原文（如 `state!=1000`，或 OR 组 `a=1^ORa=2`），可同时起个显示名称。所属的表必须已经在条件库里有记录（保存过或导入过这张表的链接）。
- **表的主机和路径**：条件库按表记下链接的主机和 simplelist 应用路径（设置键 `servicenow_condition_tables`），保存链接和导入链接时都会更新，每张表以先见到的为准。生成链接时用它，所以只导入、没保存过链接的表也能建链接。经典链接的前缀只取决于主机；simplelist 的前缀带应用路径（按应用，如 `/x/dsva2/enterprise-integrations`），只导入了经典链接的表没有应用路径，建 simplelist 时借用同主机上已知的应用路径，找不到则用默认值。
- **分组和筛选**：条件按字段分成可折叠的组（组头显示条数和已选数，OR 组和无法识别字段的归入「其他 / OR 组」），有「全部折叠 / 全部展开」。搜索框下面可以叠加筛选：使用状态（全部 / 已选 / 已使用 / 未使用）、条件类型（＝ / ≠ / 包含等 / OR 组 / 标签）、字段、来源链接（只看某条链接用到的条件）。筛选时所有有结果的组自动展开，「清除筛选」一键恢复。切换表会重置筛选。
- **直接建链接**：勾选同一张表的条件 → 选择经典或 simplelist → 填名称 → 实时预览 → 保存为新链接或复制。同字段冲突的提示和链接上的「组合」一致。保存后窗口保持打开，可以继续生成。
- 链接上的「组合」面板也会列出条件库里的条件（来源标为「来自条件库」）。

## 实现

| 位置 | 内容 |
|---|---|
| [app/core/sn_query.py](../../app/core/sn_query.py) | 纯函数：`parse_list_url`（解析 simplelist / 经典链接）、`split_query`（OR 组合并为一个条件）、`is_valid_unit`、`describe`（字段、运算符、值）、`label_for`、`build_url` |
| [app/routers/servicenow.py](../../app/routers/servicenow.py) | `GET /api/servicenow/links/conditions`（每条链接拆出的条件）、`POST /api/servicenow/links/build`（按基础链接 `base_id` 或表 `table`，加所选条件生成链接）、`POST /api/servicenow/links/condition-names`（保存条件显示名称，设置键 `servicenow_condition_names`）；条件库：`GET /api/servicenow/conditions`、`POST /api/servicenow/conditions`（新增，或修改名称/备注）、`POST /api/servicenow/conditions/delete`、`POST /api/servicenow/conditions/cleanup`、`POST /api/servicenow/conditions/import/preview`（解析粘贴的链接，返回条件和「库里已有」）、`POST /api/servicenow/conditions/import`（导入所选条件；表、主机、条件都由服务端重新从链接解析，不信任客户端传来的值）（设置键 `servicenow_condition_library`，存表、条件原文、备注、来源；表的主机和路径存 `servicenow_condition_tables`；名称仍存在 `servicenow_condition_names`；最多 1000 个条件） |
| `frontend/src/LinkComposer.jsx` | 组合界面：勾选、冲突确认、实时预览、保存 |
| `frontend/src/ConditionLibrary.jsx` | 条件库界面：管理条件、按表勾选生成链接 |
| `frontend/src/ServiceNowLinks.jsx` | 链接行上的「组合」入口、标题栏的「条件」入口 |

`build` 接口只接收基础链接的 id（或表名）和条件片段，主机由服务端取，客户端不能指定任意地址。按表建链接时，主机和 simplelist 应用路径取自条件库记下的该表信息。

## 验证

- 四条已收藏的 simplelist 链接，用 `build_url` 重新拼出来与原链接**逐字符一致**。
- 对 10 条已收藏链接，拆分后分别生成经典和 simplelist 链接，再解析回来，表名和查询都一致。
- `^NQ`、开头的 `^OR`（前面没有条件）被拒绝；`^OR` 组被作为整体保留；`^ORDERBY` 被丢弃；重复条件被去重；`a=1^b=2` 作为单个条件、`ORa=1`、条件数超过 60 等输入被拒绝。
- 条件命名保存、读取、清除正常。
- 前端构建通过。**界面本身尚未在浏览器里逐项点过**，建议上线前走一遍：打开组合面板、勾选冲突条件、改名、保存、复制。

## 已知限制

- 可以在条件库里手工输入新条件（原文写法，如 `state!=1000`），但还没有字段 + 运算符 + 值的可视化编辑器。
- 不支持 OR 组的拆分（只能整组使用）和 `^NQ`。
- 不同表的链接不能混合。
- Next Experience 的「已保存列表」链接（`…/list/params/list-id/<id>/tiny-id/<短码>`）的条件存在服务端，地址里没有，所以只能在条件库的“从链接导入”里读取，不能保存为常用链接后自动收录。读取方式（2026-10-29 实测）：用已登录的 Edge 打开链接，页面的 `nowRecordListConnected` GraphQL 响应里 `getTinyListLayout` 带有 `query`（标准条件文本，含 `ORDERBY` / `GROUPBY` 和 `sys_tags` 条件）、`listTitle`，表名在 `layoutQuery.table`。worker 的 `resolve` 子命令读出这些后拼成等价的 simplelist 链接（`resolved_url`），再走普通的预览和导入。它用“数量表”里当前选中的登录身份，已登录时静默运行（无窗口），登录过期才会弹出 Edge 窗口；保存的是解析当时的条件快照。入口是 `POST /api/servicenow/conditions/import/resolve/stream`。想查看任意页面调了哪些接口，可以用 [tools/servicenow_probe_page.py](../../tools/servicenow_probe_page.py)。

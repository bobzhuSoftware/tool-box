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
- 条件显示名称：内置了一批已知条件的友好名称（如 `sub_phase=1` → Sub phase = Pre-Sales）；未知条件显示原始写法。每个条件旁的 ✎ 可以改名，名称按用户保存。
- 生成的链接仍然只允许 `https://*.service-now.com`，长度不超过 4000，一次最多组合 60 个条件。

## 两种格式

| 格式 | 形式 | 说明 |
|---|---|---|
| 经典 | `…/now/nav/ui/classic/params/target/<表>_list.do%3Fsysparm_query%3D<双重编码的条件>%26sysparm_first_row%3D1%26sysparm_view%3D` | 条件直接体现在地址里；标签条件靠写进查询实现，不经过筛选器界面，不会丢 |
| simplelist | `…/<应用路径>/simplelist/<表>/params/list-title/<标题>/query/<条件>` | 应用路径取自基础链接；基础链接是经典格式时，借用同一主机上已收藏的 simplelist 链接的应用路径，找不到则用默认的 `/x/dsva2/enterprise-integrations` |

两种格式在表格里都可以读数：带标签条件的 simplelist 也能读对（见 [count-consistency-check.md](count-consistency-check.md)）。

## 实现

| 位置 | 内容 |
|---|---|
| [app/core/sn_query.py](../../app/core/sn_query.py) | 纯函数：`parse_list_url`（解析 simplelist / 经典链接）、`split_query`（OR 组合并为一个条件）、`is_valid_unit`、`describe`（字段、运算符、值）、`label_for`、`build_url` |
| [app/routers/servicenow.py](../../app/routers/servicenow.py) | `GET /api/servicenow/links/conditions`（每条链接拆出的条件）、`POST /api/servicenow/links/build`（按基础链接和所选条件生成链接）、`POST /api/servicenow/links/condition-names`（保存条件显示名称，设置键 `servicenow_condition_names`） |
| `frontend/src/LinkComposer.jsx` | 组合界面：勾选、冲突确认、实时预览、保存 |
| `frontend/src/ServiceNowLinks.jsx` | 链接行上的「组合」入口 |

`build` 接口只接收基础链接的 id 和条件片段，主机和表名由服务端从基础链接取，客户端不能指定任意地址。

## 验证

- 四条已收藏的 simplelist 链接，用 `build_url` 重新拼出来与原链接**逐字符一致**。
- 对 10 条已收藏链接，拆分后分别生成经典和 simplelist 链接，再解析回来，表名和查询都一致。
- `^NQ`、开头的 `^OR`（前面没有条件）被拒绝；`^OR` 组被作为整体保留；`^ORDERBY` 被丢弃；重复条件被去重；`a=1^b=2` 作为单个条件、`ORa=1`、条件数超过 60 等输入被拒绝。
- 条件命名保存、读取、清除正常。
- 前端构建通过。**界面本身尚未在浏览器里逐项点过**，建议上线前走一遍：打开组合面板、勾选冲突条件、改名、保存、复制。

## 已知限制

- 不支持手工输入新条件（字段 + 运算符 + 值），只能用已有链接里出现过的条件。
- 不支持 OR 组的拆分（只能整组使用）和 `^NQ`。
- 不同表的链接不能混合。

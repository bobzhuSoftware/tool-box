# ServiceNow 列表筛选条件（Sub phase 等）

目标：表格里的格子要按 Blue/Red 与 Sub phase（Requests / Pre-Sales）分别计数，并加合计。本文记录已验证的事实和待实现的方案。

## 已验证的事实

### 1. Workspace 页面里改筛选器不会写入 URL

`https://dsv.service-now.com/x/dsva2/<app>/simplelist/<表>/params/list-title/<标题>/query/<encoded query>` 这类链接，筛选条件**只来自路径里的 `/query/`**。在页面右侧 Filter 面板里加条件只是会话内状态：地址栏不变，筛选角标会加 1，复制出来的链接仍是旧的。

所以不能"先在页面上加筛选，再复制地址栏"。

### 2. 可以直接在 `/query/` 末尾追加条件

`/query/` 的内容就是 encoded query，`^`（编码为 `%5E`）表示 AND。在末尾追加 `%5E<字段><运算符><值>` 即可，页面会把它显示成 Filter 面板里的条件。

基础链接（Pending to be assigned Blue）解码后：

```
business_analystISEMPTY
^division=420b49d01b3ae5103620b6c5464bcb89
^state!=1000
^state!=1300
^origin_of_request!=schenker_origin
^sys_tags.e965fb502b627adcbe57f2c0ce91bf48EXCLUDINGe965fb502b627adcbe57f2c0ce91bf48
^short_descriptionNOT LIKEAPI
```

表名 `x_dsva2_enterpri_0_edi_case`；Blue/Red 靠 `division=<sys_id>` 区分，Unassigned 靠 `business_analystISEMPTY`。

### 3. `sub_phase` 是选项字段，内部值是数字，建议用内部值

| 显示名 | 内部值 | 来源 |
|---|---|---|
| Pre-Sales | `1` | 经典界面 Run 出的 `sysparm_query=sub_phase=1` |
| Requests | `2` | 经典界面 Run 出的 `sysparm_query=sub_phase=2` |

- 在 Workspace 页面的 `/query/` 里追加 `^sub_phase=Pre-Sales` **不生效**。
- 追加 `^sub_phase=Requests` 在页面里**生效**，匹配到了数据（有一次结果为 0，是因为当时多加了别的筛选条件，不是文字值的问题）。原因没有查清：可能是页面把文字当作显示名解析了，也可能与 `-` 的解析有关，两个值写法不同的原因目前只是推测。
- 已验证可用的 Pre-Sales 链接：基础链接 `/query/` 末尾追加 `%5Esub_phase%3D1`。Requests 的数字值 `2` 来自经典界面，与文字写法的结果是否一致待核对数量。
- **统一使用数字内部值**：页面（UI 层）对文字值的容忍度因字段值而异；而 worker 是直接调 stats REST 接口，文字值是否被接受没有验证过，所以链接和附加条件一律写 `sub_phase=1/2`，不要依赖文字写法。

### 4. 用经典界面获取任意筛选的 query

经典界面会把筛选写进 URL，适合用来查内部值：

1. 打开 `https://dsv.service-now.com/now/nav/ui/classic/params/target/x_dsva2_enterpri_0_edi_case_list.do`
2. 用筛选器选条件（如 Sub phase = Pre-Sales），点 Run。
3. 地址栏形如：
   ```
   .../classic/params/target/x_dsva2_enterpri_0_edi_case_list.do%3Fsysparm_query%3Dsub_phase%253D1%26sysparm_first_row%3D1%26sysparm_view%3D
   ```
4. 这是**双重编码**：解码一次得到 `x_..._list.do?sysparm_query=sub_phase%3D1&...`，再解码 `sysparm_query` 得到 `sub_phase=1`。

补充：选项字段的内部值和显示名不一定一致，这里 Phase 与 Sub phase 两列都显示 "Pre-Sales"，但它们是不同字段。

### 5. 其它尝试

- 直接在浏览器打开 `/api/now/stats/...` 的 GET 链接，用户这边访问不了，不能依赖。
- F12 Network 里搜 `sub_phase` 可以看到页面实际发出的 query（备用办法）。

## 目标表格与对应条件

| | Opened | Unassigned Requests | Unassigned Pre-Sales | Total |
|---|---|---|---|---|
| Blue | 已有链接（Cases Received in past 7 days Blue） | 基础链接 + `^sub_phase=2` | 基础链接 + `^sub_phase=1` | 行合计 |
| Red | 已有链接 | 基础链接（Red）+ `^sub_phase=2` | 基础链接（Red）+ `^sub_phase=1` | 行合计 |
| Total | 列合计 | 列合计 | 列合计 | 总计 |

Total 是否包含 Opened 列需确认：从需求图看，行 Total 只是 Unassigned 两列之和；Opened 列的 Total 是 Blue + Red。

## 经典链接（已适配）

worker 现在同时支持 simplelist 和经典链接，解析规则见 [servicenow_worker.py](../../servicenow_worker.py) 的 `parse_list_url`：

| 形式 | 例子 |
|---|---|
| Next Experience 包装的经典页（浏览器里直接复制的地址栏） | `.../now/nav/ui/classic/params/target/<表>_list.do%3Fsysparm_query%3D<双重编码的 query>%26sysparm_first_row%3D1%26sysparm_view%3D` |
| 直接的经典链接 | `https://dsv.service-now.com/<表>_list.do?sysparm_query=<编码的 query>` |
| nav_to | `https://dsv.service-now.com/nav_to.do?uri=<表>_list.do%3Fsysparm_query%3D...` |

表名取 `<表>_list.do` 的 `<表>`，query 取 `sysparm_query`（经典包装页要先解码一次再取参数）。`^ORDERBY…` 等额外片段原样保留，stats 接口不受影响（实测 `sub_phase=2^ORDERBYnumber` 与 `sub_phase=2` 都返回 502）。前端 `urlProblem` 也放宽为 simplelist 或 `_list.do`。

经典页面没有标题旁的数字徽章，总数在底部分页（`1 to 8 of 8`）。

### 重要发现：REST 统计接口遇到 `sys_tags` 条件会静默返回 0

基础链接里有 `sys_tags.<标签 id>EXCLUDING<标签 id>`（即页面上 "Tags excluding STC"）。实测逐个叠加条件的计数：

| 累计条件 | REST 计数 |
|---|---|
| business_analystISEMPTY | 1837 |
| + division | 395 |
| + state!=1000 | 147 |
| + state!=1300 | 22 |
| + origin_of_request!=schenker_origin | 14 |
| + sys_tags…EXCLUDING | **0** |
| 不带 sys_tags，带 short_description | 11 |
| 全部条件 | **0**（页面实际是 8） |

原因：同一个 query 走 Table API 会返回 403 `Field(s) present in the query do not have permission to be read`，说明 `sys_tags` 字段对 REST 不可读。stats 接口对此不报错，直接返回 `count=0`，所以**这类链接的 REST 结果是错的 0**。

### 处理方式（已实现）

- REST 返回 **大于 0** 时直接采用。
- REST 返回 **0** 或失败时，改读页面自己显示的总数：
  - 经典链接：进入 `gsft_main` iframe，读列表表格的 `grand_total_rows` 属性（实测等于页面分页里的总数），读不到时退回分页文字 `x to y of N`。
  - simplelist：仍用标题旁的数字徽章。
- 页面也读不到、而 REST 是 0 时，才返回 0（`source=rest`）。
- 结果里的 `source`：`rest` / `dom`。`dom` 表示数字来自页面。

### 实测结果（经典链接，带完整条件）

| 链接 | 结果 | 来源 |
|---|---|---|
| Blue 完整条件（无 sub_phase） | 8 | dom |
| Blue + `sub_phase=1`（Pre-Sales） | 8 | dom |
| Blue + `sub_phase=2`（Requests） | 0 | dom |
| 仅 `sub_phase=2` | 502 | rest |
| 仅 `sub_phase=1` | 2083 | rest |

### 已知问题

- 原 simplelist 的 Blue 链接（含 sys_tags）REST 返回 0，页面徽章当时没读到，结果是错误的 0。**已修复**：simplelist 现在优先读页面自己的 GraphQL 响应（`nowRecordListConnected` 的 `layoutQuery.count`），再退回读徽章。实测与方案见 [count-consistency-check.md](count-consistency-check.md)。经典链接仍可使用。
- 读页面比 REST 慢（每个 0 结果多等几秒到十几秒），真正的 0 也会走一遍页面确认。

## 合计行/列（已实现）

合计是**可选**的：不是每个表格都需要，也不一定汇总整行/整列（例如她的图里行 Total 只加 Requests + Pre-Sales，不含 Opened）。

- 编辑模式里，除了"＋ 行 / ＋ 列"，还有"＋ 合计行 / ＋ 合计列"。新增的合计项就是名为"合计"的普通行/列，可以改名、删除。
- 合计项的表头下方有勾选列表"汇总：…"，选择它加哪些行/列（默认勾选当时已有的所有数据行/列；之后新增的数据行/列需要手动勾选）。
- 数据存在表格配置的 `sums` 字段里：`"c<序号>"` 表示合计列，`"r<序号>"` 表示合计行，值是它汇总的列/行序号。旧配置没有这个字段，照常使用。后端 `SnTable.sums` 对应保存，删除行/列时前端会同步调整序号（`removeSumIndex`，和 `cells` 的 `removeIndex` 同一思路）。
- 合计行与合计列交叉的格子留空，规则以后再定。
- 汇总的格子里有失败或尚未读取的，屏幕上显示已算出的部分和并加 ⚠（悬停有说明）；一个格子都没读到时显示 `—`；没配链接的格子按 0 处理，整行都没配链接则留空。
- "复制结果"会带上合计；不完整的合计留空，避免把不完整的数字贴进报告。

预留给**分组表头**（如 "Unassigned" 横跨多列）：以后可以加独立的 `colGroups: [{label, start, end}]`，和 `sums` 一样靠列序号引用，调整序号时在同一处处理，两者互不影响。

## 待实现

- 分组表头（合并单元格），以及复制时输出对应的合并表头。
- 合计行/列交叉格的规则。

## 验证清单

- Blue/Red 的 Pre-Sales、Requests 数量与经典页面筛选后的数量一致，且至少有一个格子数量不为 0。
- Pre-Sales + Requests 之和等于原 Unassigned 总数（若还有其它 Sub phase，要单独核对）。
- 带 `sys_tags` 条件的链接，`source` 应为 `dom`，数量与页面一致，不能是 REST 的 0。
- 合计行/列在有格子失败时显示正确。

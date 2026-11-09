# ADR 0004：读取工具对错误 key 的处理

- 状态：已采纳
- 决策者：Agent（人类于 2026-11-09 授权修复此问题，具体方案由 Agent 决定）
- 日期：2026-11-09
- 涉及：`src/zotero_mcp/client.py`（`get_collection_items`、`get_item_children`、`get_notes`、`get_annotations`、`get_citation_items`、`export_items`）、`src/zotero_mcp/citation_models.py`

## 背景

在 Zotero 10.0.6 容器里实测，本地 API 对不存在或类型不对的 key 大多不报错：

| 请求 | 实测结果 |
| --- | --- |
| `GET /collections/{不存在或条目 key}/items/top` | 200，`Total-Results` 等于整库顶层条目数，返回整个库 |
| `GET /collections/{不存在或条目 key}` | 404 |
| `GET /items/{不存在或集合 key}/children`（带或不带 `itemType`） | 200，`[]` |
| `GET /items/{不存在或集合 key}` | 404 |
| `GET /items/top?itemKey={子笔记 key}&format=bibtex` | 200，内容为空行，`Total-Results: 0` |
| `GET /items/top?itemKey={子笔记},{顶层条目}&format=…` | 200，只含顶层条目，`Total-Results: 1` |
| `GET /items/top?itemKey={独立笔记}&format=bibtex` | 200，内容为空行，但 `Total-Results: 1` |
| `GET /items?itemKey={key,…}` | 200，返回命中的条目及它们的子条目；不存在的 key 不出现 |

后果：agent 把整库当作集合内容；空列表无法区分“没有子条目”和“key 写错”；`export_items` 对子条目返回空内容且无错误；`get_citation_items` 对子条目、附件和不存在的 key 给出同一个 `NOT_FOUND`，消息里还暴露内部端点。

## 决定

1. `get_collection_items`：先请求 `/collections/{key}`，404 时返回 `NOT_FOUND`，消息提示用 `zotero_get_collections` 查集合 key；通过后再列条目。已删除（进回收站）的集合同样返回 `NOT_FOUND`，已在 live 测试验证。
2. `get_item_children`、`get_notes`（指定父条目）、`get_annotations`：列表为空时才请求 `/items/{key}`，404 时返回 `NOT_FOUND`。检查完成后恢复前一个请求的分页状态（`Total-Results`、`Link`），所以空页的 `total` 仍是真实值。传入集合 key 也返回 `NOT_FOUND`。
3. `get_citation_items`：保持原来的 `/items/top` 批量请求。只有当有 key 未被返回时，才再发一次 `/items?itemKey=…` 批量请求来区分：
   - 子笔记、子附件、批注 → `NOT_CITABLE`，该项（`CitationItemFailure`）带可选字段 `parent_key`，消息提示改用父条目；批注的 `parent_key` 是所属附件，消息说明要引用该附件所属的文献；
   - 未命中（含集合 key）→ `NOT_FOUND`，没有 `parent_key`；
   - 独立笔记/附件仍是 `NOT_CITABLE`，没有 `parent_key`。
   消息不再出现内部端点。`parent_key` 在成功项和其他错误里不出现，模型里默认值为 `null`。
4. `export_items`：选择**整体失败**，不是逐项错误。导出结果是一段 BibTeX/RIS 文本，无法给每个 key 附带状态，要逐项报告就得改返回结构。导出请求完成后读取响应的 `Total-Results`，若小于去重后的 key 数，再发一次 `/items?itemKey=…` 批量请求，整体抛出：
   - 只要有子条目：`NOT_CITABLE`；否则 `NOT_FOUND`；
   - `details.items` 列出每个有问题的 key：`{key, code, parent_key?}`；
   - 消息列出 `子 key -> 父 key` 的替换建议，并说明没有导出任何内容。
   响应头缺失时不做检查。

## 理由

- **整库返回只能先检查。** Zotero 对错 key 与对真集合的响应格式相同；若用“总数等于整库”启发式，需要再请求整库总数才能判断，成本更高且不可靠。`/collections/{key}` 响应很小，代价是 **每次 `get_collection_items` 多一次本地请求**。本地 API 在同一台机器上，这个开销可以接受，且保证没有误判。
- **空列表后再检查。** 子条目列表非空即证明父条目存在，所以正常路径零额外请求；只有空页多一次请求。
- **引用与导出走同一个分类。** 两者都只需回答“这个 key 是子条目还是不存在”，用同一次 `/items` 批量请求回答，不逐个请求。批量请求只在出错路径发生。
- **导出复用 `NOT_CITABLE`，不新增错误码。** 两个工具遇到子条目时的恢复动作相同（改用 `parent_key`），agent 只需学一个代码；`details.items` 里每项的代码保留精确信息。
- **`parent_key` 放在条目层，不放进 `error`。** 与 `CitationItemFailure` 的 `index`、`key` 同级，不改变 `error: {code, message}` 的现有形状；`CHILD_ITEM_NOT_ALLOWED` 的 `details.children` 也使用同名 `parent_key`。
- **整体失败与现有契约一致。** `export_items` 的契约是“只导出顶层条目”，含不可导出 key 的请求没有可信的部分结果；静默丢掉一部分正是要修的缺陷。

## 已知限制

- 独立笔记、独立附件在 BibTeX/RIS 中没有表示，Zotero 静默跳过它们，且 `Total-Results` 仍把它们算进去，所以 `export_items` 检测不到；CSL JSON 会为独立笔记输出一个 `document` 条目。这与 `get_citation_items` 对它们返回 `NOT_CITABLE` 不一致，本次不处理。
- 子附件、批注批量请求会带回已命中条目的全部子条目，已用 `everything()` 翻页并按请求 key 过滤；只发生在出错路径。

## 考虑过的选项

| 选项 | 为什么没选 |
| --- | --- |
| `get_collection_items` 先列条目再检查 | 错 key 时先拉取整库再丢弃，浪费请求 |
| 用返回总数判断是否“整库” | 需要整库总数才能判断；真集合恰好包含全部条目时误判 |
| 空列表一律当作 `NOT_FOUND` | 真的没有子条目的条目会被误报 |
| 每次都先检查父条目 | 非空列表时多一次请求，没有收益 |
| `export_items` 逐项错误（`items[{key,status,content|error}]`） | 需要为 BibTeX/RIS 按条目切分 Zotero 的输出，或逐个 key 请求；改变返回结构 |
| 为导出新增 `NOT_EXPORTABLE` | 与 `NOT_CITABLE` 恢复动作相同，多一个代码增加 agent 的判断成本 |
| `get_citation_items` 对未命中的 key 逐个请求 | 最多 50 次请求；批量请求一次解决 |
| 批注的 `parent_key` 直接解析到最终文献 | 需要第二轮批量请求，批注极少作为引用对象，暂不值得 |

## 重新评估的触发条件

- Zotero 的本地 API 对未知 key 改为返回 404 或空集合：可以删除对应的预检查。
- 本地请求变慢，使 `get_collection_items` 的预检查可感知：可以改为先请求再检查，或缓存集合 key。
- agent 常常需要“导出一批 key，容忍其中部分无效”：改为逐项结果。
- 需要报告独立笔记/附件的导出缺失：为导出增加类型检查。

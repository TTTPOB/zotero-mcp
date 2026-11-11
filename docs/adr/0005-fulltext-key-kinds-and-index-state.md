# ADR 0005：全文工具按 key 类型和索引状态给出可自救的错误

- 状态：已采纳（决策 1 的“参数名不变”由 ADR 0009 取代；其余决定继续有效）
- 决策者：Agent（人类于 2026-11-09 授权修复此问题，具体方案由 Agent 决定）
- 日期：2026-11-09
- 涉及：`src/zotero_mcp/client.py`（`get_item_fulltext`、`find_item_fulltext`）、`src/zotero_mcp/fulltext.py`、`src/zotero_mcp/fulltext_models.py`

## 背景

真实 Zotero 10.0.6 上的测试发现：

1. `find_item_fulltext` 传论文（父条目）key 时返回 `FULLTEXT_NOT_INDEXED`，提示“Index it in Zotero first”。真实原因是传错了 key，agent 会让用户去重建索引。`get_item_fulltext` 已能传父条目 key 并选择附件。
2. `get_item_fulltext` 传笔记 key 也报“Index the attachment…”。集合 key、不存在的 key 同样落入“未索引”。
3. 刚导入的附件一开始 `FULLTEXT_NOT_INDEXED` 且 `retryable=false`，稍后同一 key 就能读取。

2026-11-09 在容器里实测：`/items/<key>/fulltext` 对尚未索引的 PDF、从未索引的文本附件、链接 URL 附件、父条目、笔记、批注、集合和不存在的 key 全部返回相同的 404 `Not found`，没有任何响应体或响应头能区分。能区分它们的只有该 key 的条目元数据（`itemType`、`linkMode`、`contentType`、`dateAdded`）。

## 决定

1. `find_item_fulltext` 的 `attachment_key` 参数接受附件 key 或父条目 key。父条目 key 与 `get_item_fulltext` 共用同一个附件选择函数（`Library._select_fulltext_attachment`，包含 `links.enclosure` 首选、唯一附件、唯一 PDF、否则 `FULLTEXT_ATTACHMENT_AMBIGUOUS`）。结果中的 `attachment_key` 是实际选中的附件。参数名不变。
2. 先请求全文端点；只有失败后才读取该 key 的条目元数据来分类，所以常见成功路径没有额外请求：
   - 笔记、批注：`INVALID_ARGUMENT`，message 指向 `get_item_metadata`/`get_notes`/`get_annotations`，`details` 为 `{key, item_type}`。
   - 集合：`INVALID_ARGUMENT`，`details` 为 `{key, kind: "collection"}`；条目和集合都查不到：`NOT_FOUND`，`details` 为 `{key}`。
   - 把父条目 key 当作 `get_item_fulltext` 的显式 `attachment_key`：`INVALID_ARGUMENT`，`details` 为 `{key, kind: "parent_item"}`。
   - 父条目没有附件：沿用 `FULLTEXT_ATTACHMENT_NOT_FOUND`，message 改为说明没有附件，不再让用户去索引。
3. 附件没有索引时仍使用 `FULLTEXT_NOT_INDEXED`，新增 `details.reason`：
   - `not_indexable`（链接 URL，或 image/audio/video 内容类型）：`retryable=false`。
   - `indexing_pending`（`dateAdded` 在 10 分钟内）：`retryable=true`，message 建议稍后重试。
   - `not_indexed`（其余）：`retryable=false`，message 说明“刚添加的附件可能仍在建立索引，否则在 Zotero 中重建索引”。
   `details.attachment_key` 保留。

## 理由

- 复用选择函数让两个工具对同一个 key 给出一致的结果，也避免两份逻辑分叉。
- Zotero 不暴露索引队列状态，无法精确判断“正在索引”。`dateAdded` 只是启发式，但足以覆盖“刚导入就读取”这个实际出现过的场景，同时避免旧附件被标成可重试而让 agent 无限重试。
- `retryable` 按 `docs/errors-and-writes.md` 的定义只表示“可以安全地再次尝试”。读取操作总是安全的，所以只在有现实理由相信再试会成功时才设为 true。
- 保留错误码和 `details.attachment_key`，只新增 `details.reason`，旧客户端不受影响。`not_indexable` 的类型清单故意只列确定不会被索引的类型，宁可落入 `not_indexed`，也不误报“不可索引”。

## 考虑过的选项

- **只让 `find_item_fulltext` 报错并列出候选附件 key**：agent 仍要多调用一次，且两个工具行为不一致。放弃。
- **为 `not_indexable` 和 `indexing_pending` 新增错误码**：对客户端破坏更大，而 `details.reason` 已足够区分。放弃。
- **一律 `retryable=true`**：旧附件或扫描 PDF 会被重试到没有尽头。放弃。
- **在所有路径先读取条目元数据再读全文**：每次调用多一次请求，成功路径不需要。放弃。
- **`find_item_fulltext` 新增 `item_key` 参数**：改变接口形状，只为同一个概念多一个参数名。放弃。

## 重新评估的触发条件

- Zotero 提供索引队列或索引状态的接口（届时用真实状态代替 `dateAdded` 启发式）。
- 实际使用中发现 10 分钟窗口明显过短（大批量导入时索引排队很久）或过长。
- Zotero 开始索引目前列为 `not_indexable` 的类型。

# 错误与写入契约

本项目的写工具通过本地 Zotero API 操作。错误代码是机器可读契约，消息可以随版本改进，但代码不应被客户端当作自然语言解析。

## 错误 envelope

库层抛出 `LibraryError(code, message, retryable=False, details=None)`。MCP 层把它转换为 `CallToolResult`，并同时提供 `isError: true`、`structuredContent` 和可解析的文本块：

```json
{
  "ok": false,
  "status": "failed",
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "The item or collection changed. Re-read its current local version before retrying.",
    "retryable": false
  }
}
```

读工具的错误保留更小的 `{"error": {...}}` 形状。写工具错误额外带 `ok=false`，因此通过写工具声明的 output schema；两种表示的 JSON 内容一致。

当前稳定代码：

- `INVALID_ARGUMENT`：参数、输入字段或 MCP 输入校验不通过。`message` 只有一两句话，写明参数路径（如 `item_keys[3]`）、问题和改法；多个问题时列出前 3 条并注明总数。`details` 是前 5 条的 `type`、`loc`、`msg`，不回显输入值，也不带 pydantic 文档链接。`search_items` 的 `item_type` 不是 Zotero 已知类型时同样返回此码，见 [ADR 0006](adr/0006-short-argument-errors.md)。
- `NOT_FOUND`：条目、集合或附件不存在；`get_collection_items` 传入不存在的集合 key 也返回它，不会退回整库条目；`get_item_children`、`get_notes`（指定父条目时）、`get_annotations` 传入不存在的 key（包括集合 key）也返回它，而不是空列表。
- `NOT_CITABLE`：条目不能作为引用。`get_citation_items` 对子笔记、子附件、批注在该项上附带 `parent_key`（改用父条目），对独立笔记/附件不带；`export_items` 请求含子条目时也返回它（整个调用失败，`details.items` 逐项列出 `key`、`code`、`parent_key`）。
- `NO_INDEX`：通用的无索引错误；全文工具使用更具体的 `FULLTEXT_NOT_INDEXED`。
- `FULLTEXT_NOT_INDEXED`：全文工具没有可读取的附件索引，并在 `details.attachment_key` 保留来源。
- `FULLTEXT_ATTACHMENT_AMBIGUOUS`：自动选择有歧义，候选附件在 `details.candidates`。
- `FULLTEXT_REVISION_MISMATCH`：连续读取的文本 revision 已变化，`details` 保留 expected/actual。
- `UNAUTHORIZED`：本地授权缺失、撤销或被 Zotero 拒绝。
- `VERSION_CONFLICT`：本地条目版本或 Zotero server ID 不匹配。
- `CONNECTION_ERROR`：读操作或尚未开始写请求时无法连接 Zotero。
- `UNKNOWN_WRITE_RESULT`：写请求期间连接中断、响应不完整或无法确认结果。
- `CHILD_ITEM_NOT_ALLOWED`：`manage_collection_items` 加入集合时，批次里有子笔记/附件（带 `parentItem`）。Zotero 不允许子条目属于集合，整批在任何写入之前被拒绝，`details.children` 逐项列出 `key` 和 `parent_key`；改为加入父条目。
- `ATTACHMENT_NAME_AMBIGUOUS`：固定 Markdown 文件名对应多个已有附件，未创建新附件。
- `ATTACHMENT_CONTENT_CONFLICT`：固定 Markdown 文件名已有不同内容，未创建新附件。
- `ATTACHMENT_CONTENT_UNAVAILABLE`：已有固定 Markdown 附件无法读取，未创建新附件。
- `LIBRARY_LOCKED`：Zotero 正在锁定库。
- `UNSUPPORTED_VERSION`：客户端未使用本地 API，或 Zotero 未提供受支持的 server ID。

`retryable` 只表示客户端是否可以安全地再次尝试。写请求传输中断一律不是可自动重试；先读取库确认状态。`details` 只用于补充字段位置、操作名等结构化信息，客户端不应依赖其中所有字段都存在。

## 批量写入

`zotero_add_items`、标签批量修改和集合成员批量修改保留旧 aggregate 字段，同时返回 canonical `results`：

```json
{
  "ok": false,
  "status": "partial",
  "results": [
    {"input_index": 0, "key": "ABCD1234", "status": "success"},
    {"input_index": 1, "key": "SECOND02", "status": "failed", "error": {"code": "VERSION_CONFLICT"}},
    {"input_index": 2, "key": "THIRD003", "status": "pending"}
  ],
  "pending": ["THIRD003"]
}
```

每个结果的 `status` 只有 `success`、`unchanged`、`failed`、`pending` 或 `unknown`。`pending` 只用于本地批量循环明确尚未调用的输入；上游创建响应缺少 index 时必须使用 `unknown`，因为无法证明该输入未执行。此时 `error.code` 为 `UNKNOWN_WRITE_RESULT`、`retryable` 为 false。`input_index` 指向原始输入位置，`key` 在 Zotero 返回创建 key 或输入本来带 key 时保留。批次可以部分成功，不要重放已成功项或整个批次。

附件导入分为创建和上传两个阶段。上传失败时结果保留已创建的 `attachment_key`、`stage: "upload"` 以及条目级错误；应检查该附件后再决定后续动作，不能盲目重新创建。

## 版本与变更

更新和删除要求调用者传入最近读取到的本地 `version`。写成功结果只在 Zotero 或 pyzotero 确实返回新版本时才可携带版本；拿不到确认版本时不会猜测 `version + 1`，调用者应重读条目。`changes` 是补丁字段，不是 summary 或原生对象的无条件往返写回。

创建工具的 MCP annotation 将 `destructiveHint` 标记为 false；这只是客户端提示，不替代本地授权和版本约束，也不是额外的确认机制。

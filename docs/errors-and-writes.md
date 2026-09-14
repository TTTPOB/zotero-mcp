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

- `INVALID_ARGUMENT`：参数、输入字段或 MCP 输入校验不通过。
- `NOT_FOUND`：条目、集合或附件不存在。
- `NO_INDEX`：通用的无索引错误；全文工具使用更具体的 `FULLTEXT_NOT_INDEXED`。
- `FULLTEXT_NOT_INDEXED`：全文工具没有可读取的附件索引，并在 `details.attachment_key` 保留来源。
- `FULLTEXT_ATTACHMENT_AMBIGUOUS`：自动选择有歧义，候选附件在 `details.candidates`。
- `FULLTEXT_REVISION_MISMATCH`：连续读取的文本 revision 已变化，`details` 保留 expected/actual。
- `UNAUTHORIZED`：本地授权缺失、撤销或被 Zotero 拒绝。
- `VERSION_CONFLICT`：本地条目版本或 Zotero server ID 不匹配。
- `CONNECTION_ERROR`：读操作或尚未开始写请求时无法连接 Zotero。
- `UNKNOWN_WRITE_RESULT`：写请求期间连接中断、响应不完整或无法确认结果。
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

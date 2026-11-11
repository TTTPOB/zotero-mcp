# 错误与写入契约

本项目的写工具通过本地 Zotero API 操作。错误代码是机器可读契约，消息可以随版本改进，但代码不应被客户端当作自然语言解析。

## 错误 envelope

内部库层抛出继承自 `Exception` 的 `LibraryError(code, message, retryable=False, details=None)`；它不是受支持的 Python API。MCP 层把它转换为 `CallToolResult`，并同时提供 `isError: true`、`structuredContent` 和可解析的文本块：

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

- `INVALID_ARGUMENT`：参数、输入字段或 MCP 输入校验不通过。`message` 只有一两句话，写明参数路径（如 `item_keys[3]`）、问题和改法；多个问题时列出前 3 条并注明总数。`details` 是前 5 条的 `type`、`loc`、`msg`，不回显输入值，也不带 pydantic 文档链接。`search_items` 的 `item_type` 不是 Zotero 已知类型时同样返回此码，见 [ADR 0006](adr/0006-short-argument-errors.md)。`add_items` 和 `update_item` 都在写入前拒绝未知字段名，`add_items` 整批不写；校验用 Zotero 所有条目类型的字段并集，不检查字段是否属于该条目类型。全文工具收到笔记、批注、集合或（作为 `attachment_key` 的）父条目 key 时也返回它，`details.key` 保留该 key，`details.item_type` 或 `details.kind` 说明实际类型。
- `NOT_FOUND`：条目、集合或附件不存在；`get_collection_items` 传入不存在的集合 key 也返回它，不会退回整库条目；`get_item_children`、`get_notes`（指定父条目时）、`get_annotations` 传入不存在的 key（包括集合 key）也返回它，而不是空列表。写入工具 `update_item`、`delete_item`、`update_collection`、`delete_collection` 对不存在的 key 也返回它，`message` 带上 key 并建议重新搜索；其中 `update_item` 和 `update_collection` 会先确认对象存在再写入，避免 Zotero 按不存在的 key 新建条目或集合。全文工具收到不存在的 key 时也返回它，`details.key` 保留该 key。
- `NOT_CITABLE`：条目不能作为引用。`get_citation_items` 对子笔记、子附件、批注在该项上附带 `parent_key`（改用父条目），对独立笔记/附件不带；`export_items` 请求含子条目时也返回它（整个调用失败，`details.items` 逐项列出 `key`、`code`、`parent_key`）。
- `FULLTEXT_NOT_INDEXED`：全文工具没有可读取的附件索引，并在 `details.attachment_key` 保留来源。`details.reason` 区分三种情况：`indexing_pending`（附件十分钟内刚添加，Zotero 可能仍在建立索引，`retryable=true`，稍后重试同一个 key）、`not_indexable`（链接 URL 或图片/音频/视频附件，不会被索引，`retryable=false`，换一个附件）、`not_indexed`（其余情况，`retryable=false`，在 Zotero 中重建索引）。Zotero 对这三种情况的全文请求都只返回同样的 404，所以这是根据附件元数据做的判断。
- `FULLTEXT_ATTACHMENT_NOT_FOUND`：父条目没有任何附件。
- `FULLTEXT_ATTACHMENT_AMBIGUOUS`：自动选择有歧义，候选附件在 `details.candidates`。
- `FULLTEXT_REVISION_MISMATCH`：连续读取的文本 revision 已变化，`details` 保留 expected/actual。
- `UNAUTHORIZED`：本地授权缺失、撤销或被 Zotero 拒绝。
- `VERSION_CONFLICT`：本地条目版本或 Zotero server ID 不匹配。`update_item`、`delete_item`、`update_collection`、`delete_collection` 因版本过期而冲突时，`error.details.current_version` 是该条目或集合当前的本地版本（读取失败时省略该字段，此时先重读），`message` 说明可在确认自己的修改仍然适用后用该版本重新提交；`details` 还带 `operation` 和 `key`。批量标签/集合修改里，同样的 `error` 在对应的 `failed` 项中。Zotero server ID 不匹配没有 `current_version`。
- `CONNECTION_ERROR`：读操作或尚未开始写请求时无法连接 Zotero。
- `UNKNOWN_WRITE_RESULT`：写请求期间连接中断、响应不完整或无法确认结果。
- `CHILD_ITEM_NOT_ALLOWED`：`manage_collection_items` 加入集合时，批次里有子笔记/附件（带 `parentItem`）。Zotero 不允许子条目属于集合，整批在任何写入之前被拒绝，`details.children` 逐项列出 `key` 和 `parent_key`；改为加入父条目。
- `ATTACHMENT_NAME_AMBIGUOUS`：固定 Markdown 文件名对应多个已有附件，未创建新附件。
- `ATTACHMENT_CONTENT_CONFLICT`：固定 Markdown 文件名已有不同内容，未创建新附件。
- `ATTACHMENT_CONTENT_UNAVAILABLE`：已有固定 Markdown 附件无法读取，未创建新附件。
- `LIBRARY_LOCKED`：Zotero 正在锁定库。
- `UNSUPPORTED_VERSION`：客户端未使用本地 API，或 Zotero 未提供受支持的 server ID。

`retryable` 只表示客户端是否可以安全地再次尝试。写请求传输中断一律不是可自动重试；先读取库确认状态。`VERSION_CONFLICT` 也是 `retryable: false`：原样重发必然再次冲突，用 `current_version` 重新提交会覆盖别人的修改，必须由调用者先确认自己的修改仍然适用，因此不是可以自动重试的情形。`details` 只用于补充字段位置、操作名等结构化信息，客户端不应依赖其中所有字段都存在。

## 写入结果

所有写工具返回 `ok`、`status`、`results`。每个 `results` 项含 `input_index`、`status`，以及已知的 `key`、`version`、`stage`、`error` 或 `warnings`。未设置的字段直接省略，不用 `null`、空对象或空数组占位。

单目标工具（`update_item`、`delete_item`、`update_collection`、`delete_collection`、`create_note`、`create_collection`、`attach_file`、`attach_markdown`）还在顶层返回已知的 `key` 和 `version`；附件的 `key` 就是附件 key。`add_items`、`batch_update_tags`、`manage_collection_items` 只通过 `results` 报告各项。顶层 `error` 用于整个调用的错误或单目标失败的补充说明。

```json
{
  "ok": true,
  "status": "success",
  "results": [{"input_index": 0, "status": "success", "key": "ABCD1234", "version": 12}],
  "key": "ABCD1234",
  "version": 12
}
```

### 批量写入与恢复

```json
{
  "ok": false,
  "status": "partial",
  "results": [
    {"input_index": 0, "key": "ABCD1234", "status": "success", "version": 12},
    {"input_index": 1, "key": "SECOND02", "status": "failed", "error": {
      "code": "VERSION_CONFLICT", "message": "The item changed; confirm your changes still apply before resubmitting.",
      "retryable": false, "details": {"current_version": 14}
    }},
    {"input_index": 2, "key": "THIRD003", "status": "pending"}
  ]
}
```

结果项的 `status` 为 `success`、`unchanged`、`failed`、`pending` 或 `unknown`。`input_index` 指向原始输入位置；`key` 在 Zotero 返回创建 key 或输入本来带 key 时保留。批量标签和集合归属修改在第一项失败后停止，后续输入为 `pending`；`pending` 只表示本地循环明确尚未调用的输入。上游创建响应缺少 index 时必须使用 `unknown`，因为无法证明该输入未执行；其 `error.code` 为 `UNKNOWN_WRITE_RESULT`。

顶层 `status=failed`（没有任何成功或未变化项）或 `unknown` 时，MCP `isError=true`，逐项结果仍可读取。`partial` 的 `isError=false`；只要有失败或未确认项，`ok=false`。批次可以部分成功，不要重放已成功项或整个批次；未确认项应先读取库确认状态。

每项 `error` 都是 `{code, message, retryable, details?}`，不解析英文消息判断类型。Zotero 创建失败项的 HTTP 数字码映射为：400 → `INVALID_ARGUMENT`，404 → `NOT_FOUND`，412 → `VERSION_CONFLICT`，其他 → `UNKNOWN_ERROR`；原数字保留在 `details.http_status`。例如，不存在父条目的笔记在 Zotero 10.0.6 中返回 400，因此是 `INVALID_ARGUMENT`，不是根据消息文字推测出的 `NOT_FOUND`。

附件导入分为创建和上传两个阶段。上传未确认时，结果保留已创建的顶层 `key` 和 `results[0].key`，阶段在 `results[0].stage: "upload"`，错误说明也保留恢复所需信息。先检查该附件，不要盲目重新创建。`attach_markdown` 同内容复用已有附件，结果项为 `unchanged`；同名异内容、多同名或已有附件无法读取时不创建第二份，内容冲突的摘要在 `error.details` 中。

### 版本与变更

更新和删除要求调用者传入最近读取到的本地 `version`。`add_items` 的 `results[i].version` 来自 Zotero 创建响应；`create_note`、`create_collection` 同时在顶层和结果项返回响应里的本地版本。可直接把它用于下一次更新，不必先读取。

`update_item`、`update_collection` 写入成功后读回对象，在顶层和 `results[0].version` 返回实际本地版本。批量标签和集合归属修改对每个有变更的条目执行一次读取、一次写入、一次读回，在 `results[i].version` 返回新版本；未变化项为 `unchanged`，可带读取时的版本。读回失败只省略版本，不改变已确认写入的成功状态；不会猜测 `version + 1`。`Last-Modified-Version` 是库版本，不能作为无变化或被规范化为无变化的条目版本。

`attach_file` 和 `attach_markdown` 的上传路径不返回版本：Zotero 可能在上传响应之后再次修改附件，上传结果版本不能作为下一次编辑的可靠输入。编辑附件前先读取它。`attach_markdown` 复用已有同内容附件时可返回读取到的版本。`delete_*` 没有新版本。版本缺失时，下一次修改前先读取对象。

`changes` 是补丁字段，不是 summary 或原生对象的无条件往返写回。数组字段整体替换，增删标签优先用 `batch_update_tags`，修改集合归属优先用 `manage_collection_items`。

### 存储差异警告

Zotero 可能接受不属于该条目类型的字段或作者角色，但存储时把字段放入 Extra、丢弃字段或替换作者角色。`add_items`、`create_note`、`update_item` 对照提交数据与实际存储数据，在相关的 `results[i].warnings` 返回简短英文提示，包含字段路径、条目类型及 `get_item_schema` 的检查建议；成功状态保持不变。没有差异警告时省略 `warnings`。

警告覆盖非空提交字段在存储对象中缺失，以及 `creators[n].creatorType` 被替换。字段值明确映射到另一个存储字段时不警告；标签字符串转对象、日期转字符串、去除首尾空格等正常规范化不警告。这不是完整的值校验或差异报告。创建比较直接使用 Zotero 响应里的已存储 `data`，不增加请求；更新复用版本读回的数据，读回失败时既没有版本也没有警告。

创建工具的 MCP annotation 将 `destructiveHint` 标记为 false；这只是客户端提示，不替代本地授权和版本约束，也不是额外的确认机制。

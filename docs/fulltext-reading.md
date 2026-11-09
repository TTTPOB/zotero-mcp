# 全文阅读

`zotero_get_item_fulltext` 只读取 Zotero 已建立索引的附件文本，不下载附件、不做 OCR，也不重新解析 PDF。

## 选择附件

可以传入父文献的 `item_key`，也可以显式传入 `attachment_key`。`find_item_fulltext` 的 `attachment_key` 同样接受父文献 key，使用与本节相同的选择规则（共用同一段代码），成功结果中的 `attachment_key` 是实际选中的附件；候选有歧义时同样返回 `FULLTEXT_ATTACHMENT_AMBIGUOUS`。

- 显式 `attachment_key` 优先，适合补充材料、不同版本或连续阅读。
- 如果父条目的 `links.enclosure` 已包含附件 key，且该 key 严格匹配子附件候选，使用该附件作为首选。这里只解析已有链接，不请求其 URL。
- 父文献只有一个子附件时，使用该附件。
- 父文献有多个附件但只有一个 `application/pdf` 时，使用这个 PDF。
- 多个 PDF，或多个非 PDF 且没有唯一首选时，不会逐个试读后静默替换。工具返回 `FULLTEXT_ATTACHMENT_AMBIGUOUS`，错误详情只包含最多 5 个小型候选摘要。根据候选的 `attachment_key` 再次显式调用。
- 候选的 `index_state` 是 `unknown`。附件存在、附件是 PDF、索引可读是三个不同状态；只有实际读取成功才能确认有文本。

成功结果中的 `attachment_key` 是本次实际读取的附件。不要在后续分页中重新让父文献自动选择附件。

## 连续读取

第一次调用可以使用父文献 key：

```json
{
  "item_key": "ABCD1234",
  "max_characters": 20000
}
```

保存响应中的 `attachment_key`、`text_revision` 和 `next_offset`。后续调用使用实际附件 key，并传回同一个 revision：

```json
{
  "item_key": "ABCD1234",
  "attachment_key": "PDF00001",
  "offset": 20000,
  "max_characters": 20000,
  "text_revision": "sha256:..."
}
```

实现不使用缓存或不透明游标。每次调用都会从 Zotero 重新取得该附件的完整索引文本，并对 UTF-8 编码的原文计算 SHA-256。revision 不匹配时返回 `FULLTEXT_REVISION_MISMATCH`，不会用新文本静默接续旧 offset；应从新结果的 offset 0 重新开始阅读。

`offset` 是从 0 开始的 Unicode code point 索引，不是字节偏移，也不是 UTF-16 code unit。`next_offset` 是下一次调用应使用的 code point 起点；为 `null` 表示已读到文本末尾。`total_characters` 同样按 code point 计数。

## 覆盖范围

返回结果同时保留 Zotero 原始的 `index` 字段，并提供两个明确的覆盖字段：

- `index_coverage.indexed_pages` / `total_pages` 是 Zotero 报告的索引页数。缺失时为 `null`，不会从附件类型或页数推断。索引页数不保证表格、公式、图片文字或文本顺序准确。
- `reading_coverage.start` / `end` 是本次返回片段在完整索引文本中的 code point 区间，`reached_end` 表示本次片段是否触及文本末尾，`complete` 只有在本次片段从 0 开始并覆盖到末尾时才为 true。它表示本工具返回给调用者的阅读范围，不表示已经读完论文。

没有可读索引时返回 `FULLTEXT_NOT_INDEXED`。该错误不代表附件不存在，只表示此次 Zotero 全文端点没有可用文本。Zotero 对“尚未索引”“永远不会索引”“key 根本不是附件”都返回同一个 404，因此工具在失败后再读取该 key 的条目元数据来区分：

- key 是笔记或批注：返回 `INVALID_ARGUMENT`，提示改用 `get_item_metadata`/`get_notes`（笔记）或 `get_annotations`（批注）读取，独立笔记和子笔记相同。key 是集合返回 `INVALID_ARGUMENT`，不存在返回 `NOT_FOUND`。
- 附件是链接 URL 或图片/音频/视频：`details.reason=not_indexable`，`retryable=false`。
- 其他附件按 `dateAdded` 判断：十分钟内为 `details.reason=indexing_pending`、`retryable=true`，建议稍后重试；更早的为 `not_indexed`、`retryable=false`，建议在 Zotero 中重建索引。这是启发式判断，Zotero 没有公开索引队列状态；没有文本层的扫描 PDF 也会落入 `not_indexed`。

## 附件内查找

`zotero_find_item_fulltext` 用于在**一个附件**的 Zotero 索引文本内跳读（传父文献 key 时自动选择附件，规则同上）：

```json
{
  "attachment_key": "PDF00001",
  "query": "mitochondrial",
  "context_characters": 120,
  "max_matches": 20
}
```

匹配规则固定为 Unicode `casefold` 后的字面量子串匹配：大小写不敏感，不使用正则表达式，不搜索整个资料库；重叠的不同原文区间会保留，casefold 展开造成的相同原文区间只计一次。结果中的 `start` 和 `end` 是原始未修改文本的 0 起始 code point 区间，`snippet_start`、`snippet_end` 是上下文片段在同一原文中的实际区间。`match_count` 是本次返回的数量，`total_matches` 是完整扫描后的唯一区间数量；达到 `max_matches` 时以 `has_more=true`、`truncated=true` 明确提示仍有结果。`text_revision` 可按连续读取的相同方式校验正文没有变化。

该能力仍会让 Zotero 返回指定附件的完整索引文本，再在 MCP 进程内定位片段；它是轻量跳读工具，不是新的全文索引、向量检索或 PDF 解析器。

## 原始 Markdown 附件

`zotero_read_attachment_text` 与 `zotero_get_item_fulltext` 不同：它请求 Zotero 的 `/file` 端点，读取附件原始 bytes，再按 UTF-8 解码。不会使用 Zotero 索引，也不会规范化换行、Markdown 或 Unicode。正常情况下只接受 `text/markdown` 或 `text/plain` 且文件名扩展名为 `.md`、`.markdown` 或 `.txt` 的附件；为兼容旧版 Windows 上传的 `.md/.markdown` 附件，也接受 `application/octet-stream` 或缺失 MIME，但仍必须通过二进制签名、UTF-8 和 50 MiB 检查。若 Zotero 将文件重定向到 `file://`，服务端只接受空 host 或 `localhost` 的本地 URI，并拒绝远程路径。

返回的 `content_sha256` 是原始 bytes 的 SHA-256。分页时客户端应比较每页返回的相同 hash，并使用 `offset`、`next_offset` 和 `total_characters` 拼接原文；该 hash 不等同于索引读取的 `text_revision`。

`zotero_attach_markdown` 在服务端创建临时的固定名称 `paper-chew-fulltext.md` 文件，然后复用 `attach_file` 导入。调用前会检查同一父条目的同名附件：内容相同返回 `unchanged`；内容不同、多同名或原始内容不可读取时返回明确错误，不创建重复附件，也不会自动删除已有附件。

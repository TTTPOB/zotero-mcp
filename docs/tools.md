# 工具速查

默认连接本机个人库。所有工具使用 `zotero_` 前缀，例如 `zotero_search_items`。

## 读取

| 工具 | 用途 |
| --- | --- |
| `search_items` | 搜索标题/作者/年份、标签；`qmode=everything` 包含 Zotero 索引的文本 |
| `get_item_metadata` | 默认读取语义元数据、`key` 和本地 `version`；`view=raw` 获取原生对象 |
| `get_item_children` | 列出子附件、笔记或批注 |
| `get_collections` | 列出集合及其父集合和版本 |
| `get_collection_items` | 列出直接属于某集合的条目 |
| `get_tags` | 列出标签 |
| `get_recent` | 按添加时间列出最近的顶层条目 |
| `get_notes` | 读取笔记，可指定父条目 |
| `get_annotations` | 读取指定附件的 Zotero 批注 |
| `get_item_fulltext` | 读取选定附件的 Zotero 索引文本，按字符分页并校验文本版本 |
| `find_item_fulltext` | 在指定附件的索引文本中查找字面量，返回附近片段和原文字符位置 |
| `read_attachment_text` | 从安全的 Markdown/text 附件读取原始 UTF-8 文件内容，不使用 Zotero 索引 |
| `get_item_schema` | 从本地 Zotero 查询条目类型、字段和作者类型 |
| `export_items` | 通过本地 Zotero 导出 BibTeX、RIS、CSL JSON |

搜索、最近条目、集合条目、子条目列表默认使用 `view=summary`；`view=metadata` 保留语义元数据，`view=raw` 返回原生记录。笔记和批注列表默认 metadata，保留正文。视图不截断完整标题、不静默隐藏独立附件，也不从附件存在推断索引可读。

搜索需要 `query` 或有效 `tags` 至少一项，tags-only 合法。`qmode=everything` 默认不排除附件，显式 `item_type` 筛选原生命中对象；结果提供父 key，保持上游顺序而不合并父子条目。`tag_scope=matched_item`（默认）由 Zotero 筛选命中对象的标签，`parent` / `either` 在当前原生页内检查相应层级。

列表工具接受 `limit`（1–100）和 `offset`。始终用返回的 `next_offset` 续页，`null` 表示结束；父标签过滤后即使本页为空，也可能还有下一页。分页使用原生页的已消费位置及响应信息；上游没有可靠分页信息且最后一页刚好满页时，可能需要再请求一次空页。它不提供库修改期间的快照保证。

全文工具接受论文或附件 key。传父文献时透明选择附件；无法确定时返回候选清单，再指定 `attachment_key`。续读传回实际附件 key 和 `text_revision`，文本改变会要求重新开始。偏移以 0 起始 Unicode code point 计数，`max_characters` 为 1–50,000。索引页数和本次返回的文本范围分别表达，不表示 Agent 已通读附件。

`find_item_fulltext` 只查指定附件，使用不区分大小写的字面量匹配，返回片段及原文字符位置，不进行全库扫描。`read_attachment_text` 则读取附件文件本身：仅接受 `text/markdown` 或 `text/plain` 且扩展名为 `.md`、`.markdown` 或 `.txt` 的附件，按 UTF-8 原样解码，单文件上限 50 MiB。它返回原始 bytes 的 SHA-256，不使用索引的 `text_revision`。详见 [读取视图](read-views.md) 与 [全文阅读](fulltext-reading.md)。

## 写入

先执行 `zotero-mcp authorize`，在 Zotero 中选择“始终允许”。

| 工具 | 用途 |
| --- | --- |
| `add_items` | 创建 1–50 个条目、笔记或批注，传入 Zotero item-data 对象 |
| `update_item` | 按 key、version 修改指定字段；适用于笔记、批注 |
| `delete_item` | 按 key、version 永久删除条目及其子项 |
| `create_note` | 创建附属于文献的子笔记；默认转义纯文本 |
| `create_collection` | 创建根集合或子集合 |
| `update_collection` | 按 key、version 重命名或移动集合 |
| `delete_collection` | 永久删除集合及子集合，保留文献 |
| `batch_update_tags` | 批量增删标签，保留未修改的标签和标签类型 |
| `manage_collection_items` | 批量加入/移出集合，保留其他集合归属 |
| `attach_file` | 将绝对路径的本地文件复制到 Zotero 附件存储 |
| `attach_markdown` | 幂等创建固定名称 `paper-chew-fulltext.md` Markdown 附件 |

修改或删除条目前，先读取其 `version`。集合改名/移动也需要本地版本。发生版本冲突时，重新读取后决定如何修改；程序不会用旧数据重试覆盖。

`update_item` 只更改你传入的字段，但数组字段会整体替换。因此，增删标签优先使用 `batch_update_tags`，调整集合归属优先使用 `manage_collection_items`。

批量标签或集合归属修改遇到错误时会停止。`results` 按输入索引和 key 关联逐项结果，旧的 `updated`、`unchanged`、`failed`、`pending` 等聚合字段继续保留。批量创建遵循 Zotero 的逐条结果，成功和失败可同时存在；响应未确认的输入需要检查库状态，不能当作未执行直接重放。

附件上传若在创建条目之后失败，保留 `attachment_key` 和 `stage`。先检查该附件，避免重新调用产生重复项。工具级错误使用稳定的 `code`、`message`、`retryable` 和 MCP `isError=true`；部分批量结果保留已完成项，恢复方法见 [错误与写入](errors-and-writes.md)。`attach_markdown` 固定使用 `paper-chew-fulltext.md`，同内容附件返回 `unchanged`，同名异内容、多同名或已有附件无法读取时不会创建第二份，也不会自动删除。

更新后没有服务器确认的新版本时，下一次修改前重新读取，不自行把旧版本加一。

## 示例

创建一条图书：

```json
{
  "items": [{
    "itemType": "book",
    "title": "示例图书",
    "creators": [{"creatorType": "author", "lastName": "张", "firstName": "三"}],
    "date": "2026",
    "tags": [{"tag": "待读"}]
  }]
}
```

以上参数传给 `zotero_add_items`。字段以 `zotero_get_item_schema` 的本地查询结果为准。项目不做 DOI/ISBN 在线补全；你可以让助手准备元数据后创建条目。

更改标题（示例 key 和版本需要替换为实际读取值）：

```json
{"item_key": "ABCD1234", "version": 7, "changes": {"title": "新的标题"}}
```

以上参数传给 `zotero_update_item`。

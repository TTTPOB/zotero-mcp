# 工具速查

默认连接本机个人库。所有工具使用 `zotero_` 前缀，例如 `zotero_search_items`。

## 读取

| 工具 | 用途 |
| --- | --- |
| `search_items` | 搜索标题/作者/年份、标签；`qmode=everything` 包含 Zotero 索引的文本 |
| `get_item_metadata` | 读取完整条目、`key` 和本地 `version` |
| `get_item_children` | 列出子附件、笔记或批注 |
| `get_collections` | 列出集合及其父集合和版本 |
| `get_collection_items` | 列出直接属于某集合的条目 |
| `get_tags` | 列出标签 |
| `get_recent` | 按添加时间列出最近的顶层条目 |
| `get_notes` | 读取笔记，可指定父条目 |
| `get_annotations` | 读取指定附件的 Zotero 批注 |
| `get_item_fulltext` | 读取 Zotero 已索引全文，按字符分页 |
| `get_item_schema` | 从本地 Zotero 查询条目类型、字段和作者类型 |
| `export_items` | 通过本地 Zotero 导出 BibTeX、RIS、CSL JSON |

列表工具接受 `limit`（1–100）和 `offset`。返回 `next_offset` 后，用它继续读取；`null` 表示结束。最后一页刚好达到 limit 时，可能需要再请求一次空页。

全文工具接受论文或附件 key。传论文 key 时优先选择有索引的 PDF；要读其他附件，先调用 `get_item_children`，再传附件 key。`offset` 和 `max_characters` 按 Unicode 字符计数。`index` 返回 Zotero 提供的索引覆盖信息，索引可能只覆盖附件的部分页面。

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

修改或删除条目前，先读取其 `version`。集合改名/移动也需要本地版本。发生版本冲突时，重新读取后决定如何修改；程序不会用旧数据重试覆盖。

`update_item` 只更改你传入的字段，但数组字段会整体替换。因此，增删标签优先使用 `batch_update_tags`，调整集合归属优先使用 `manage_collection_items`。

批量标签或集合归属修改遇到错误时会停止。返回值中的 `updated`、`unchanged`、`failed`、`pending` 分别列出已修改、未变化、失败及未处理的 key。批量创建则遵循 Zotero 的逐条结果，`success`/`successful` 和 `failed` 可以同时有内容。

附件上传若在创建条目之后失败，返回 `ok=false` 和 `attachment_key`。先检查该附件，避免重新调用产生重复项。

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

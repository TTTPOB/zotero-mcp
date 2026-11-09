# 工具速查

默认连接本机个人库。所有工具使用 `zotero_` 前缀，例如 `zotero_search_items`。

## 读取

| 工具 | 用途 |
| --- | --- |
| `search_items` | 搜索标题/作者/年份、标签；`qmode=everything` 包含 Zotero 索引的文本 |
| `get_item_metadata` | 一次按 `item_keys`（1–50 个）读取语义元数据、`key` 和本地 `version`，子笔记/附件也可读；`view=raw` 获取原生对象 |
| `get_item_children` | 列出子附件、笔记或批注；key 不存在（包括传入集合 key）返回 `NOT_FOUND`，只有确实没有子条目才返回空列表 |
| `get_collections` | 列出集合及其父集合和版本 |
| `get_collection_items` | 列出直接属于某集合的顶层条目（独立附件、独立笔记也是顶层，仍会列出）；不含子附件/子笔记，子条目用 `get_item_children`，summary 的 `fulltext.candidate_attachment_key` 给出最佳附件；key 不是已有集合（包括传入条目 key）时返回 `NOT_FOUND`，不会退回整库条目，集合 key 用 `get_collections` 查 |
| `get_tags` | 列出标签名；`items` 为 `{"tag": "名称"}` 数组，不含标签类型和条目数 |
| `get_recent` | 按添加时间列出最近的顶层条目 |
| `get_notes` | 读取笔记，可指定父条目（父条目不存在返回 `NOT_FOUND`） |
| `get_annotations` | 读取指定附件的 Zotero 批注（附件 key 不存在返回 `NOT_FOUND`） |
| `get_item_fulltext` | 读取选定附件的 Zotero 索引文本，按字符分页并校验文本版本 |
| `find_item_fulltext` | 在指定附件的索引文本中查找字面量，返回附近片段和原文字符位置 |
| `read_attachment_text` | 从安全的 Markdown/text 附件读取原始 UTF-8 文件内容，不使用 Zotero 索引 |
| `get_item_schema` | 从本地 Zotero 查询条目类型、字段和作者类型 |
| `export_items` | 通过本地 Zotero 导出顶层条目的 BibTeX、RIS、CSL JSON，不包含子笔记/附件 |
| `get_citation_items` | 按输入 key 获取规范引用 URI、选择链接、citation key 和 CSL JSON |

搜索、最近条目、集合条目、子条目列表默认使用 `view=summary`；`view=metadata` 保留语义元数据，`view=raw` 返回原生记录。笔记和批注列表默认 metadata，保留正文。视图不截断完整标题、不静默隐藏独立附件，也不从附件存在推断索引可读。

搜索需要 `query` 或有效 `tags` 至少一项，tags-only 合法。`qmode=everything` 默认不排除附件，显式 `item_type` 筛选原生命中对象（空字符串或纯空白等同于不传；支持 `book || journalArticle`、`-attachment` 组合写法，类型名不是 Zotero 已知类型时返回 `INVALID_ARGUMENT`，可用 `get_item_schema` 查看）；结果提供父 key，保持上游顺序而不合并父子条目。`tag_scope=matched_item`（默认）由 Zotero 筛选命中对象的标签，`parent` / `either` 在当前原生页内检查相应层级。

按 DOI/ISBN/URL 或其他非标题字段查找时，使用 `qmode="everything"` 配合 `item_type="-attachment"`，并核对返回的 `doi` 是否与目标 DOI 一致；默认模式可能漏掉 DOI，不排除附件则可能命中引用该 DOI 的其他论文全文。

列表工具接受 `limit`（1–100）和 `offset`。始终用返回的 `next_offset` 续页，`null` 表示结束；父标签过滤后即使本页为空，也可能还有下一页。分页使用原生页的已消费位置及响应信息；上游没有可靠分页信息且最后一页刚好满页时，可能需要再请求一次空页。它不提供库修改期间的快照保证。

全文工具接受论文或附件 key。传父文献时透明选择附件；无法确定时返回候选清单，再指定 `attachment_key`。续读传回实际附件 key 和 `text_revision`，文本改变会要求重新开始。偏移以 0 起始 Unicode code point 计数，`max_characters` 为 1–50,000。索引页数和本次返回的文本范围分别表达，不表示 Agent 已通读附件。

`find_item_fulltext` 只查指定附件，使用不区分大小写的字面量匹配，返回片段及原文字符位置，不进行全库扫描。`read_attachment_text` 则读取附件文件本身：正常接受 `text/markdown` 或 `text/plain` 以及 `.md`、`.markdown`、`.txt` 扩展名；兼容旧的 `.md/.markdown` generic MIME，但仍要求原始 bytes 通过二进制签名、UTF-8 和 50 MiB 检查。它返回原始 bytes 的 SHA-256，不使用索引的 `text_revision`。详见 [读取视图](read-views.md) 与 [全文阅读](fulltext-reading.md)。

批量元数据：`get_item_metadata` 接受 `item_keys`（1–50 个，不再有单数 `item_key`），按输入顺序（包括重复 key）返回 `{"items": [...]}`；每项为 `{index, key, status, item}`，`status` 为 `ok` 时 `item` 是所选视图的条目，找不到的 key 为 `status: "error"` 并带 `error.code = "NOT_FOUND"`，其余 key 不受影响。它读取 `/items` 而不是 `/items/top`，所以子笔记和附件同样可读；结果只包含请求的 key，不会混入其子条目。

每次 `get_citation_items` 接受 1–50 个 key，按输入顺序（包括重复 key）返回逐项状态。Word/LibreOffice 的 Zotero 字段使用 `uri`（必须以 `http://zotero.org/` 开头）；笔记链接使用 `select_link`，Pandoc/LaTeX 使用 `citation_key` 和 `csl`。不要把 `links.alternate.href` 网页地址作为引用 URI。没有 citation key 时返回 null，不自行生成；CSL 对象保持原样，其 `id` 可能是 citation key，不是查询用的条目 key。子笔记、子附件和批注返回 `NOT_CITABLE`，并在该项上带 `parent_key`，改用它作为引用对象（批注的 `parent_key` 是所属附件，该附件的父条目才是要引用的文献）；不存在的 key（包括集合 key）返回 `NOT_FOUND`；独立笔记/附件返回 `NOT_CITABLE`，没有 `parent_key`；无效 CSL 数据返回 `CITATION_DATA_INVALID`。只有 `/items/top` 没有返回某个 key 时，才多发一次批量请求区分子条目和不存在的 key。

`export_items` 同样接受 1–50 个 key，只导出顶层条目，不附带子笔记/附件。请求里含子笔记、子附件或批注的 key 时整个调用失败，返回 `NOT_CITABLE`；含不存在的 key 时返回 `NOT_FOUND`；两种情况下 `error.details.items` 逐项给出 `key`、`code` 和（子条目的）`parent_key`，不会返回部分内容。判断依据是 Zotero 响应的 `Total-Results`，正常请求不增加请求；独立笔记/附件没有 BibTeX/RIS 表示，Zotero 会静默跳过，这里不检测。BibTeX、RIS 的 `content` 是文本；CSL JSON 的 `content` 是解析后的 CSL 对象数组，可直接写成 Pandoc 参考文献文件。CSL JSON 中的 `id` 在 Zotero 有 citation key 时使用该值，否则为条目 URI；需要按 key 可靠关联引用数据时使用 `get_citation_items`。

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
| `manage_collection_items` | 批量加入/移出集合，保留其他集合归属；加入时若含子笔记/附件，整批拒绝并返回 `CHILD_ITEM_NOT_ALLOWED` |
| `attach_file` | 将绝对路径的本地文件复制到 Zotero 附件存储 |
| `attach_markdown` | 幂等创建固定名称 `paper-chew-fulltext.md` Markdown 附件 |

修改或删除条目前，先读取其 `version`。key 不存在时 `update_item`、`delete_item`、`update_collection`、`delete_collection` 返回 `NOT_FOUND`，重新搜索取得 key。集合改名/移动也需要本地版本。发生版本冲突时，重新读取后决定如何修改；程序不会用旧数据重试覆盖。

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

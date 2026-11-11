# 工具速查

默认连接本机个人库。所有工具使用 `zotero_` 前缀，例如 `zotero_search_items`。

## 读取

| 工具 | 用途 |
| --- | --- |
| `search_items` | 搜索标题/作者/年份、标签；`qmode=everything` 包含 Zotero 索引的文本；整串是 DOI/ISBN 时精确查找 |
| `get_item_metadata` | 一次按 `item_keys`（1–50 个）读取语义元数据、`key` 和本地 `version`，子笔记/附件也可读；`view=raw` 获取原生对象 |
| `get_item_children` | 列出子附件、笔记或批注；key 不存在（包括传入集合 key）返回 `NOT_FOUND`，只有确实没有子条目才返回空列表 |
| `get_collections` | 列出集合及其父集合和版本 |
| `get_collection_items` | 列出直接属于某集合的顶层条目（独立附件、独立笔记也是顶层，仍会列出）；不含子附件/子笔记，子条目用 `get_item_children`，summary 的 `fulltext.candidate_attachment_key` 给出最佳附件；key 不是已有集合（包括传入条目 key）时返回 `NOT_FOUND`，不会退回整库条目，集合 key 用 `get_collections` 查 |
| `get_tags` | 列出标签名；`items` 为 `{"tag": "名称"}` 数组，不含标签类型和条目数 |
| `get_recent` | 按添加时间列出最近的顶层条目 |
| `get_notes` | 读取笔记，可指定父条目（父条目不存在返回 `NOT_FOUND`） |
| `get_annotations` | 读取指定附件的 Zotero 批注（附件 key 不存在返回 `NOT_FOUND`） |
| `get_item_fulltext` | 读取选定附件的 Zotero 索引文本，按字符分页并校验文本版本 |
| `find_item_fulltext` | 在一个附件（或父文献自动选出的附件）的索引文本中查找字面量，返回附近片段和原文字符位置 |
| `read_attachment_text` | 从安全的 Markdown/text 附件读取原始 UTF-8 文件内容，不使用 Zotero 索引 |
| `get_item_schema` | 从本地 Zotero 查询条目类型、字段和作者类型 |
| `export_items` | 通过本地 Zotero 导出顶层条目的 BibTeX、RIS、CSL JSON，不包含子笔记/附件 |
| `get_citation_items` | 按输入 key 获取规范引用 URI、选择链接、citation key 和 CSL JSON |

搜索、最近条目、集合条目、子条目列表默认使用 `view=summary`；`view=metadata` 保留语义元数据，`view=raw` 返回原生记录。笔记和批注列表默认 metadata，保留正文。视图不截断完整标题、不静默隐藏独立附件，也不从附件存在推断索引可读。

搜索需要 `query` 或有效 `tags` 至少一项，tags-only 合法。`qmode=everything` 默认不排除附件，显式 `item_type` 筛选原生命中对象（空字符串或纯空白等同于不传；支持 `book || journalArticle`、`-attachment` 组合写法，类型名不是 Zotero 已知类型时返回 `INVALID_ARGUMENT`，可用 `get_item_schema` 查看）；结果提供父 key，保持上游顺序而不合并父子条目。`tag_scope=matched_item`（默认）由 Zotero 筛选命中对象的标签，`parent` / `either` 在当前原生页内检查相应层级。

整个 `query` 是一个 DOI（裸 DOI、`doi:` 前缀或 `https://doi.org/`、`http://dx.doi.org/` 链接，不区分大小写）或 ISBN-10/13（连字符、空格可有可无）时，`search_items` 进入**标识符精确查找**：只返回 DOI 字段（或 Extra 里的 `DOI:` 行）、ISBN 字段与之完全相等的顶层条目；只引用该 DOI 的论文、目标的子笔记和附件都不会返回，不需要 `qmode` / `item_type`。响应带 `identifier_lookup`（`type`、规范化后的 `value`、`match: "exact"`、`found`、每条命中的 `matches[].field`），0 条时 `found=false` 并附 `message`，表示库里没有该标识符。该模式下 `tags` / `tag_scope` 筛选命中条目本身，`offset`、`limit`、`next_offset`、`total` 只在精确命中的条目里翻页。详见 [读取视图与搜索](read-views.md)。

列表工具接受 `limit`（1–100）和 `offset`。始终用返回的 `next_offset` 续页，`null` 表示结束；父标签过滤后即使本页为空，也可能还有下一页。分页使用原生页的已消费位置及响应信息；上游没有可靠分页信息且最后一页刚好满页时，可能需要再请求一次空页。它不提供库修改期间的快照保证。

全文工具接受论文或附件 key。传父文献时透明选择附件；无法确定时返回候选清单，再指定 `attachment_key`。续读传回实际附件 key 和 `text_revision`，文本改变会要求重新开始。偏移以 0 起始 Unicode code point 计数，`max_characters` 为 1–50,000。索引页数和本次返回的文本范围分别表达，不表示 Agent 已通读附件。

`find_item_fulltext` 只查一个附件（可传附件 key，或父文献 key 自动选择附件），使用不区分大小写的字面量匹配，返回片段及原文字符位置，不进行全库扫描。`read_attachment_text` 则读取附件文件本身：正常接受 `text/markdown` 或 `text/plain` 以及 `.md`、`.markdown`、`.txt` 扩展名；兼容旧的 `.md/.markdown` generic MIME，但仍要求原始 bytes 通过二进制签名、UTF-8 和 50 MiB 检查。它返回原始 bytes 的 SHA-256，不使用索引的 `text_revision`。详见 [读取视图](read-views.md) 与 [全文阅读](fulltext-reading.md)。

批量元数据：`get_item_metadata` 接受 `item_keys`（1–50 个，不再有单数 `item_key`），按输入顺序（包括重复 key）返回 `{"items": [...]}`；每项为 `{index, key, status, item}`，`status` 为 `ok` 时 `item` 是所选视图的条目，找不到的 key 为 `status: "error"` 并带 `error.code = "NOT_FOUND"`，其余 key 不受影响。它读取 `/items` 而不是 `/items/top`，所以子笔记和附件同样可读；结果只包含请求的 key，不会混入其子条目。

每次 `get_citation_items` 接受 1–50 个 key，按输入顺序（包括重复 key）返回逐项状态。Word/LibreOffice 的 Zotero 字段使用 `uri`（必须以 `http://zotero.org/` 开头）；笔记链接使用 `select_link`，Pandoc/LaTeX 使用 `citation_key` 和 `csl`。不要把 `links.alternate.href` 网页地址作为引用 URI。没有 citation key 时返回 null，不自行生成；CSL 对象保持原样，其 `id` 可能是 citation key，不是查询用的条目 key。子笔记、子附件和批注返回 `NOT_CITABLE`，并在该项上带 `parent_key`，改用它作为引用对象（批注的 `parent_key` 是所属附件，该附件的父条目才是要引用的文献）；不存在的 key（包括集合 key）返回 `NOT_FOUND`；独立笔记/附件返回 `NOT_CITABLE`，没有 `parent_key`；无效 CSL 数据返回 `CITATION_DATA_INVALID`。只有 `/items/top` 没有返回某个 key 时，才多发一次批量请求区分子条目和不存在的 key。

`export_items` 同样接受 1–50 个 key，只导出顶层条目，不附带子笔记/附件。请求里含子笔记、子附件或批注的 key 时整个调用失败，返回 `NOT_CITABLE`；含不存在的 key 时返回 `NOT_FOUND`；两种情况下 `error.details.items` 逐项给出 `key`、`code` 和（子条目的）`parent_key`，不会返回部分内容。判断依据是 Zotero 响应的 `Total-Results`，正常请求不增加请求；独立笔记/附件没有 BibTeX/RIS 表示，Zotero 会静默跳过，这里不检测。BibTeX、RIS 的 `content` 是文本；CSL JSON 的 `content` 是解析后的 CSL 对象数组，可直接写成 Pandoc 参考文献文件。CSL JSON 中的 `id` 在 Zotero 有 citation key 时使用该值，否则为条目 URI；需要按 key 可靠关联引用数据时使用 `get_citation_items`。

## 写入

先执行 `zotero-mcp authorize`，在 Zotero 中选择“始终允许”。

| 工具 | 用途 |
| --- | --- |
| `add_items` | 创建 1–50 个条目、笔记或批注，传入 Zotero item-data 对象；含未知字段名时整批拒绝 |
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

所有写工具返回 `ok`、`status`、`results[]`；每项按 `input_index` 对应输入，带状态和已知的 `key`、`version`、`stage`、`error`。未设置字段直接省略。单目标工具还在顶层带已知的 `key`、`version`，附件的 `key` 是附件 key；`add_items`、`batch_update_tags`、`manage_collection_items` 只通过结果项报告。

修改或删除需要最近读取的本地 `version`。`add_items` 从创建响应返回 `results[i].version`；`create_note`、`create_collection` 也返回已知的新版本，可直接用于更新。`update_item`、`update_collection` 返回写入后读回的实际版本，批量标签/集合归属修改在各自结果项返回读回版本，未变化项可带当前版本。`attach_file` 和 `attach_markdown` 上传后不返回版本，编辑附件前先读取；`attach_markdown` 复用已有附件时可返回读取到的版本。删除没有新版本。无法取得版本时省略该字段，下次修改前先读取，不自行加一。

key 不存在时 `update_item`、`delete_item`、`update_collection`、`delete_collection` 返回 `NOT_FOUND`，重新搜索取得 key。版本冲突的 `error.details.current_version` 给出当前本地版本；确认自己的修改仍然适用后才用它重新提交，程序不会用旧数据重试覆盖。

`update_item` 只更改传入字段，但数组字段会整体替换。增删标签优先使用 `batch_update_tags`，调整集合归属优先使用 `manage_collection_items`。创建和单条更新若发现非空字段未按原字段存储，或作者角色被 Zotero 替换，会在 `results[i].warnings` 提示字段路径及 `get_item_schema`；这不把已完成的写入判为失败，也不警告普通规范化。

批量标签或集合归属修改遇到错误立即停止，后续结果项为 `pending`。批量创建遵循 Zotero 的逐条结果，成功和失败可同时存在；全部失败时顶层 `status=failed`、MCP `isError=true`，部分成功时 `status=partial`、`isError=false`。未确认的写入为 `unknown`、`isError=true`；先检查库状态，不直接重放。每项错误统一为 `{code, message, retryable, details?}`；创建失败的原 HTTP 数字码在 `details.http_status`。

附件上传未确认时保留已创建的顶层 `key` 及 `results[0].key`，阶段在 `results[0].stage`。先检查该附件，避免重新调用产生重复项。`attach_markdown` 固定使用 `paper-chew-fulltext.md`，同内容附件的结果项为 `unchanged`，同名异内容、多同名或已有附件无法读取时不创建第二份，也不自动删除。详细结果与恢复方法见 [错误与写入](errors-and-writes.md)。

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

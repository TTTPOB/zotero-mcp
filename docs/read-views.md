# 读取视图与搜索契约

列表默认返回精简的 `summary`，完整语义字段使用 `metadata`，Zotero 原生对象使用 `raw`。投影不改变原生顺序，也不合并父子条目。

## 条目视图

| 视图 | 内容 | 默认工具 |
| --- | --- | --- |
| `summary` | 发现文献所需的标题、作者、年份、标识符及关系字段 | `search_items`、`get_recent`、`get_item_children`、`get_collection_items` |
| `metadata` | `key`、`version`、`item_type`、`parent_key` 和完整语义 `data`，不带顶层 `library`/`links` | `get_item_metadata`（每项的 `item`）、`get_notes`、`get_annotations` |
| `raw` | Zotero 原生记录的深拷贝，不删字段 | 支持 `view` 的工具均可显式请求 |

摘要始终包含 `key`、`version`、`item_type`、`title`。`year`、`creator_summary`、`doi`、`parent_key`、`candidate_attachment_key` 没有值时省略，不返回 `null` 或空字符串占位。普通条目的完整标题保留；笔记以去除 HTML 后的第一个非空行作为标题，最多 120 个字符。

`key` 可以直接作为 `get_item_fulltext` 或 `find_item_fulltext` 的 `item_key`。父文献的 `candidate_attachment_key` 是从 Zotero 的 `links.attachment.href` 提取的最佳附件 key，只是候选，不保证索引可读；附件自身的摘要没有这个字段，它的 `key` 就是附件 key。摘要不含重复的全文入口对象或固定为未知的索引状态。

`metadata.data` 保留摘要、作者、标签、集合及类型特有字段。它不是可直接写回的 payload；更新仍使用变化字段补丁和本地 `version`。MCP 输出只序列化业务结果实际设置的字段，因此 `raw` 不会因 schema 默认值新增字段。

## 集合视图

`get_collections` 默认 `view="summary"`，每个集合返回：

- `key`、`version`、`name`；
- `item_count`：Zotero 的 `meta.numItems`，是集合直接包含的条目数；
- `parent_key`：仅子集合提供，根集合省略。

`view="raw"` 返回含 `library`、`links`、`meta`、`data` 的原生记录。集合不提供 `metadata` 视图。

## 搜索与父条目

`qmode="everything"` 默认不发送 `itemType`，保留附件全文命中。`qmode="titleCreatorYear"` 未指定类型时使用 `-attachment`。显式 `item_type` 会先去除首尾空白；空字符串或纯空白等同于未提供。

普通搜索保留实际命中对象的 `key`。附件命中的 `item_type` 是 `attachment`，`key` 就是命中的附件；子附件、子笔记等提供 `parent_key`。`summary` 还提供 `parent`，内含父条目的 `title`、`creator_summary`、`year`，缺失值同样省略。不会编造 snippet、字段命中或相关性分数，也不会把子命中替换成父文献。

父条目按当前原生页的不同 key 统一批量读取，不逐条请求。没有子命中时不读取父条目；`metadata`、`raw` 仅在父标签筛选需要时读取父条目。

标签筛选通过 `tag_scope` 指定层级：

- `matched_item`（默认）：由 Zotero 筛选命中对象的标签。
- `parent`：在当前原生页的父条目标签上筛选；顶层命中检查自身。
- `either`：命中对象或其父条目任一层级满足即可。

不同 `tags` 列表项是 AND；单项中的 `a || b` 是 OR；`-tag` 排除标签。父标签筛选与摘要上下文共用同一批父条目，不重复请求。

### 按 DOI / ISBN 精确查找

整个 `query` 是一个标识符时，`search_items` 不再做普通文本搜索：

- 识别范围：DOI（`10.x/...`、`doi:` 前缀、`https://doi.org/`、`http://dx.doi.org/`，大小写不敏感，URL 里的百分号编码会还原）；ISBN-10/13（可带 `ISBN` 前缀、连字符或空格）。裸的 10/13 位数字需要校验位正确才算 ISBN；带 `ISBN` 前缀则不校验，只做不转换的精确匹配。`query` 里除标识符外还有别的词，则仍是普通搜索。
- 检索与过滤：先用 Zotero 的 `qmode=everything` 经 `/items/top` 取候选（子笔记、附件折叠为其父条目），再按字段精确过滤。DOI 比较 DOI 字段（也接受存成 `https://doi.org/...` 的值）和 Extra 中独占一行的 `DOI: ...`，不区分大小写；`10.1000/x.1` 不会匹配 `10.1000/x.10`。ISBN 比较 ISBN 字段（可存多个 ISBN），ISBN-10 与对应的 ISBN-13 视为同一本书。
- ISBN 的候选检索：Zotero 保存 ISBN 时自动加连字符，且纯数字查询匹配不到带连字符的值。Zotero 搜索把 `%` 当作 LIKE 通配符，所以用 `9%7%8%…` 形式（数字间插入 `%`）一次就能命中任意分组方式；ISBN-10 与 ISBN-13 各查一次。
- 响应：普通 `items` / `offset` / `next_offset` / `total`，外加 `identifier_lookup`：`type`（`doi`/`isbn`）、`value`（规范化后的标识符）、`match: "exact"`、`fields`、`found`、`matches`（本页每条命中的 `key`、命中的 `field` 和存储值）。没有命中时 `items` 为空、`found=false`，并有 `message` 说明库里没有该标识符。
- 与普通搜索的差异：`qmode` 被忽略，`item_type` 只在显式给出时转发给 Zotero；`tags` 在精确命中之后过滤，三种 `tag_scope` 等价（命中的都是顶层条目）；分页针对精确命中的列表，`total` 是精确命中总数，`next_offset` 在还有更多精确命中时给出。候选上限 500 条/查询，超过时 `identifier_lookup.candidates_truncated=true`。
- Zotero 10.0.6 保存条目时会把 Extra 里的 `DOI:` 行移入 DOI 字段，所以 Extra 兜底只对较旧或同步来的数据起作用。

## 分页

列表返回 `items`、请求的 `offset` 和 `next_offset`。有 `Link: ... rel="next"` 时使用 next URL 的 `start`；有 `Total-Results` 时附带 `total` 并判断末页。父条目批量读取不会覆盖搜索页原来的分页信息。

普通搜索的分页位置与总数来自原生查询，而不是父标签过滤后的结果。本页过滤为空仍可能有下一页，必须按返回的 `next_offset` 继续。没有可靠响应头时，满页才给出 `offset + limit`；否则返回 `null`。原生空页结束分页。分页不是库修改期间的快照。

## 体积与连续阅读验证

离线测试用固定的三个合成条目确认摘要明显小于 raw，同时保留完整标题、独立 PDF、数量和顺序。体积断言使用比例，不把某个 JSON 字符数冻结成接口契约。

内存 MCP 测试执行 recent summary → 传摘要 `key` 作为 `item_key` 读取 → 传返回的 `attachment_key` 作为 `item_key` 并携带 `text_revision` 续读，验证正文拼接顺序。测量对象为 JSON 字符数，而非 tokenizer token 数或真实 Agent 成功率。

# 读取视图与搜索契约

本项目的列表读取默认返回面向 Agent 任务的 `summary`，需要完整语义字段时显式请求 `metadata`，需要 Zotero 原生对象时请求 `raw`。视图只改变返回投影，不改变本地 API 查询本身。

## 视图

读取条目支持 `view`：

| 视图 | 内容 | 默认工具 |
| --- | --- | --- |
| `summary` | `key`、`version`、`item_type`、完整 `title`、`year`、`creator_summary`、`doi`、父条目关系和全文入口；搜索时附加命中关系 | `search_items`、`get_recent`、`get_item_children`、`get_collection_items` |
| `metadata` | `key`、`version`、`item_type`、`parent_key` 和完整语义 `data`，不带顶层 `library`/`links` | `get_item_metadata`（每项的 `item`）、`get_notes`、`get_annotations` |
| `raw` | Zotero 原生记录的深拷贝，不删字段 | 所有支持 `view` 的工具均可显式请求 |

`summary` 不截断标题，不删除条目，也不改变原生顺序。`fulltext.index_state` 固定为 `unknown`，因为附件存在或 MIME 类型不能证明 Zotero 索引已经可读。`fulltext.read_key` 是可直接传给全文工具的 key；附件自身或 `links.enclosure.href` 的明确 `/items/<8位key>` 路径中解析出合法 Zotero key 时提供 `candidate_attachment_key`，但不会据此宣称索引可读。

`metadata.data` 保留摘要、作者、标签、集合及条目类型特有的语义字段。它不是可直接写回的 payload；更新仍使用变化字段补丁和本地 `version`。

MCP 结构化输出模型只序列化业务结果实际设置的字段（`model_fields_set`），因此 `view="raw"` 的每条结果的 `item` 与原生对象一致，raw 列表的 `structuredContent["items"]` 与原生列表一致，不会因 schema 默认值新增 `null` 字段；summary/metadata 仍只输出投影明确设置的字段。

## 搜索与附件

`qmode="everything"` 默认不发送 `itemType`，因此保留 Zotero 返回的附件全文命中。需要限制返回对象时显式传 `item_type`，例如 `item_type="-attachment"`。为兼容原有元数据搜索，`qmode="titleCreatorYear"` 在未显式指定 `item_type` 时仍使用 `-attachment`。

### 按 DOI / ISBN 精确查找

整个 `query` 是一个标识符时，`search_items` 不再做普通文本搜索：

- 识别范围：DOI（`10.x/...`、`doi:` 前缀、`https://doi.org/`、`http://dx.doi.org/`，大小写不敏感，URL 里的百分号编码会还原）；ISBN-10/13（可带 `ISBN` 前缀、连字符或空格）。裸的 10/13 位数字需要校验位正确才算 ISBN；带 `ISBN` 前缀则不校验，只做不转换的精确匹配。`query` 里除标识符外还有别的词，则仍是普通搜索。
- 检索与过滤：先用 Zotero 的 `qmode=everything` 经 `/items/top` 取候选（子笔记、附件折叠为其父条目），再按字段精确过滤。DOI 比较 DOI 字段（也接受存成 `https://doi.org/...` 的值）和 Extra 中独占一行的 `DOI: ...`，不区分大小写；`10.1000/x.1` 不会匹配 `10.1000/x.10`。ISBN 比较 ISBN 字段（可存多个 ISBN），ISBN-10 与对应的 ISBN-13 视为同一本书。
- ISBN 的候选检索：Zotero 保存 ISBN 时自动加连字符，且纯数字查询匹配不到带连字符的值。Zotero 搜索把 `%` 当作 LIKE 通配符，所以用 `9%7%8%…` 形式（数字间插入 `%`）一次就能命中任意分组方式；ISBN-10 与 ISBN-13 各查一次。
- 响应：普通 `items` / `offset` / `next_offset` / `total`，外加 `identifier_lookup`：`type`（`doi`/`isbn`）、`value`（规范化后的标识符）、`match: "exact"`、`fields`、`found`、`matches`（本页每条命中的 `key`、命中的 `field` 和存储值）。没有命中时 `items` 为空、`found=false`，并有 `message` 说明库里没有该标识符。
- 与普通搜索的差异：`qmode` 被忽略，`item_type` 只在显式给出时转发给 Zotero；`tags` 在精确命中之后过滤，三种 `tag_scope` 等价（命中的都是顶层条目）；分页针对精确命中的列表，`total` 是精确命中总数，`next_offset` 在还有更多精确命中时给出。候选上限 500 条/查询，超过时 `identifier_lookup.candidates_truncated=true`。
- Zotero 10.0.6 保存条目时会把 Extra 里的 `DOI:` 行移入 DOI 字段，所以 Extra 兜底只对较旧或同步来的数据起作用。

搜索结果采用“原生命中 + 关系字段”的最小契约，不扫描全库，也不把附件命中无界地去重或替换成父文献：

- 原生记录的 `key` 始终是 Zotero 实际命中的对象 key。
- 附件命中提供 `matched_attachment_key`，并在存在父文献时提供 `parent_key`。
- 搜索摘要提供 `match_details_available=false`，不编造字段命中、snippet 或相关性分数；关系字段只说明原生附件和父条目 key。
- 父文献不会因为附件命中被自动拉取或重复返回；需要父文献语义时按 `parent_key` 读取。
- 独立附件保留在结果中，`parent_key` 为 `null`。

标签筛选通过 `tag_scope` 明确层级：

- `matched_item`（默认）：把标签传给 Zotero，约束原生命中对象。
- `parent`：不把标签传给上游；对当前有限页的附件命中读取其父条目并在父条目标签上筛选。
- `either`：原生命中对象或父条目任一层级满足标签即可。

不同 `tags` 列表项仍是 AND；单项中的 `a || b` 是 OR；`-tag` 排除标签。`parent`/`either` 只对当前上游页做有界父条目读取，不改变上游 offset，也不把过滤后的输出数量当作下一页 offset。

## 分页

列表结果包含 `items`、请求的 `offset` 和 `next_offset`。如果 Zotero 响应提供有效的 `Link: ... rel="next"`，从 next URL 的 `start` 读取下一偏移；如果提供 `Total-Results`，用它判断精确满页是否已经结束，并在结果中附带 `total`。这些值表示原生查询结果，不是父标签过滤后的精确总数。

响应没有可用分页头时，继续使用保守 fallback：返回数量等于 `limit` 才给出 `offset + limit`，否则 `next_offset` 为 `null`。当 `Total-Results` 存在但上游返回空页时也返回 `null`，避免重复请求同一 offset 形成循环。这可能需要最后一次空请求，但不会为了计算父映射而无界扫描。条目发生变化造成的普通 offset 漂移也不被伪装成快照保证。

## 可复现体积测量

`tests/test_read_views.py::test_fixed_fixture_summary_size_is_reproducible` 使用固定的三个合成条目（长摘要、八位作者、完整标题、一个独立 PDF），采用：

```python
json.dumps(value, ensure_ascii=False, indent=2)
```

单份业务对象的字符数为：

| 投影 | 条目数 | 字符数 |
| --- | ---: | ---: |
| `raw` | 3 | 6167 |
| `summary` | 3 | 1226 |

该数字证明固定 fixture 下投影体积变化，同时确认完整标题、独立 PDF、数量和顺序都保留。它不是用户提供 JSONL 的回放结果，也不是 tokenizer 的 token 测量。

`tests/test_read_workflow.py::test_deterministic_read_workflow_and_envelope_sizes` 还执行一个固定 mock MCP 流程：recent summary → 使用 `fulltext.read_key` 读取并选择附件 → 使用返回的 `attachment_key` 与 `text_revision` 续读，再断言正文拼接顺序。该流程的业务 page 测量公式为：

```python
len(json.dumps(recent.structuredContent, ensure_ascii=False, separators=(",", ":")))
```

固定 fixture 的业务 page 为 `302` 字符。模拟 MCP envelope 包含 `content`（工具文本块）、`structuredContent` 和 `isError`，测量公式为：

```python
len(
    json.dumps(
        {
            "content": [{"type": block.type, "text": block.text} for block in recent.content],
            "structuredContent": recent.structuredContent,
            "isError": recent.isError,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
)
```

该 envelope 为 `724` 字符。两者都是确定性 mock 业务数据的 JSON 字符数，不是用户 JSONL 回放、真实 LLM Agent 任务成功率或 tokenizer token 数。

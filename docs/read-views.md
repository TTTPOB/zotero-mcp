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

按 DOI/ISBN/URL 或其他非标题字段查找时，使用 `qmode="everything"` 配合 `item_type="-attachment"`，并核对返回的 `doi` 是否与目标 DOI 一致；默认模式可能漏掉 DOI，不排除附件则可能命中引用该 DOI 的其他论文全文。

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

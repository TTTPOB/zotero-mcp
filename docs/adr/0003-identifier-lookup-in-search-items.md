# ADR 0003：DOI / ISBN 作为 search_items 的精确标识符查找

- 状态：已采纳
- 决策者：Agent（人类于 2026-11-09 授权修复此问题，具体方案由 Agent 决定）
- 日期：2026-11-09
- 涉及：`src/zotero_mcp/client.py`（`search_items`、`_lookup_identifier`）、`src/zotero_mcp/identifiers.py`、`src/zotero_mcp/presentation.py`（`ItemPage.identifier_lookup`）

## 背景

在真实 Zotero 10.0.6 上实测，agent 按最自然的方式查 DOI/ISBN 会得到错误或缺失的结果：

- 默认 `query="10.1000/target.1"`（`titleCreatorYear`）：只返回标题里写了该 DOI 的引用者，目标条目不出现，也没有提示。
- 旧文档推荐的 `qmode="everything"` + `item_type="-attachment"`：返回引用者、目标的子笔记（`title=""`、`doi=null`）和目标本身；`-attachment` 不排除笔记，引用者仍混在里面。
- `query="https://doi.org/..."`、`doi:...` 前缀：0 条。
- ISBN 查纯数字 `9780262035613` 0 条，必须写成库里的带连字符形式才命中。进一步实测：Zotero 保存 ISBN 时会自动加连字符（用 API 写入纯数字 `9780262035613` 后读回是 `978-0-262-03561-3`），搜索对 `-` 不做归一化，是子串匹配。

其他实测结论：

- `/items/top?q=...&qmode=everything` 返回的是顶层条目：子笔记命中会折叠为其父条目，不再单独出现。
- 搜索词按空格拆成多个词，各词按子串 AND 匹配；大小写不敏感。
- `%` 在 q 里是 LIKE 通配符（`9%7%8%0%2%6%2%0%3%5%6%1%3` 命中 `978-0-262-03561-3`，与存储时的连字符分组无关；`_` 同理是单字符通配）。
- Zotero 10.0.6 保存条目时，把 Extra 里的 `DOI: ...` 行移入 DOI 字段，所有测过的条目类型都如此（含 `book`、`webpage`）。

## 决定

`search_items` 识别 `query` **整体**是一个标识符时，进入标识符查找，而不是普通文本搜索：

1. 识别：DOI（裸 `10.\d{4,9}/…`、`doi:` 前缀、`https?://(dx.|www.)?doi.org/` 链接，大小写不敏感，URL 里的 `%XX` 还原，去掉 `?`/`#` 后缀）；ISBN-10/13（可带 `ISBN`、`ISBN-13:` 等前缀，连字符和空格可有可无）。裸数字必须校验位正确才算 ISBN，带 `ISBN` 前缀则放宽（只做不转换的精确匹配）。`query` 里还有别的词，仍是普通搜索。
2. 候选：用 `qmode=everything` 经 `/items/top` 取候选。DOI 直接查规范化 DOI；ISBN 用 `d%i%g%i%t%s` 通配形式，ISBN-10 和 ISBN-13 各查一次。每个查询最多取 500 条候选（5 页 × 100），超过则 `candidates_truncated=true`；不扫描全库。
3. 精确过滤：DOI 与 DOI 字段（先去前缀、小写）比较，也比较 Extra 里独占一行的 `DOI: …`；ISBN 把 ISBN 字段（可含多个 ISBN）归一化成数字后比较，ISBN-10 与 ISBN-13 互转后视为同一本书（`978` 前缀才有 ISBN-10 形式）。只返回字段真正相等的条目。
4. 响应：保持 `items` / `offset` / `next_offset` / `total`，新增 `identifier_lookup`：`type`、规范化后的 `value`、`match="exact"`、`fields`、`found`、本页每条命中的 `matches[{key, field, value}]`，0 条时附 `message`；仅在截断时出现 `candidates_truncated`。`items` 里的摘要字段不变。
5. 参数语义：`qmode` 被忽略（内部总用 `everything`）；显式 `item_type` 转发给 Zotero，未给则不发送；`tags` 在精确匹配后本地过滤，`tag_scope` 的三个取值等价（候选都是顶层条目，其“父级”就是自身）；`offset` / `limit` / `next_offset` / `total` 针对精确命中的列表，`total` 是精确命中总数。
6. 工具参数描述与 docstring 删掉“DOI 要用 everything + `-attachment`”的绕路说明，改为说明标识符查找。

## 理由

- agent 只会把标识符整体当 query 传入；让工具自己做对，比要求 agent 记住绕路、再核对 `doi` 更可靠。
- Zotero 的搜索是子串匹配，只能给出候选；是否“就是这个标识符”必须由字段精确比较决定，这样引用者、`target.10` 这类前缀相同的 DOI 都不会被当成目标。
- `/items/top` 让子笔记折叠到目标本身，省去自己排除笔记和附件。
- 通配符查询一次就覆盖任意连字符分组，不需要内置 ISBN 注册机构分组表，也不需要多次猜测写法。
- `found=false` + `message` 让 agent 能区分“库里没有”和“没搜到”。
- 匹配 Extra 里的 `DOI:` 行成本很低，对较旧或同步来的数据（那时 DOI 还在 Extra）有用；目前 Zotero 10.0.6 自己写入的条目已经迁入 DOI 字段。

## 考虑过的选项

| 选项 | 结论 |
| --- | --- |
| 只改文档，让 agent 继续用 `everything` + `-attachment` | 否决：笔记仍混入，引用者仍可能排在前面，agent 容易误当目标 |
| 新增 `find_item_by_identifier` 工具 | 否决：agent 的第一反应是调用 `search_items`，新工具需要再被发现；多一个工具也增加说明长度 |
| 给 `search_items` 加 `identifier` 参数 | 否决：agent 仍可能把 DOI 放进 `query` |
| ISBN 候选用多种连字符写法分别查 | 否决：分组位置依赖注册机构，需分组表或穷举约 20 个查询 |
| 拉取 `everything` 结果后本地过滤全部 | 否决：个人库可能上万条，且 Zotero 对纯数字 ISBN 也查不到 |
| 不匹配 Extra | 否决：成本低且对旧数据有用 |
| 只保留 `identifier_lookup.value` 而不报告每条命中字段 | 否决：Extra 命中时摘要里的 `doi` 为 null，agent 需要知道为何命中 |

## 取舍与限制

- 依赖 Zotero 把 q 里的 `%` 当 LIKE 通配符。这是实测行为，不是文档化契约；Zotero 升级后若失效，ISBN 的连字符存储形式将再次查不到（纯数字存储仍可）。`tests/live` 里的 ISBN 用例会发现这个变化。
- 候选上限 500 条/查询。真实库里一个完整 DOI 或 ISBN 命中的候选通常只有个位数，所以不做更复杂的处理；超限时用 `candidates_truncated` 提示而不是静默漏掉。
- ISBN 校验位不对的裸 13 位数字不会被识别为 ISBN，仍走普通搜索。

## 重新评估的触发条件

- Zotero 不再把 `%` 当通配符，或改变 ISBN 的存储与搜索方式。
- 需要按其他标识符（arXiv、PMID、URL、citationKey）查找；可沿用同一框架增加类型。
- 实测出现候选超限、或查找耗时明显增加。

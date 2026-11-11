# ADR 0002：写工具的错误码、isError 与版本返回

- 状态：已采纳；保留旧聚合字段、未知版本使用 null、批量结果不带版本的部分由 [ADR 0007](0007-compact-write-outcomes.md) 取代，其余决定继续有效。
- 决策者：Agent（人类于 2026-11-09 授权修复此问题，具体方案由 Agent 决定）
- 日期：2026-11-09
- 涉及：`src/zotero_mcp/client.py`（`update_item`、`delete_item`、`update_collection`、`delete_collection`、`add_items`、批量标签/集合修改）、`src/zotero_mcp/server.py`（`ZoteroMCP.call_tool`）、`src/zotero_mcp/models.py`

## 背景

在真实 Zotero 10.0.6 上走写入流程时，测试 agent 观察到四个问题：

1. `update_item` 对不存在的 key 返回 `INVALID_ARGUMENT`，message 是 HTTP 原文（`itemType property not provided`）。
2. 整批写入全部失败时，结果里 `ok=false`、`status=failed`，但 MCP 的 `isError=false`；只看 `isError` 的 agent 会当作成功。`add_items` 还会静默丢弃未知字段，而 `update_item` 会拒绝。
3. `VERSION_CONFLICT` 不带当前版本，agent 必须再读一次。
4. `update_item` 成功后不返回新版本，连续修改两次要中间重读。

在一次性容器里实测得到的事实：

- `PATCH /items/<不存在的 key>` 在 changes 不含 `itemType` 时返回 400，含 `itemType` 时**直接用该 key 新建条目**；`PUT /collections/<不存在的 key>` 同样**新建集合**。`DELETE` 不存在的条目或集合返回 404。
- `PATCH`/`PUT` 成功返回 204，响应头 `Last-Modified-Version` 是**库**版本。更新确实修改了内容时它等于条目新版本；更新没有改变任何内容时条目版本不变，而该头仍是库版本（实测头为 13，条目版本为 11）。
- 412 响应没有可用的版本信息；message 里的 `expected N, found M` 是自然语言。
- pyzotero 的 `create_items` 不校验字段名，Zotero 对 POST 里的未知属性直接忽略；`update_item` 会先调用 `check_items`，所以拒绝未知字段。

## 决定

1. **不存在的 key 一律 `NOT_FOUND`。** `update_item`、`update_collection` 在写之前先读取条目/集合，读不到就返回 `NOT_FOUND`，不发出 PATCH/PUT；`delete_item`、`delete_collection` 把 404 映射为 `NOT_FOUND`。message 带上 key，并建议用 `search_items` / `get_collections` 重新取 key；`details` 含 `operation` 和 `key`。
2. **写工具结果 `status` 为 `failed` 时 `isError=true`。** `structuredContent` 不变，逐项结果仍可读；`partial` 保持 `isError=false`，`unknown` 原来就是 `isError=true`。批量标签/集合修改在没有任何成功或未变化项时（例如第一个 key 就失败）原先报 `partial`，改为 `failed`，其余输入仍是 `pending`。`add_items` 在写入前调用 pyzotero 的 `check_items`，含未知字段名的批次整批返回 `INVALID_ARGUMENT`，不写入任何条目。
3. **`VERSION_CONFLICT` 带 `error.details.current_version`。** 写之前读到的版本与传入版本不同就直接冲突（不发请求）；Zotero 返回 412（并发修改）时再读取一次当前版本。读取失败时省略该字段并保留原来的提示。message 说明：确认修改仍然适用后可用该版本重新提交。`retryable` 保持 `false`。
4. **`update_item` / `update_collection` 成功后返回新版本。** 写入成功后立即读回条目/集合的版本，放在顶层 `version` 和 `results[0].version`；读回失败时为 `null`，不使用 `version + 1`。`successful`、`success`、`unchanged`、`failed`、`unknown`、`pending`、`updated` 等原有字段全部保留。批量标签/集合修改的 `results` 不带 `version`。

## 理由

- 第 1 点：用 400 的文本判断“不存在”不可靠，而且带 `itemType` 的 PATCH 和任何 PUT 会创建一个新对象，agent 把写错的 key 变成垃圾数据。写之前读一次可以一并排除。多一次本地 GET 的代价可以接受；`update_collection` 原本就这样做。
- 第 2 点：`isError` 是许多客户端唯一检查的位置。全部失败时没有任何写入，报告成功是误导；部分成功仍需要 agent 读逐项结果，因此保持 `false`。校验未知字段只需 pyzotero 已有的 `check_items`，成本是每个会话一次缓存的 `/itemFields` 请求。它用所有条目类型的字段并集校验，不检查字段是否属于该条目类型，这与 `update_item` 的现有行为一致。
- 第 3 点：版本冲突后 agent 最需要的就是当前版本。选择“读取”而不是解析 412 的 message，因为后者是自然语言，会随 Zotero 版本变化。`retryable` 在 `docs/errors-and-writes.md` 中的定义是“客户端是否可以安全地再次尝试”：原样重发必然再次冲突；用新版本重提会覆盖别人的修改，必须先由调用者确认自己的修改仍然适用，所以不是可以自动重试的情形，保持 `false`，把 `current_version` 放在 `details` 里让手动重提只需一步。
- 第 4 点：`Last-Modified-Version` 在“更新没有改变任何内容”时给出的是库版本，会让 agent 拿到比条目实际版本更大的数字，下一次更新会被误判为冲突，所以不使用它。读回版本是权威值，代价是一次额外的 GET；取不到时返回 `null` 比猜测安全。顶层和逐项同时放 `version`，单条写入的 agent 读顶层，按 `results` 统一处理的 agent 读逐项。

## 考虑过的选项

| 问题 | 选项 | 结论 |
| --- | --- | --- |
| 1 | 只在 PATCH 返回 400 后再查是否存在 | 拒绝：带 `itemType` 的 PATCH 不会失败而是新建 |
| 1 | 把 400 文本映射为 `NOT_FOUND` | 拒绝：依赖英文文本，且不能覆盖创建的情形 |
| 2 | 部分成功也设 `isError=true` | 拒绝：agent 会丢弃已成功的逐项结果；与 `partial` 的语义不符 |
| 2 | 对 `add_items` 逐条按条目类型校验字段 | 暂不做：需要为每种类型额外请求，收益有限 |
| 3 | 解析 412 message 里的 `found N` | 拒绝：自然语言，不稳定 |
| 3 | `retryable=true` | 拒绝：原样重试必然失败，带新版本的重试需要先判断 |
| 4 | 使用 `Last-Modified-Version` | 拒绝：无改动更新时是库版本 |
| 4 | 返回 `version + 1` | 拒绝：Zotero 一次写入不一定只增加 1 |
| 4 | 只放顶层或只放逐项 | 拒绝：两种读取方式都会有 agent 使用 |

## 重新评估的触发条件

- Zotero 的 PATCH/PUT 对不存在的 key 改为返回 404，或 412 响应带条目版本，使额外的读取可以去掉。
- 批量工具（标签/集合）需要在逐项结果里返回新版本，或批次变大使额外的 GET 成为瓶颈。
- 实际使用中发现 agent 需要把 `partial` 也当作错误，或 `add_items` 的字段并集校验漏掉了常见的拼写错误。

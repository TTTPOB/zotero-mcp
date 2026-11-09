# ADR 0006：参数错误只给一两句可执行的说明

- 状态：已采纳
- 决策者：Agent（人类于 2026-11-09 授权修复此问题，具体方案由 Agent 决定）
- 日期：2026-11-09
- 涉及：`src/zotero_mcp/errors.py`（`format_validation_error`）、`src/zotero_mcp/client.py`（`key()`、`Library._check_item_type`、`export_items` 的参数类型）

## 背景

MCP 输入校验失败时，`message` 是 pydantic 原文加文档 URL，`details` 又带完整 `input`，每次 500–1200 字符。典型例子：key 过长只提示 “String should have at most 8 characters”，没说 key 是 8 位字母数字、不要传 URI；一次传 51 个 key 时，错误里回显全部 51 个 key。另外 `search_items` 传不存在的 `item_type`（如 `article`）会静默返回 0 条，agent 无从得知是类型名错了。

## 决定

1. `from_exception` 遇到 pydantic `ValidationError` 时，按每条错误的 `type`/`loc`/`ctx` 生成一句话：参数路径（`item_keys[3]`）、问题、改法。key 参数（名字以 `key`/`keys` 结尾）给专门提示：8 位大写字母和数字、例如 `ABCD2345`、不要传 URI 或标题，并指向能取得 key 的工具。超长列表写明上限和实际个数并建议分批。多个问题时列出前 3 条，并注明总数。`limit` 越界保留原有的分页提示。
2. 保持错误码 `INVALID_ARGUMENT`、`retryable=false`、`isError` 和 `structuredContent` 的外层结构（写工具仍是 `ok/status/error` 信封）。变化只在 `error.message` 的文字，以及 `error.details`：仍是列表，每项只含 `type`、`loc`、`msg`，最多 5 项；去掉 `input` 和 `url`。
3. `key()` 对 `str` 类型参数的报错同样说明格式和取 key 的方式。`export_items` 的 `item_keys` 改用 `ItemKeyList`，与其他工具一致：校验在 MCP 边界发生，错误能指出具体下标；工具的输入 schema 因此多了数量和格式约束。
4. `search_items` 的 `item_type` 在发送请求前校验：按 `||` 拆分，去掉每段前导 `-` 后，与 Zotero 的 `item_types()` 比较（区分大小写）。未知类型返回 `INVALID_ARGUMENT`，给出相近的类型名，并提示用 `zotero_get_item_schema` 查看全部类型。类型名列表在每个 `Library` 实例里只取一次。空值和只含空白的值仍等同于不传。

## 理由

- agent 看到错误后下一次调用就应该能修对；这需要路径和改法，而不是 pydantic 的内部术语。
- 回显输入既浪费上下文，对 51 个 key 的情况也毫无帮助。
- 静默返回 0 条会让 agent 误以为库里没有这类文献。
- 基于 pydantic 的 `type`/`loc`/`ctx` 生成消息，新增参数自动受益，不必逐个工具写文案。

## 考虑过的选项

- 只截断 pydantic 原文：仍然没有改法，也保留了文档链接。
- 为每个工具手写错误文案：与参数定义分离，容易过期。
- 对 `item_type` 只在 0 条结果时附加提示：多一次判断，且仍要先发出一次无意义的搜索；预先校验更直接。
- 用固定的类型白名单：与 Zotero 版本脱节，所以改用本地 API 的 `itemTypes`。

## 重新评估的条件

- 客户端依赖 `details` 里的 `input` 或 `url`。
- Zotero 允许 `itemType` 取 `itemTypes` 之外的值（例如插件自定义类型）。
- 校验失败的消息仍频繁导致 agent 重复同样的错误调用。

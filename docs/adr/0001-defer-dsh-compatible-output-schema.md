# ADR 0001：暂缓让 outputSchema 兼容 DSH

- 状态：暂缓
- 决策者：人类（项目维护者）。推翻或修改须经人类授权。
- 日期：2026-11-09
- 涉及：`src/zotero_mcp/server.py`（`structured_output=True`）；DSH `packages/mcp/mcp-client/src/tools.ts`

## 背景

每个工具都通过 FastMCP 的 `structured_output=True` 声明 `outputSchema`，并返回 `structuredContent`。`content` 里的文本是同一对象的 JSON 序列化。

在 DSH（0.2.2-rc.1，`daily-driver` 分支 `4195017a0b`）中，`outputSchema` 的唯一用途是 PTC 模式：DSH 把它渲染成 TypeScript 返回类型，写进工具 SDK 说明（`describe_tools` 返回的 `ToolOutputMap`）。agent 写 `run_code` 程序时，靠这份类型直接访问 `structuredContent` 的字段，不用先调用一次查看结构。native 模式发给模型的只有 `name`、`description` 和 `parameters`。

DSH 的 ToolRuntime 只接受 JSON Schema 的一个子集：`type`、`properties`、`required`、`additionalProperties`、`items`、标量 `enum`/`const`、`oneOf`，以及 `description`、`title`、`default`、`examples` 注释。Pydantic 生成的 schema 使用 `$ref`、`$defs`、`anyOf` 和 `minimum`。2026-11-09 的离线检查结果：26 个工具中，有 24 个的 `outputSchema` 超出该子集（`anyOf` 248 处、`$ref` 53 处、`$defs` 23 处、`minimum` 11 处）。

DSH 的 MCP 桥遇到不受支持的 schema 时，把 `structuredContent` 当作任意 JSON 处理（`supportedOutputSchema` 返回 `undefined`）。因此当前效果是：工具能正常注册和调用，PTC 中只拿不到返回类型。用户的 DSH 配置把 `mcp__*` 工具设为 `deferred`，只有 agent 调用 `describe_tools` 时才显示工具说明。

## 决定

暂不修改。保持现有 `outputSchema` 和 `structuredContent` 输出。

## 理由

- 当前不影响正确性。DSH 中工具照常可用，代价只是 PTC 程序缺少返回类型。
- 更明确影响 agent 行为的问题优先：错误 key 被静默接受、写入失败仍报告成功、DOI 查找漏掉目标等。
- 两种修法都会带来新的约束（见下文），需要单独评估。

## 以后重新评估时的选项

| 选项 | 做法 | 估时 | 覆盖面 |
| --- | --- | --- | --- |
| A：改 zotero-mcp | 列出工具时把 schema 改写进 DSH 子集：展开 `$ref`/`$defs`；把 `X \| None` 产生的 `anyOf` 改为 `oneOf`；去掉 `minimum` 等校验关键字 | 1–2 小时 | 仅 zotero-mcp |
| B：改 DSH mcp-client | 在 `supportedOutputSchema` 前做同样的规范化，失败时仍退回任意 JSON | 2–3 小时 | 所有用 Pydantic、Zod 生成 schema 的 MCP 服务器；需在 DSH fork 上维护 |
| C：不再声明 `outputSchema` | 保留 `structuredContent`，`tools/list` 不再输出 schema | <1 小时 | DSH 中效果与现状相同；其他客户端失去返回结构说明 |

修改前需要确认：

- `anyOf` 只在分支互斥时（如 `X | null`）可以等价改写为 `oneOf`；其余情况保留退回路径。
- 一旦 schema 被 DSH 接受，DSH 会用它校验每次返回，结构不符时调用会报错。需要用离线测试和 `tests/live` 覆盖所有工具的实际返回。
- 每个工具的 `describe_tools` 输出预计增加约 2–3K 字符。

## 重新评估的触发条件

- agent 在 PTC 中频繁先打印返回值再写代码，或因猜错字段名而失败。
- DSH 上游扩展支持的 schema 子集，或开始规范化 MCP schema。
- 其他实际使用的客户端依赖 `outputSchema`。

# Agent 接口改造验收记录

## 范围与基线

依据用户提供的《Zotero MCP：Agent 接口与上下文效率评审》实施，仓库基线为 `4951e10`（`0.12.0+local.1`）。保持本地 API 薄接口定位、共享客户端锁、本地授权与版本冲突约束，不引入 OCR、向量数据库或模型依赖。

本次重新运行基线测试：`env -u NO_PROXY -u no_proxy uv run pytest -q`，99 项通过（0.90 秒）。对照运行 `uv run pytest -q` 得到 97 项通过、2 项配置测试失败：`httpx2.InvalidURL: Invalid port: ':1]'`，堆栈定位到环境代理规则的 URL 解析。该问题在功能修改前已经存在，后续验证统一清除这两个变量，不将其归因于接口改造。

## 分工

实现分为三个独立工作树，由 `klaude-openai/gpt-5.6-luna`、`xhigh` 子代理负责，每项功能/修复独立提交：

- 搜索语义、summary / metadata / raw 视图、读取 schema 与分页。
- 全文附件选择、固定来源续读、文本版本校验和附件内 find。
- 稳定错误、批量写入与未知结果、MCP 错误和输出 schema。

主代理在全部初始实现完成后亲自阅读最终代码、运行集成测试，将发现交由原 Luna 子代理逐项修复，再复核合并结果。任务验证使用确定性 mock MCP 流程，不声称进行了真实 LLM Agent 回放。

## 验收矩阵

| 场景 | 验证重点 | 证据层级 |
| --- | --- | --- |
| 最近条目 → 选文献 → 读正文 | 标题/key/version 保留；直接提供全文入口 | 业务 mock + MCP |
| 正文独有词检索 | everything 不再默认排除附件；附件命中保留来源和父 key | mock HTTP；真实命中仍需 Zotero |
| 仅父文献有标签 | 明确筛选层级，不把附件自身标签当作父标签 | 业务/mock HTTP |
| 独立 PDF | 不因无父文献而被列表或搜索静默丢弃 | 业务 mock |
| 多个 PDF / 补充材料 | 首选关系可靠时透明选择，否则提供候选并要求明确来源 | 业务 mock |
| 无索引 / 部分索引 | 可操作错误；索引覆盖与阅读覆盖分开 | 业务 + MCP |
| Unicode 正文续读 | 0 起始 code point offset，来源和 revision 一致 | 业务 + MCP |
| 变更后续读 | 显式固定附件 key，文本 SHA-256 变化时拒绝静默接续 | 业务 mock + MCP |
| 附件内 find | 有界片段、真实位置、明确匹配规则 | 业务 + MCP |
| 满页 / 无分页头 | 使用可靠上游信息，否则保守 fallback | mock HTTP |
| 版本冲突 | 错误码稳定，不自动重放 | 业务 + mock HTTP + MCP |
| 部分批量成功 | 已完成/未变化/失败/未执行与输入稳定关联 | 业务 + mock HTTP |
| 上传后响应丢失 | 已创建 key 和失败阶段保留，写入未知不可盲目重试 | 业务/mock HTTP |
| 工具发现和输入范围 | 实际 tools/list 的字段、enum、范围与成功 outputSchema | MCP 内存会话 |
| 错误协议返回 | 机器可读 code/message/retryable 且 isError=true | MCP 内存会话 |
| 输出体积 | 同一批条目完整标题和顺序下比较单份 raw/summary | 可复现合成 fixture |
| 协议双份表示 | 分别测量业务对象与 content/structuredContent envelope | MCP；宿主模型消息另行验证 |

## 验证边界

所有自动化测试使用模拟库、内存 HTTP transport 或 MCP 内存会话，不访问真实 Zotero，也不修改用户文献库。mock 能证明查询构造、映射、协议和恢复行为，不能证明特定 Zotero 版本已正确索引某个正文词。

MCP 按兼容性约定保留文本与 structuredContent。这个仓库不能决定宿主最终是否重复注入模型消息；不得把协议字段的存在直接算作模型 token 重复计费。体积测量报告字符数，不冒充供应商 token 数或真实 Agent 成功率。

全文缓存、全库搜索索引和额外批量元数据工具只有在明确收益支持下才增加；保留对具体改进边界的说明。

## 主代理最终评审方法

实现代理完成后，主代理再逐项检查最终代码和提交，而不把子代理报告视为评审结论：

1. 检查搜索返回对象与上游 offset 的关系，附件/父条目标签筛选是否有明确契约；检查 raw 入口、独立附件和完整标题是否保留。
2. 检查全文多候选、显式附件读取、Unicode 偏移与 revision 变化，确认“索引页数”没有被当成“已读页数”。
3. 检查实际 MCP `tools/list` 的输入/输出 schema、错误 `isError`，以及批量部分成功和未知写入是否给出可恢复结果。
4. 在合并后的主工作树运行全套 mock/内存 HTTP/MCP 测试与 Ruff；针对评审发现补回归测试，功能修复单独提交。
5. 使用同一个确定性合成条目集合比较单份 raw 与 summary JSON 字符数，公式为 `1 - summary_chars / raw_chars`。另行测量 MCP envelope，避免混淆字段投影与宿主去重的收益。
6. 检查每个功能/修复的 atomic commit、文档默认值与实际签名一致性，并记录尚需真实 Zotero/宿主验证的部分。

## 主代理评审发现及处理

首次集成后的 Python 3.13 测试为 135 项通过、6 项失败；失败集中在全文专用错误未接入统一错误契约。主代理继续逐项阅读最终代码，发现并交回 Luna 实现独立修复：

- 全文错误被重映射后丢失错误码/候选；框架错误链解包可能越过已有业务错误。
- 读取视图类型没有实际用于工具返回注解，输出 schema 仍然过于宽泛；输入范围也需对外暴露。
- 搜索后读取父条目会覆盖原生搜索页的响应头，影响后续分页判断。
- 全文尾段不应声明已完整阅读全文；find 需要说明匹配上限，Unicode casefold 扩展需要避免重复的原文跨度。
- 批量响应缺失输入不能推断为未执行；附件上传异常或响应形状问题必须保留已经创建的 key。
- Python 3.11 下实际执行 `build_server()` 失败，错误明确要求使用 `typing_extensions.TypedDict` 替代嵌套的 `typing.TypedDict`。

这些是实际集成失败或最终代码可定位的问题，不是针对同机攻击者的额外加固。修复后的复核结果见下节。

## 最终结果

主代理已完成最终代码评审，并复核上述问题的独立修复；未发现仍阻碍本次接口改造交付的问题。最终保留 23 个工具（13 读、10 写），新增的唯一工具是 `find_item_fulltext`，没有把现有工具合并成通用执行入口。

### 验证结果

| 检查 | 结果 |
| --- | --- |
| Python 3.13.7：`env -u NO_PROXY -u no_proxy uv run pytest -q` | 177 passed（3.86 秒） |
| Python 3.11.13：`env -u NO_PROXY -u no_proxy .venv-review311/bin/python -m pytest -q` | 177 passed（4.11 秒） |
| `uv run ruff check .` | 通过 |
| `uv run ruff format --check .` | 通过，35 files already formatted |
| `git diff --check` | 通过 |

Python 3.11 验证环境用相同 `uv.lock` 和 `uv sync --locked --python 3.11` 创建；`.venv-review311` 是本机验证环境，不提交到仓库。相比基线 99 项，最终增加 78 项测试。测试全程禁止真实网络连接；pyzotero HTTP 行为通过内存 transport 验证，MCP 使用固定 SDK 1.30.0 的内存会话。

### 体积与任务流程

相同三条合成记录采用 `json.dumps(..., ensure_ascii=False, indent=2)`：

| 单份业务对象 | 字符数 |
| --- | ---: |
| raw | 6,167 |
| summary | 1,226 |

减少 `1 - 1226 / 6167 = 80.12%`。测试保留全部条目、原顺序、完整标题和独立 PDF，没有通过减少条目数或标题截断取得收益。

另一个确定性 mock MCP 流程验证了 recent summary → 使用 `fulltext.read_key` → 选取并读取附件 → 用实际 `attachment_key` 和 `text_revision` 续读，最后拼接得到完整 fixture 正文。该流程的业务 page 是 302 字符，包含文本块、structuredContent 和 isError 的 envelope 为 724 字符，两者均采用紧凑 JSON。具体 fixture 和公式见 [读取视图文档](read-views.md) 及 `tests/test_read_workflow.py`。这些是字符数，不是供应商 token 数；也不代表真实 LLM Agent 任务成功率。

### 提交与兼容性

实现由三名 Luna xhigh 子代理完成，主代理负责拆分、集成、最终评审和修复验收。读取视图、附件搜索、分页、全文选择、revision 续读、附件内 find、错误契约和写入恢复按功能/修复独立提交。评审发现的错误链丢失、父查询覆盖分页头、未知写入误分类、raw 被默认值污染、Python 3.11 schema 失败等问题也分别提交。

默认行为发生了有意变化：列表默认 summary、单条元数据默认 metadata，原生数据通过 `view="raw"` 获取；everything 默认保留附件命中，不去重或替换为父文献；多个全文候选无法确定时要求明确附件。所有改动均保留本地授权、版本冲突不自动重试、共享客户端锁与请求状态清理。

### 尚未执行的外部验收与有意保留的边界

- 未访问或写入真实 Zotero 文献库。特定 Zotero 版本中“正文独有词确实命中附件”的能力，仍需隔离库完成报告所述原生查询矩阵；本次证明的是查询构造、附件关系、筛选层级和分页契约。
- 未修改 DSH，也未验证宿主最终注入模型的消息是否去重。MCP 按兼容性惯例保留文本和 structuredContent 两份表示；仓库侧投影的收益已单独测量。
- 全文 revision 是每次重新读取原始索引文本后计算的 SHA-256，不是服务器/库身份绑定游标，也不等于条目 metadata.version。续读应固定返回的附件 key。没有增加缓存、TTL、全库索引、OCR 或向量检索平台。
- 缓存、更强游标和批量按 key 元数据工具属于原评审的有收益再做项目，本次没有凭空扩充；附件内 find、可靠原生分页信息等轻量改进已经实现。
- 当前环境直接执行不清除代理变量的 pytest 仍受基线 `NO_PROXY` IPv6 解析问题影响；验收命令已明确记录环境处理方式。

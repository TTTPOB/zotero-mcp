# ADR 0009：全文工具统一使用 item_key

- 状态：已采纳
- 决策者：人类（2026-11-11 在评审中逐项选定）
- 日期：2026-11-11
- 涉及：A8；取代 ADR 0005 决策 1 中“参数名不变”的决定

## 背景

两个全文工具都接受论文或附件 key，但查找工具称其为 `attachment_key`，读取工具另有可选 `attachment_key`，使同一个概念存在两套输入方式。续读还要同时保留论文 key 和返回的附件 key，没有实际必要。

## 决定

1. `get_item_fulltext(item_key, offset, max_characters, text_revision)` 删除可选 `attachment_key`。`find_item_fulltext(item_key, query, context_characters, max_matches, text_revision)` 把原来的 `attachment_key` 改名为 `item_key`。
2. 两个工具的 `item_key` 均接受论文或附件 key，并共用解析代码。附件索引读取成功时直接返回；索引不可读才读取条目元数据区分父条目、无索引附件、笔记、批注、集合及未知 key。
3. 结果继续返回实际的 `attachment_key`。后续读取或查找把它作为 `item_key` 传回，续读同时保留 `text_revision` 和 `next_offset`。
4. 选择歧义时错误提示把某个候选的 `attachment_key` 作为 `item_key`。删除“把父 key 放进显式 attachment_key”的不可达错误分支及不再使用的 key 类型。
5. 不修改 `read_attachment_text`、`get_annotations` 等其他工具的参数名。ADR 0005 的 key 分类、索引状态与错误决定继续有效；删除的旧参数对应分支不再适用。

## 理由

一个输入概念只保留一个参数名，论文 key 与附件 key 的解析行为一致。结果使用 `attachment_key` 精确说明本次实际读取的对象，连续阅读可以固定附件，不再重复执行父文献选择。无需兼容别名或新增抽象。

## 考虑过的选项

- 保留不同参数名：不能表达两个工具都接受父文献，增加调用负担，不采用。
- 同时接受 `item_key` 和旧 `attachment_key`：会保留重复输入及优先级规则，没有当前消费者需要，不采用。
- 把输出 `attachment_key` 也改名：会丢失实际附件身份的明确含义，没有必要。

## 重新评估的触发条件

- 真实任务需要一次读取多个附件，而不再是选定一个附件后稳定续读。
- Zotero 的全文端点或附件选择能力发生改变，现有论文/附件共用入口不再足够。

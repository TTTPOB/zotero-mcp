# Zotero MCP：本地精简版

供个人使用，连接正在运行的 **Zotero 10+**。基于 [54yyyu/zotero-mcp](https://github.com/54yyyu/zotero-mcp) 精简，保留 MIT 许可证与上游 Git 历史。

运行依赖只有 **pyzotero + 官方 MCP SDK**。默认使用 stdio，也可以改用 Streamable HTTP；两种方式都通过 `http://localhost:23119/api` 读写。

## 保留了什么

- 检索元数据、标签及 Zotero 已索引的全文；读取条目、集合、笔记、批注。
- 创建、修改、删除条目；管理标签和集合；创建子笔记；导入本地附件。
- 通过 Zotero 导出 BibTeX、RIS、CSL JSON。

批注使用 `zotero_add_items` 创建，通过 `zotero_update_item` 修改，通过 `zotero_delete_item` 删除。传入 Zotero 的批注字段，包括 `parentItem` 和 `annotationPosition`。

## 删除了什么

语义搜索、向量数据库、embedding、reranker、本地模型推理、模型供应商 SDK、独立 PDF/OCR/EPUB 提取器、SQLite 直读、WebDAV、云端写入及旧插件/Connector 写入路径。

同时删除额外的 standalone CLI、自动安装/更新、Docker、发布工作流、实验数据、外部 DOI/URL 元数据抓取及复杂工具集开关。默认不加载任何模型，不下载模型文件。

## 以后使用时，按这三步做

**1. 打开 Zotero。**

使用 Zotero 10+。进入「设置 → 高级」，勾选「允许此计算机上的其他应用程序与 Zotero 通信」。保留 Zotero 窗口。

**2. 准备项目环境，并授权写入。**

下面的命令是使用步骤；代码清理阶段没有执行项目安装或授权。

在 PowerShell 执行：

```powershell
cd C:\Tools\playground\zotmcp\zotero-mcp
uv sync --locked --no-dev
.\.venv\Scripts\zotero-mcp.exe authorize
```

在 Zotero 弹窗里选择 **Always Allow / 始终允许**。不要选择仅允许一次。

只读使用可以跳过 `authorize`。程序不会要求 zotero.org API key。

**3. 在支持 MCP 的客户端中添加一个 stdio 服务。**

命令：`C:\Tools\playground\zotmcp\zotero-mcp\.venv\Scripts\zotero-mcp.exe`

参数：`serve`

使用 `mcpServers` 格式的客户端可参考 [Windows 配置示例](docs/mcp-windows.json)。其他客户端填入同一命令与参数即可。保存后重启客户端。

先让助手执行：**“列出 Zotero 最近添加的 5 篇文献。”**

## 改用 HTTP 连接（可选）

需要多个客户端共用一个进程，或者客户端不方便启动进程时，用 Streamable HTTP：

```powershell
.\.venv\Scripts\zotero-mcp.exe serve --transport http
```

默认监听 `http://127.0.0.1:8000/mcp`，客户端填这个地址即可。用 `--host`、`--port` 改绑定地址和端口，也可以用环境变量 `ZOTERO_MCP_HTTP_HOST`、`ZOTERO_MCP_HTTP_PORT`；命令行参数优先。

这个接口**不做认证**，所以默认只绑定本机回环地址。改成 `--host 0.0.0.0` 或局域网地址后，同一网络里任何人都能读写你的 Zotero 库，请先确认网络环境可信。

写入权限仍由 `authorize` 决定，HTTP 只是换一种连接方式。不带 `--transport` 的 `serve` 与以前完全一致。

## 几个需要记住的点

- 默认连接本机个人库。群组库设置 `ZOTERO_LIBRARY_TYPE=group` 和 `ZOTERO_LIBRARY_ID=群组ID`，授权的 Zotero 需已同步该群组。
- 授权文件保存在 Windows 的 `%LOCALAPPDATA%\zotero-mcp\local-auth.json`，包含本地 key 与 server ID。不要提交或分享。密钥撤销或切换 Zotero profile 后，重新执行 `authorize`，再重启 MCP。
- 更新/删除前读取条目的 `version`；程序使用本地版本号防止覆盖并发修改。不要传入云端版本号。
- `delete_item` 和 `delete_collection` **永久删除**，不会移入回收站。删除集合会移除子集合，保留文献。
- 搜索、最近条目、集合条目和子条目列表默认返回精简 `summary`，保留完整标题和可调用的 key。需要摘要、完整作者、标签等数据时用 `view="metadata"`；需要原生 Zotero 对象时用 `view="raw"`。单条 `get_item_metadata` 默认返回 metadata；summary 不能整份写回，`changes` 仍是字段补丁。
- `qmode="everything"` 默认包含附件命中。结果保留原生命中对象及 `parent_key`，不合并成父文献；`tag_scope` 明确标签筛选作用于命中对象、父条目或任一层级。详见 [读取视图与搜索](docs/read-views.md)。
- 批量操作可能部分成功。逐项检查 `results` 中的输入索引、key 和状态，不要重发已成功条目。结果未知时先检查 Zotero；附件创建后上传失败会保留已创建的 key。详见 [错误与写入恢复](docs/errors-and-writes.md)。
- 全文来自 Zotero 自己的索引。多个候选附件无法确定时需指定附件 key；续读使用返回的附件 key 和 `text_revision`。`find_item_fulltext` 可在指定附件中找词并返回真实字符位置。没有全文时在 Zotero 中索引附件；本项目不执行 OCR。详见 [全文阅读契约](docs/fulltext-reading.md)。
- 不兼容上游全部工具名和配置。完整工具列表见 [工具说明](docs/tools.md)，本次改造的验证结果与边界见 [接口验收记录](docs/interface-review-acceptance.md)。

## 开发检查

`uv.lock` 已提交。uv 使用全局缓存，不在项目目录设置 `UV_CACHE_DIR`。

正常开发安装：`uv sync --locked`，之后运行 `uv run ruff check .` 和 `uv run pytest`。

不安装项目的检查方式：运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1`。脚本把锁定的依赖导出到临时文件，在全局 uv 缓存的临时环境中执行静态检查与测试，不安装本项目。

测试使用模拟库和内存 HTTP/MCP 通道，禁止访问真实网络或 Zotero 文献库。

依据：[Zotero 本地 API](https://www.zotero.org/support/dev/web_api/v3/local_api)、[pyzotero 本地 API](https://pyzotero.readthedocs.io/en/latest/#the-local-zotero-api)。

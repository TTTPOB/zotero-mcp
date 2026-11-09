# 真实 Zotero 环境测试

默认的 `uv run pytest` 只用模拟库，不接触任何 Zotero。`tests/live/` 另外在一个**用完即弃的真实 Zotero 10.0.6** 上运行：它在 rootless podman 容器里启动，库每次都是空的，写入不会影响你自己的文献库。

## 前置条件

- Linux 上可用的 rootless podman（5.x 验证过），能访问 `download.zotero.org`、`ghcr.io` 和 PyPI。构建用普通容器网络，不需要 `--network=host`。
- 镜像约 1 GiB，首次构建需下载约 118 MB 的 Zotero 压缩包，并校验写死在 `containers/zotero-test/Containerfile` 里的 SHA256。

## 命令

```sh
scripts/zotero-test-env build   # 构建镜像（固定 Zotero 10.0.6、uv 0.12.10、Python 3.13、锁定依赖）
scripts/zotero-test-env start   # 启动全新的空库，等待本地 API 就绪
scripts/zotero-test-env test    # 在容器内运行 tests/live；额外参数交给 pytest
scripts/zotero-test-env stop    # 停止并丢弃整个库
scripts/zotero-test-env reset   # stop + start
scripts/zotero-test-env serve   # 在容器内运行 zotero-mcp serve --transport http
```

- `start` 每次都先删除同名容器，库不持久化。它生成一个随机写入 key，在 Zotero 启动前写入该 profile 的 `localAPIKeys.json`，因此不需要点击授权弹窗。就绪后，它在容器内写入 `$XDG_CONFIG_HOME/zotero-mcp/local-auth.json`，格式与 `zotero-mcp authorize` 保存的相同。
- `test` 要求容器已启动。仓库以只读方式挂载到容器内的 `/src`，虚拟环境在容器内的 `/home/zotero/venv`，不会改动宿主机的 `.venv` 或仓库文件。例如只跑一个文件：`scripts/zotero-test-env test -q tests/live/test_live_reads.py`。
- 同一次 `start` 之后可以重复运行 `test`，但种子数据会累积；需要干净的库时先 `reset`。
- `serve` 在前台运行，Ctrl-C 停止。`start` 和 `serve` 会打印地址：容器端口只发布到宿主机的 `127.0.0.1` 随机端口，MCP 端点形如 `http://127.0.0.1:<端口>/mcp`，无认证，只供本机手动试用。`start` 还会打印一个 `/api/` 中继端口，只适合在浏览器或 curl 里查看，不要用它跑客户端（见下）。
- 可用环境变量 `ZOTERO_TEST_IMAGE`、`ZOTERO_TEST_CONTAINER` 改镜像名和容器名。

## 防止误连真实库

`tests/live/` 默认全部跳过。只有同时满足下面两点才会运行：

1. 设置了 `ZOTERO_LIVE=1`（`scripts/zotero-test-env test` 会设置）。
2. 本机 `localhost:23119` 上的 Zotero 返回的 `Zotero-Server-ID` 等于 `ZOTERO_TEST_SERVER_ID`，这个值是 `start` 记录的一次性库的 ID，`test` 从容器内读取并传入。

任何一项不满足，测试直接退出而不是继续。连接 Zotero 用的 key 来自 `ZOTERO_TEST_API_KEY`，不读取宿主机上的授权文件；只有一个测试读取 `local-auth.json`，用来确认 `start` 写入的内容与运行中的 Zotero 一致。因此在装有真实 Zotero 的机器上手动设置 `ZOTERO_LIVE=1` 也不会写入真实库。

## 测试内容

`tests/live/conftest.py` 覆盖离线套件的 `no_network` fixture，保留 `no_host_proxy`。会话级 fixture 预置两篇带 DOI 的文章（其中一篇有 `citationKey`）、一个 PDF 和一个文本附件、一条子笔记、一个高亮注释、一个集合和一个子集合以及若干标签。每个 MCP 工具至少通过 MCP 内存会话真实调用一次，包括写入工具，并且在写入后读回验证。`test_live_known_gaps.py` 里是标记为 `xfail(strict=True)` 的已知缺陷用例；对应的修复提交需要同时去掉标记。

## 已知限制

- **客户端必须在容器内运行。** pyzotero 把本地地址写死为 `localhost:23119`，附件上传会 POST 到 Zotero 返回的 `localhost:23119` 地址，`read_attachment_text` 会读取 Zotero 所在文件系统里的 `file://` 路径。所以 `test` 在容器内执行，宿主机映射的端口只用于人工查看或 `serve`；通过 `serve` 的端点读取附件文本时，读取也发生在容器内。
- **群组库无法测试。** 本地不能创建群组，群组只能来自同步，因此群组 URI 拼接只靠离线测试覆盖。
- **全文索引是异步的。** PDF 一般几秒后可读，种子 fixture 会轮询等待。通过本地 API 上传的纯文本附件在测试时限内没有被索引，所以全文用例使用 PDF，文本读取用例使用 `read_attachment_text`。
- **不能连接真实的 Zotero。** 容器里的 Zotero 没有你的数据，也没有同步；对真实库的发布前验证需要另行手动完成。
- `POST /api/local/authorize` 一定会弹窗，没有测试模式能绕过，所以环境改用预置 key，`zotero-mcp authorize` 本身不在 live 测试范围内。

# musicdl-web

musicdl-web 是面向单一操作者的私有自托管音乐获取工作台。它只处理公开可获取内容或操作者本人平台账号当前合法权益内的内容，不提供共享会员、第三方解析、付费墙或 DRM 绕过能力。

项目当前处于双平台纵向 spike 阶段。完整 Vue 工作台、下载队列和 NAS 发布只有在网易云音乐与 QQ 音乐都通过安全门槛后才会展开。

## 当前开发门槛

- 搜索只访问音乐平台自有 HTTPS 域名，不在搜索阶段解析音频 URL。
- 音质必须按歌曲与当前平台会话逐档验证；精确选择失效时明确失败，不静默降级。
- 初始请求和重定向都必须经过域名白名单，TLS 校验不可关闭。
- 不使用共享 Cookie、第三方 VIP 解析或上游 `musicdl` 的公开搜索/下载执行路径。
- 账号权益验证只能由操作者在本地界面或本机受控流程完成；秘密不得进入日志、命令参数或聊天。

完整范围见 [规格](docs/spec.md)，完成证据见 [验收清单](docs/acceptance.md)，架构决策见 [ADR](docs/adr)。

## 开发环境

需要 Python 3.12、Node.js 24 和 `uv`。当前 spike 可用以下命令验证：

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy
uv run python spikes/search_probe.py --source both --query 周杰伦 --limit 2
```

本目录中的 `musicdl/` 仅为上游只读参考检出，已从版本控制和容器构建上下文排除。

## 只读状态服务

当前可部署的容器只提供 spike 状态页和健康检查，不包含下载入口：

```bash
docker compose up --build -d
curl http://127.0.0.1:4534/healthz
```

NAS 部署使用 `.env.example` 中的 `/data/docker/musicdl-web` 路径约定。音乐库以只读方式挂载；会话密钥仅预留挂载，当前状态服务不会读取它。

## 许可与免责声明

本项目自有代码采用 Apache License 2.0。项目与网易云音乐、QQ 音乐及 Navidrome 无官方关联。完整应用仅面向个人非商业、自托管用途；操作者必须遵守平台条款及所在地法律，并仅获取其有权使用的内容。

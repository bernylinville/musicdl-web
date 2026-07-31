---
status: accepted
---

# 遵循 NAS 的独立 Compose 布局

`musicdl-web` 独立部署在 `/data/docker/musicdl-web`，以 UID/GID `1000:1000` 运行，通过 `4534:4534` 提供 Web 服务，并使用 `restart: unless-stopped` 与 `json-file` 日志轮转（`10m`、保留 `3` 个文件）。应用数据、临时文件和会话密钥分别挂载自 `/data/docker/musicdl-web/data`、`/data/docker/musicdl-web/tmp` 与 `/data/docker/musicdl-web/secrets/session.key`，服务器音乐库以读写方式挂载 `/data/media/music:/music`；开源 Compose 用环境变量表达这些宿主路径，NAS 本地 `.env` 提供实际值且不提交敏感内容。

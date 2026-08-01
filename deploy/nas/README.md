# NAS 部署

运行时目录为 `/data/docker/musicdl-web`，入口为 `http://192.168.50.10:4534`。

## 当前版本

- 镜像：`musicdl-web:0.2.2-20260731`（`linux/amd64`）
- 归档：`/data/docker/musicdl-web/releases/musicdl-web_0.2.2-20260731_linux-amd64.tar`
- SHA-256：`acc5d9ab10af45b44ec294401d065c238e638ce4f4ebdb6f725e2ccfad9af436`
- 健康检查：`http://127.0.0.1:4534/healthz`

当前版本提供完整工作台、双平台搜索、同源封面代理、页面“导入登录 Cookie”、网易云官方二维码登录、队列、任务历史和双交付模式。封面代理限制 2 MiB 流式响应、只接受有效 JPEG、使用有 TTL 的缓存，并在失败时软降级为占位符；同一份已验证封面用于音频标签和服务器 `cover.jpg`，缺失时任务明确告警。手动 Cookie 导入会实时验证，并在失败时保留旧会话。

网易云二维码走官方 HTTPS 流程，图片 URL 不透明且禁止缓存，前端每秒轮询；二维码过期后必须由操作者手动刷新。NAS smoke 已验证二维码启动、SVG 图片、等待态轮询和取消，真实手机扫码与确认仍待现场验证。QQ 二维码入口继续明确显示“尚未支持”；QQ 精确音质/下载因动态签名未现场证明而返回 `503`，精确下载、试听和浏览器取回的现场缺口不影响容器健康，但一期现场验收仍未完成。

`/data/media/music` 以读写方式挂载；服务只在 `ffprobe` 校验、Mutagen 标签处理和隐藏目录暂存全部完成后原子发布成品。Navidrome 继续由独立 Compose 以只读方式扫描同一目录，本次发布没有修改或重启 Navidrome。

## 安全约束

容器使用 UID/GID `1000:1000`、只读根文件系统、capability drop 和 `no-new-privileges`。运行时密钥文件位于 `/data/docker/musicdl-web/secrets/session.key`，权限必须为 `600`，用于 AES-GCM 会话材料加密。

平台凭据只能在 `http://192.168.50.10:4534` 页面导入。不得把 Cookie、token 或平台会话内容粘贴到聊天、命令行，或写入日志、API、SQLite、镜像和 Git。

## 运维检查

镜像在 `linux/amd64` 开发机本地构建并通过 `docker save` 导入 NAS；NAS 不从源码构建。

```bash
ssh 192.168.50.10 'cd /data/docker/musicdl-web && docker compose ps'
ssh 192.168.50.10 'curl -fsS http://127.0.0.1:4534/healthz'
ssh 192.168.50.10 'sha256sum /data/docker/musicdl-web/releases/musicdl-web_0.2.2-20260731_linux-amd64.tar'
```

发布后容器健康，运行时安全约束和 `/music` 可写性已验证。上一版 `0.2.1` 镜像、归档和配置备份继续保留为回滚输入。Navidrome 的容器 ID、启动时间和重启次数未变，本次发布没有修改或重启 Navidrome。带真实会话/排队任务的重启恢复、网易云手机扫码确认、本人会话精确下载、精确试听、浏览器取回及 Navidrome 发现仍需从页面现场验收。

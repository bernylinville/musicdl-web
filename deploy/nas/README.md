# NAS 部署

运行时目录为 `/data/docker/musicdl-web`，入口为 `http://192.168.50.10:4534`。

当前镜像只提供搜索 spike 状态页和 `/healthz`，不包含下载入口。`/data/media/music` 以只读方式挂载；只有双平台完整门槛通过后，才允许在新版本中改为写入。

镜像在 `linux/amd64` 开发机本地构建并通过 `docker save` 导入 NAS；NAS 不从源码构建。运行时密钥文件位于 `/data/docker/musicdl-web/secrets/session.key`，权限必须为 `600`，当前状态服务不会读取它。

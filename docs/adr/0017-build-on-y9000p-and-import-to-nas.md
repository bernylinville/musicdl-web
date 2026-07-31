---
status: accepted
---

# 在 Y9000P 构建并导入 NAS

首个版本在当前 Y9000P 上构建固定版本的 `linux/amd64` 镜像，导出并计算校验值后传入 NAS，再由 NAS 执行 `docker load` 和本项目的 `docker compose up -d`；NAS 不拉取源码也不现场编译。两端已实测均为 `x86_64/amd64` 且 Docker 版本为 29.6.2，NAS UID/GID 为 `1000:1000`；Compose 引用不可变版本标签并保留上一版本镜像，以便健康检查失败时回退。

---
status: accepted
---

# 使用单应用容器与本地持久状态

当前项目的 Docker Compose 只运行一个应用服务：同一镜像提供 Python API、编译后的前端静态资源和有界后台下载执行器，并将 SQLite 任务状态、入库索引与应用状态写入持久化数据卷。首期不引入 Redis、PostgreSQL 或独立 worker 容器；阻塞的平台调用在应用内部隔离执行，任务状态可在进程或容器重启后恢复，外部只需访问一个可配置 Web 端口。

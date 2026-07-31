---
status: accepted
---

# 采用 Vue、FastAPI 与 SQLite

前端使用 Vue 3、TypeScript 和 Vite，后端使用 Python 3.12 与 FastAPI，任务、入库索引和应用状态保存在 SQLite；生产镜像先构建前端，再由 FastAPI 提供静态产物和 API，从而保持单容器交付。`musicdl` 通过受控 Python 适配层调用，不运行其交互式 CLI，也不把来源特定的 `raw_data` 直接暴露给前端。

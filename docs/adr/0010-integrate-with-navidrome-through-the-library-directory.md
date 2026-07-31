---
status: accepted
---

# 只通过音乐库目录集成 Navidrome

Navidrome 独立部署，本项目只提供自己的 Docker Compose，不捆绑、不修改、不重启、不调用或管理 Navidrome，也不提供播放；当前项目将可配置的宿主音乐目录以读写方式挂载为服务器音乐库，现有 Navidrome 继续以只读方式挂载同一目录，并依靠它已有的 watcher 与定时扫描自行发现成品。下载只在完整文件和元数据准备完成后发布到音乐库，避免 Navidrome 扫描到半成品，并保持两个服务可以独立升级、重启和迁移。

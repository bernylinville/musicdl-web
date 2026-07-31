---
status: accepted
---

# 发布 Navidrome 可直接索引的入库产物

服务器保存必须保留平台提供的原始音频编码而不转码，并在入库发布前尽力补全 `TITLE`、`ARTIST`、`ALBUM`、`ALBUMARTIST`、曲号、碟号和日期等 Navidrome 标签，同时写入内嵌封面、专辑 `cover.jpg` 与音频同名 `.lrc`。Navidrome 完全按标签组织曲库，因此下载器是媒体与标签的唯一写入者；所有音频、标签和 sidecar 完成并校验后才原子发布到共享音乐库，Navidrome 始终只读扫描成品。

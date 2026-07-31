---
status: accepted
---

# 自有平台语义并隔离 musicdl

`NeteaseAdapter` 与 `QQAdapter` 自行负责平台自有域名上的搜索、元数据、当前会话能力快照和精确单档解析，自有下载器在下载前重新解析所选档位、拒绝降级或禁用域名重定向，自有媒体发布器完成校验、Navidrome 标签与原子入库。`musicdl==2.13.4` 只允许出现在可替换的 `MusicdlCompatV2134` 中，最多复用经 spike 验证无网络副作用的加密、签名等协议原语；不得实例化具体音乐客户端、调用其公开 `search()` / `download()`、调用任何第三方解析路径，或让 `SongInfo`、`raw_data` 和上游异常越过兼容层。如果最终没有实际复用安全原语，则删除依赖并停止使用“基于 musicdl 兼容层”的表述。

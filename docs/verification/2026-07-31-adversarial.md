# 2026-07-31 adversarial 验收记录

本记录对应 `docs/acceptance.md`，状态只代表下列命令产生的当次证据。`BLOCKED` 不等于
通过；双平台合法账号现场验收完成前不得宣称一期完成。

## 自动化矩阵

| ID | 验收面 | 状态 | 证据 |
| --- | --- | --- | --- |
| A01 | 搜索 fixture、分页、来源隔离 | PASS | 既有 `tests/test_search_adapters.py` |
| A02 | 音质快照绑定 session version | PASS | `test_quality_snapshot_rejects_a_different_session_version` |
| A03 | 音质快照 TTL 边界 | PASS | `test_quality_snapshot_rejects_the_exact_expiry_boundary` |
| A04 | 下载前精确档位重验且不降级 | PASS | `test_exact_quality_revalidation_never_downgrades` |
| A05 | TLS、初始 host、重定向 host | PASS | 网络策略测试及下载重定向测试 |
| A06 | Cookie 不跨平台/调用边界 | PASS | 既有 Cookie header 测试；平台会话对象拒绝跨来源读取 |
| A07 | QR 各终态清除临时 token | PASS | rejected/expired/network-error/succeeded/cancel 参数化测试 |
| A08 | QR、Cookie、token、source URL 的 repr/fixture/API redaction | PASS | 会话/grant repr、fixture 和 API 注入秘密测试 |
| A09 | 加密会话跨 store 重建恢复、错 key 安全失败 | PASS | AES-256-GCM + file repository 跨实例恢复、错 key、0700/0600 权限测试 |
| A10 | SQLite 不含 URL/Cookie/token | PASS | `test_sqlite_job_payload_contains_no_url_cookie_or_token` |
| A11 | 队列并发上限 | PASS | 两 worker、三任务的受控 Event 测试，无固定 sleep |
| A12 | 运行中取消 | PASS | `test_cancelling_a_running_job_reaches_cancelled_state` |
| A13 | 崩溃恢复 | PASS | active -> queued、progress 清零 |
| A14 | 失败重试 | PASS | failed -> queued，错误与进度清零 |
| A15 | 批次部分成功 | PASS | 整体 202，每项独立返回 queued/failed DownloadTask |
| A16 | 下载 302/HTML/截断/401/403 | PASS | `tests/e2e/test_download_security.py` |
| A17 | 媒体容器、codec、时长、所选音质 | PASS | FFprobe JSON parser、单音轨、技术属性、容器与安全错误测试 |
| A18 | 标签、封面、LRC、路径、冲突、原子发布 | PASS | Mutagen 四格式、内嵌封面、LRC、冲突后缀与发布回归测试 |
| A19 | 非受管文件保护 | PASS | 冲突文件内容保持不变，新产物使用来源后缀 |
| A20 | 去重、升级、文件缺失 | PASS | repository decision、旧受管音频/LRC 清理、缺失重下回归测试 |
| A21 | browser/server 双交付互斥 | PASS | browser 不入 managed index；server 成功后才登记 |
| A22 | API/前端任务类型契约 | PASS | batch/task response 与 `frontend/src/types.ts` 的 DownloadTask 对齐 |
| A23 | 默认 app 的 sessions/search/qualities API | PASS | production app 返回 sessions/search schema；无会话 quality 为明确 session_required 4xx |
| A24 | Vue 双来源、音质、试听、多选、队列、历史 | PASS | 前端 20 tests passed |
| A25 | 后端测试/lint/typecheck 与前端 test/type/build | PASS | 后端 163 tests、Ruff、mypy；前端 20 tests、lint、typecheck、build 全通过 |
| A26 | Compose 单容器/1000:1000/4534/只读根/日志 | PASS | 两份 Compose 的对应静态检查通过 |
| A27 | `/music` 可写、cap drop、no-new-privileges | PASS | 两份 Compose contract 全项通过 |
| A28 | 默认根页面与生产 runtime 装配 | PASS | `/` 提供构建工作台；默认 app 暴露 platform 与 task API |
| A29 | 镜像媒体运行依赖 | PASS | release/container smoke 证明 runtime 可用 ffprobe 与 mutagen |
| A30 | 同源封面代理与来源 URL 隔离 | PASS | 搜索只返回不透明同源路径；有效 JPEG、2 MiB 流式上限、TTL 缓存、禁用域名和失败软降级测试 |
| A31 | 同一封面写入标签与 `cover.jpg` | PASS | 下载 pipeline、四种标签格式、发布 sidecar 和“音频成功，封面缺失”回归测试 |
| A32 | 网易云官方 QR HTTPS 流程 | PASS | 启动、同源 SVG 图片、`no-store`、1 秒轮询、手动过期刷新、取消和各终态测试 |
| A33 | QR 同 jar 验证与加密会话原子替换 | PASS | 成功前账号验证；成功后替换，失败、过期和取消保留旧会话 |
| A34 | 手动 Cookie 导入 live validation | PASS | 成功后原子替换 AES-GCM 密文；验证失败保留旧会话 |

最近一次完整 release verifier：

```text
.venv/bin/pytest -q
163 passed
.venv/bin/ruff check backend tests
All checks passed
.venv/bin/mypy backend/src
Success: no issues found
```

前端 fresh evidence：20 tests passed。Ruff、mypy、前端 typecheck/lint、production build、
repository security、两份 Compose contract 和 container smoke 全部通过。

## NAS 发布实勘

执行：

```text
scripts/verify_nas_runtime.sh 192.168.50.10
```

结果：

- PASS：SSH、`/healthz`、容器 health、UID/GID `1000:1000`、只读根、单容器、镜像存在。
- PASS：运行镜像为 `musicdl-web:0.2.2-20260731`（`linux/amd64`），容器健康。
- PASS：归档 `/data/docker/musicdl-web/releases/musicdl-web_0.2.2-20260731_linux-amd64.tar` 的 SHA-256 为 `acc5d9ab10af45b44ec294401d065c238e638ce4f4ebdb6f725e2ccfad9af436`。
- PASS：最近 500 行日志无 Cookie/Authorization/token/query credential 形态。
- PASS：上一版 `0.2.1` 镜像、归档和配置备份仍保留，可作为回滚输入。
- PASS：`/music` 为读写挂载；capability 全部移除并启用 `no-new-privileges`。
- PASS：Navidrome 的容器 ID、启动时间和重启次数未变；本次发布没有修改或重启 Navidrome。
- BLOCKED：未在有真实会话或排队任务时做容器重启恢复演练。
- BLOCKED：没有合法下载产物，无法验证 Navidrome watcher/计划扫描发现。

## NAS live smoke

- PASS：网易云搜索返回 20 条结果。
- PASS：第一条结果的同源封面返回 `200 image/jpeg`；搜索 API 不暴露原始图片 URL。
- PASS：网易云二维码启动、同源 SVG 图片、等待态轮询和取消。
- PASS：界面使用“导入登录 Cookie”；QQ 二维码入口明确显示“尚未支持”。
- OPERATOR PENDING：真实手机扫描二维码并在手机确认。上述接口 smoke 不证明此项完成。

## 已部署功能与现场边界

- Vue 工作台、双平台搜索、同源封面代理、页面“导入登录 Cookie”、网易云官方二维码登录、队列、历史、服务器保存和浏览器取回接口已部署。
- 封面代理对响应执行 2 MiB 流式上限、有效 JPEG 校验和 TTL 缓存；失败时只显示占位符。同一份封面写入音频标签和服务器 `cover.jpg`，缺少封面只产生非致命告警。
- 网易云 QR 图片 URL 不透明且响应为 `no-store`，前端每秒轮询，过期后只能手动刷新。成功时先用同一 Cookie jar 验证账号，再原子替换加密会话；手动 Cookie 导入同样实时验证并在失败时保留旧会话。
- 网易云音质快照、精确档位重验和下载链路已经实现，但本人会话精确下载尚未现场验收。
- QQ 搜索接口已部署，但上游 live 响应具有波动性；QQ 动态签名未现场证明，精确音质/下载明确返回 `503`。
- QQ 二维码仍明确“尚未支持”；QQ 精确 preview 与下载仍缺现场证据。网易云真实手机扫码和确认也尚未执行。
- 会话导入只能由操作者在页面完成。Cookie、token 和平台会话内容不得进入聊天、日志、API、SQLite、命令参数、测试 fixture 或 Git。
- 浏览器单曲与多曲逐文件取回尚无现场证据。
- Navidrome 的容器 ID、启动时间和重启次数未变，且未被修改或重启；尚无合法下载产物可验证发现、标签、封面与歌词。

因此本记录证明发布与自动化门禁通过，不证明一期全部完成。

## 可重复检查器

- `scripts/verify_repository_security.sh`：秘密文件名、TLS 绕过、直接 HTTP client 边界。
- `scripts/verify_compose_contract.sh [compose-file]`：单容器、用户、端口、卷、安全与健康检查。
- `scripts/verify_release.sh`：后端、前端及两份 Compose 的汇总门禁。
- `scripts/verify_nas_runtime.sh [host]`：NAS 健康、运行时安全、`/music` 可写、日志与回滚输入检查；非空状态重启和 Navidrome 发现按设计报告 `BLOCKED`。

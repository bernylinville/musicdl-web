"""Read-only status service for the search spike."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(
    title="musicdl-web status",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Report whether the read-only status service is running."""

    return {"status": "healthy"}


@app.get("/", response_class=HTMLResponse)
def status_page() -> str:
    """Render the deliberately static spike status page."""

    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>musicdl-web · 搜索 Spike 状态</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { max-width: 52rem; margin: 0 auto; padding: 3rem 1.25rem; line-height: 1.65; }
    h1, h2 { line-height: 1.25; }
    .notice { border-left: .3rem solid #d97706; padding: .8rem 1rem; background: #d9770618; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border-bottom: 1px solid #8886; padding: .7rem; text-align: left; }
    .ok { color: #16803c; font-weight: 700; }
    .warn { color: #b45309; font-weight: 700; }
  </style>
</head>
<body>
  <main>
    <h1>musicdl-web 搜索 Spike</h1>
    <p class="notice">
      <strong>只读状态页：</strong>当前仅提供搜索 spike，不能执行下载。
    </p>
    <p>Fixture 与网络安全测试已覆盖 <strong>17+ 项测试</strong>；这不代表下载能力已经解锁。</p>
    <h2>平台状态</h2>
    <table>
      <thead><tr><th>平台</th><th>当前状态</th></tr></thead>
      <tbody>
        <tr><td>网易云音乐</td><td class="ok">搜索 fixture 通过，匿名现场探针成功</td></tr>
        <tr>
          <td>QQ 音乐</td>
          <td class="warn">搜索 fixture 通过，QQ live volatile（现场结果波动）</td>
        </tr>
      </tbody>
    </table>
    <h2>尚未解锁</h2>
    <ul>
      <li>逐曲、逐会话的实际音质清单</li>
      <li>精确单档解析与下载前重验</li>
      <li>合法短试听与媒体校验</li>
      <li>操作者本人账号权益路径</li>
    </ul>
    <h2>下一门槛</h2>
    <p>
      在本机受控平台会话中完成双平台音质、精确下载、试听和账号权益 spike；
      门槛通过前不提供下载入口。
    </p>
  </main>
</body>
</html>
"""

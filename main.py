# -*- coding: utf-8 -*-
from __future__ import annotations
from dotenv import load_dotenv
import os
# --- Windows兼容性与异步补丁 ---
import nest_asyncio
nest_asyncio.apply()

import re
from urllib.parse import urlparse
import httpx
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# --- 核心模块导入 ---
from core.database import engine, Base

# --- 导入所有端点模块 ---
from api.endpoints import clients, sources, topics, intelligence


# --- 数据库初始化 ---
async def create_tables():
    """在应用启动时，根据模型创建数据库表"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# --- 应用实例与启动事件 ---
app = FastAPI(title="IntelliScraper - 智能情报站", debug=True)
templates = Jinja2Templates(directory="templates")

@app.on_event("startup")
async def on_startup():
    await create_tables()

# 静态资源挂载
app.mount("/static", StaticFiles(directory="static"), name="static")


# --- 注册所有模块的路由 ---
app.include_router(clients.api_router)
app.include_router(clients.pages_router)
app.include_router(sources.router)
app.include_router(topics.router)
app.include_router(intelligence.api_router)
app.include_router(intelligence.pages_router)

# --- 通用页面：首页 ---
@app.get("/", response_class=HTMLResponse)
async def page_root(request: Request):
    """渲染应用首页"""
    return templates.TemplateResponse("index.html", {"request": request})


# --- 通用工具：服务器代理 ---
def _ensure_url_scheme(raw: str) -> str:
    if not raw:
        return raw
    if re.match(r"^https?://", raw, flags=re.I):
        return raw
    if raw.startswith("//"):
        return "https:" + raw
    return "https://" + raw

def _inject_base_tag(html: str, base_url: str) -> str:
    """在 <head> 注入 <base> 以修复相对链接（CSS/图片等）"""
    base_tag = f'<base href="{base_url}">'
    if "<head>" in html:
        return html.replace("<head>", f"<head>\n{base_tag}", 1)
    m = re.search(r"<head\b[^>]*>", html, flags=re.I)
    if m:
        start, end = m.span()
        return html[:end] + "\n" + base_tag + html[end:]
    return base_tag + html

@app.get("/proxy", response_class=HTMLResponse)
async def proxy_external_url(url: str = Query(...)):
    """
    用于“手动配置”页面 iframe 预览的服务器代理，包含多种抓取策略。
    """
    url = _ensure_url_scheme(url)
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    timeout = httpx.Timeout(25.0, connect=10.0)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    }

    try:
        async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True, http2=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            content = r.text or ""
            content = _inject_base_tag(content, base_url)
            return HTMLResponse(content=content, status_code=200)
    except Exception as e:
        hint = f"""
        <div style="padding:16px;font-family:system-ui;">
          <h3>无法预览该页面</h3>
          <p>原因: <strong>{e}</strong></p>
          <p>这可能是常见的反爬虫策略导致的。你可以尝试直接使用“AI自动配置”或手动填写选择器后进行“测试”。</p>
          <p>原始链接：<a href="{url}" target="_blank" rel="noopener">{url}</a></p>
        </div>
        """.strip()
        return HTMLResponse(content=hint, status_code=200)


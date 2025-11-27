# -*- coding: utf-8 -*-
"""
Recipe builder: 从 URL 抓取 HTML，推断 CSS 选择器，必要时用 LLM 校正，生成抓取配方。
返回字典结构（适配 JSON 存储），调用方可再用 ScraperRecipe(**dict) 转为对象。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from html import unescape
from typing import Optional, Dict, Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from .improved_selectors import guess_selectors_improved

logger = logging.getLogger(__name__)


def _get_llm_client():
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, None, None

    base_url = (
        os.getenv("DEEPSEEK_API_BASE")
        or os.getenv("OPENAI_BASE_URL")
        or "https://api.deepseek.com"
    )
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        return client, model, api_key
    except Exception as e:
        logger.warning("初始化 LLM 客户端失败，将跳过 AI 校正：%s", e)
        return None, None, None


def extract_domain(url: str) -> str:
    p = urlparse(url)
    host = (p.netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


async def fetch_page_html(url: str, timeout: int = 20) -> str:
    """
    优先静态抓取；失败后（且未禁用）再尝试 Playwright（如安装可用）。
    强制禁用 Playwright：DISABLE_PLAYWRIGHT=1
    """
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0.0.0 Safari/537.36")
    }

    # 1) httpx
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as s:
            r = s.get(url)
            r.raise_for_status()
            return r.text
    except Exception as e:
        logger.info("httpx 静态抓取失败：%s", e)

    # 2) Playwright（如未禁用）
    if os.getenv("DISABLE_PLAYWRIGHT") == "1":
        raise RuntimeError("静态抓取失败，且已禁用 Playwright")

    try:
        from playwright.async_api import async_playwright
    except Exception as e:
        raise RuntimeError(f"静态抓取失败，Playwright 不可用：{e}")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(extra_http_headers=headers)
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                return await page.content()
            finally:
                await browser.close()
    except Exception as e:
        raise RuntimeError(f"Playwright 获取页面失败: {e}")


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", unescape((s or "").strip()))


def _score_container(el) -> int:
    from bs4 import Tag
    if not isinstance(el, Tag):
        return 0
    text = (el.get("class") or []) + ([el.get("id")] if el.get("id") else [])
    text_str = " ".join([t for t in text if t])[:200].lower()
    score = 0
    for kw in ("news", "list", "posts", "articles", "stream", "feed"):
        if kw in text_str:
            score += 5
    links = el.find_all("a", href=True)
    score += min(len(links), 30)
    if el.find(["h1", "h2", "h3"]):
        score += 5
    return score


def _css_for_element(el) -> str:
    from bs4 import Tag
    if not isinstance(el, Tag):
        return ""
    parts = []
    cur = el
    while cur and isinstance(cur, Tag):
        name = cur.name
        if cur.get("id"):
            parts.append(f"{name}#{cur.get('id')}")
            break
        cls = cur.get("class") or []
        if cls:
            parts.append(f"{name}." + ".".join(cls[:3]))
        else:
            if cur.parent:
                siblings = [c for c in cur.parent.find_all(cur.name, recursive=False)]
                idx = siblings.index(cur) + 1 if cur in siblings else 1
                parts.append(f"{name}:nth-of-type({idx})")
            else:
                parts.append(name)
        cur = cur.parent
        if len(parts) >= 5:
            break
    return " > ".join(reversed(parts))


def _find_list_container(soup: BeautifulSoup):
    candidates = []
    for tag in soup.find_all(["ul", "ol", "div", "section"]):
        sc = _score_container(tag)
        if sc >= 5:
            candidates.append((sc, tag))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1] if candidates else None


def _guess_selectors(html: str) -> Dict[str, Optional[str]]:
    soup = BeautifulSoup(html, "html.parser")

    list_el = _find_list_container(soup)
    if not list_el:
        list_selector = "body"
        container = soup
    else:
        list_selector = _css_for_element(list_el)
        container = list_el

    title_el = None
    link_el = None
    for a in container.select("a[href]"):
        text = _clean_text(a.get_text())
        if 8 <= len(text) <= 120:
            title_el = a
            link_el = a
            break
    if not title_el:
        for h in container.find_all(["h3", "h2", "h1"]):
            text = _clean_text(h.get_text())
            if 6 <= len(text) <= 120:
                title_el = h
                link_el = h.find("a", href=True) or h
                break

    date_el = None
    for d in container.select("time, .date, .time, [datetime]"):
        if _clean_text(d.get_text()):
            date_el = d
            break

    title_selector = _css_for_element(title_el) if title_el else None
    link_selector = _css_for_element(link_el) if link_el else None
    date_selector = _css_for_element(date_el) if date_el else None

    if not link_selector:
        link_selector = "a[href]"
    if not title_selector:
        title_selector = link_selector

    return {
        "list_selector": list_selector or "body",
        "title_selector": title_selector,
        "link_selector": link_selector,
        "date_selector": date_selector,
    }


async def _llm_refine_selectors(html: str, url: str, guess: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    client, model, _ = _get_llm_client()
    if not client or not model:
        return guess

    domain = extract_domain(url)
    prompt = f"""
    你是一名资深前端抓取工程师。基于下面网页的 HTML 片段，请为“新闻/文章列表页”推导 4 个 CSS 选择器：
    - list_selector：**列表容器**（一个元素，内部包含多条新闻的重复条目）
    - title_selector：每条新闻的**标题元素**
    - link_selector：每条新闻的**主链接元素**（a[href]）
    - date_selector：每条新闻的**日期元素**；若无可用请返回 null

    约束：
    1) 仅返回 JSON（不加代码块/解释），键名固定：list_selector、title_selector、link_selector、date_selector。
    2) 选择器尽量稳定：优先 id/class，必要时 :nth-of-type；避免随机 hash 类名。
    3) **不要选到导航/页眉/页脚/侧栏**（class/id 含 nav/menu/header/footer/sidebar 的都排除）。
    4) list_selector 必须仅包含“正文列表”的父容器，能覆盖**至少 10 条**新闻条目。
    5) title_selector/link_selector 必须在 list_selector 的**子孙范围内**，指向每条新闻卡片里的标题/主链接。
    6) link_selector 应指向文章详情页，尽量排除“分类/标签/搜索”等链接（可用 :not 过滤）。
    7) 无法确定 date_selector 时返回 null。
    一个目前合理的猜测是：{guess}
    URL: {url}
    Domain: {domain}
    HTML（截断片段）:
    {html[:10000]}
    """.strip()

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个精通 CSS 选择器和网页结构分析的助手。只返回严格 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        content = (resp.choices[0].message.content or "").strip()
        data = None
        try:
            data = json.loads(content)
        except Exception:
            m = re.search(r"\{[\s\S]*\}", content)
            if m:
                data = json.loads(m.group(0))

        if not isinstance(data, dict):
            return guess

        result = {
            "list_selector": data.get("list_selector") or guess["list_selector"],
            "title_selector": data.get("title_selector") or guess["title_selector"],
            "link_selector": data.get("link_selector") or guess["link_selector"],
            "date_selector": data.get("date_selector") or guess.get("date_selector"),
        }

        if not result["link_selector"]:
            result["link_selector"] = "a[href]"
        if not result["title_selector"]:
            result["title_selector"] = result["link_selector"]
        if not result["list_selector"]:
            result["list_selector"] = guess["list_selector"] or "body"

        return result
    except Exception as e:
        logger.warning("LLM 校正失败，使用启发式选择器：%s", e)
        return guess


async def create_recipe_with_ai(url: str) -> dict:
    """
    1) 抓取 HTML（静态优先，必要时 Playwright）；
    2) 启发式猜测选择器；
    3) 如配置了 LLM，用其校正；
    4) 返回 recipe dict。
    """
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url

    domain = extract_domain(url)
    html = await fetch_page_html(url)
    guess = _guess_selectors(html)
    refined = await _llm_refine_selectors(html, url, guess)

    recipe = {
        "domain": domain,
        "start_url": url,
        "list_selector": refined["list_selector"],
        "title_selector": refined["title_selector"],
        "link_selector": refined["link_selector"],
        "date_selector": refined.get("date_selector"),
        "load_strategy": "static",
        "wait_until": "domcontentloaded",
        "wait_ms": 0,
    }

    for k in ("list_selector", "title_selector", "link_selector"):
        if not recipe.get(k):
            raise RuntimeError(f"选择器生成失败：缺少 {k}")

    return recipe

# 完整的scraper_main.py修复版本

from __future__ import annotations

import os
import re
import asyncio
from typing import List, Optional, Dict, Tuple, Set
from urllib.parse import urljoin, urldefrag

import httpx
from bs4 import BeautifulSoup, Tag

# 条件导入playwright
try:
    from playwright.async_api import async_playwright, PlaywrightContextManager

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

from .models import ScraperRecipe, ScrapedItem


def _normalize_url(base: str, href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    url = urljoin(base, href)
    url, _ = urldefrag(url)
    return url


def _text_of(el: Optional[Tag]) -> Optional[str]:
    if not el:
        return None
    txt = el.get_text(" ", strip=True)
    return txt or None


def _select_all(scope: Tag, selector: Optional[str]) -> List[Tag]:
    if not selector:
        return []
    try:
        return list(scope.select(selector))
    except Exception:
        return []


def _select_one(scope: Tag, selector: Optional[str]) -> Optional[Tag]:
    if not selector:
        return None
    try:
        return scope.select_one(selector)
    except Exception:
        return None


def _find_item_root(anchor: Tag, box: Tag) -> Tag:
    """从链接元素向上查找最合适的文章根节点"""
    cur = anchor
    for _ in range(12):
        if cur is None or cur == box or not isinstance(cur, Tag):
            break
        name = (cur.name or "").lower()
        classes = " ".join(cur.get("class", [])).lower()
        if name in ("article", "li") or re.search(r"(post|entry|card|item|result)", classes):
            return cur
        cur = cur.parent if isinstance(cur.parent, Tag) else None
    return anchor.parent if isinstance(anchor.parent, Tag) else box


class Scraper:
    def __init__(self, recipe: ScraperRecipe):
        self.recipe = recipe
        self.base_url = recipe.start_url or f"https://{recipe.domain}/"

    async def _fetch_html_static(self, url: str) -> str:
        """静态抓取HTML"""
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            return r.text

    async def _fetch_html_dynamic(self, url: str) -> str:
        """动态抓取HTML - 修复async问题"""
        if os.environ.get("DISABLE_PLAYWRIGHT", "0") == "1" or not PLAYWRIGHT_AVAILABLE:
            return await self._fetch_html_static(url)

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=['--disable-blink-features=AutomationControlled']
                )
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
                page = await context.new_page()

                await page.goto(url, wait_until=self.recipe.wait_until or "networkidle")

                if self.recipe.wait_ms and self.recipe.wait_ms > 0:
                    await asyncio.sleep(self.recipe.wait_ms / 1000)

                if self.recipe.infinite_scroll and self.recipe.scroll_times > 0:
                    for _ in range(self.recipe.scroll_times):
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep((self.recipe.scroll_wait_ms or 800) / 1000)

                html = await page.content()
                await context.close()
                await browser.close()
                return html

        except Exception as e:
            print(f"动态抓取失败，回退到静态: {e}")
            return await self._fetch_html_static(url)

    async def _fetch_html(self, url: str) -> str:
        """根据策略选择抓取方式"""
        if self.recipe.load_strategy == "dynamic":
            return await self._fetch_html_dynamic(url)
        return await self._fetch_html_static(url)

    def _is_current_page_element(self, element, soup) -> bool:
        """检查元素是否为当前页元素"""
        if element.get('class'):
            classes = ' '.join(element.get('class'))
            if any(keyword in classes.lower() for keyword in ['current', 'active', 'selected']):
                return True

        parent = element.parent
        if parent and parent.get('class'):
            classes = ' '.join(parent.get('class'))
            if any(keyword in classes.lower() for keyword in ['current', 'active', 'selected']):
                return True

        return False

    async def _get_next_page_url(self, html: str, current_url: str, page_num: int) -> Optional[str]:
        """获取下一页URL - 修复Drupal分页支持"""
        if page_num >= (self.recipe.max_pages or 1) - 1:
            return None

        soup = BeautifulSoup(html, "html.parser")

        # 根据分页模式处理
        pagination_mode = getattr(self.recipe, 'pagination_mode', 'none')

        # 1. 数字分页模式 - 修复Drupal支持
        if pagination_mode == 'number' and hasattr(self.recipe, 'page_pattern') and self.recipe.page_pattern:
            try:
                # 检查是否是Drupal零基页码系统
                drupal_zero_based = getattr(self.recipe, 'drupal_zero_based', False)

                if drupal_zero_based:
                    # Drupal系统：页码从0开始
                    # page_num=0(第1页) → URL中page=0
                    # page_num=1(第2页) → URL中page=1
                    next_page_param = page_num + 1
                else:
                    # 普通系统：页码从1开始
                    # page_num=0(第1页) → URL中page=1
                    # page_num=1(第2页) → URL中page=2
                    next_page_param = page_num + 2

                next_url = self.recipe.page_pattern.replace('{n}', str(next_page_param))

                print(f"数字分页({'Drupal零基' if drupal_zero_based else '标准'}): "
                      f"第{page_num + 2}页 -> page={next_page_param} -> {next_url}")
                return next_url

            except Exception as e:
                print(f"Page pattern处理失败: {e}")

        # 2. 加载更多模式 - 通常需要JavaScript，这里不处理
        elif pagination_mode == 'loadmore':
            print("加载更多模式需要动态渲染，跳过静态分页")
            return None

        # 3. 下一页链接模式
        elif pagination_mode == 'next' and self.recipe.next_page_selector:
            try:
                next_elements = soup.select(self.recipe.next_page_selector)

                # 如果启用了排除当前页选项，过滤掉当前页元素
                if hasattr(self.recipe, 'exclude_current_page') and self.recipe.exclude_current_page:
                    next_elements = [el for el in next_elements
                                     if not self._is_current_page_element(el, soup)]

                if next_elements:
                    next_href = next_elements[0].get("href")
                    if next_href:
                        next_url = _normalize_url(current_url, next_href)
                        # 避免无限循环
                        if next_url != current_url:
                            print(f"下一页链接: {next_url}")
                            return next_url
                        else:
                            print("检测到循环链接，停止分页")
                            return None
                    else:
                        print("未找到下一页链接")

            except Exception as e:
                print(f"Next page selector处理失败: {e}")

        # 4. 兼容旧版本 - 如果没有设置pagination_mode但有next_page_selector
        elif not pagination_mode or pagination_mode == 'none':
            if self.recipe.next_page_selector:
                try:
                    nxt = soup.select_one(self.recipe.next_page_selector)
                    if nxt and nxt.get("href"):
                        next_url = _normalize_url(current_url, nxt.get("href"))
                        if next_url != current_url:
                            print(f"兼容模式下一页: {next_url}")
                            return next_url
                except Exception as e:
                    print(f"兼容模式分页失败: {e}")

        print(f"无法获取下一页URL，当前模式: {pagination_mode}")
        return None

    async def _parse_one_page(self, html: str, page_url: str, max_links: int,
                              results: Dict[str, ScrapedItem]) -> Tuple[int, int]:
        """解析单页；返回 (容器数量, 本页原始链接尝试数)"""
        soup = BeautifulSoup(html, "html.parser")
        boxes = _select_all(soup, self.recipe.list_selector)
        container_count = len(boxes) if boxes else 0

        if not boxes:
            boxes = [soup]
            container_count = 1

        raw_links = 0
        processed_elements: Set[int] = set()

        for box in boxes:
            if self.recipe.link_selector:
                anchors = _select_all(box, self.recipe.link_selector)
            else:
                anchors = [a for a in box.find_all("a", href=True)
                           if not re.search(r"(tag|category|search|author|page/\d+|#|javascript:|mailto:)/?",
                                            a.get("href", ""), re.I)]

            raw_links += len(anchors)

            for a in anchors:
                element_id = id(a)
                if element_id in processed_elements:
                    continue
                processed_elements.add(element_id)

                href = a.get("href")
                if not href:
                    continue

                url = _normalize_url(page_url, href)
                if not url or url in results:
                    continue

                item_root = _find_item_root(a, box)

                title = None
                if self.recipe.title_selector:
                    title_el = _select_one(item_root, self.recipe.title_selector)
                    if not title_el and item_root != box:
                        title_el = _select_one(box, self.recipe.title_selector)
                    title = _text_of(title_el)

                if not title:
                    title = _text_of(a)

                date_text = None
                if self.recipe.date_selector:
                    date_el = _select_one(item_root, self.recipe.date_selector)
                    if not date_el and item_root != box:
                        date_el = _select_one(box, self.recipe.date_selector)
                    date_text = _text_of(date_el)

                if title and url and title.strip():
                    existing_titles = {item.title for item in results.values()}
                    if title not in existing_titles:
                        results[url] = ScrapedItem(title=title, url=url, date=date_text)

                if len(results) >= max_links:
                    return container_count, raw_links

        return container_count, raw_links

    async def scrape(self, max_links: int = 20):
        """执行爬取任务"""
        current_url = self.base_url
        results: Dict[str, ScrapedItem] = {}
        total_containers = 0
        total_raw = 0
        pages = 0
        truncated = False

        print(f"开始爬取，分页模式: {getattr(self.recipe, 'pagination_mode', 'none')}")
        print(f"最大页数: {self.recipe.max_pages}")
        print(f"起始URL: {current_url}")

        for page_num in range(max(1, self.recipe.max_pages or 1)):
            pages += 1
            print(f"\n=== 处理第 {page_num + 1} 页 ===")
            print(f"当前URL: {current_url}")

            try:
                html = await self._fetch_html(current_url)
                c_cnt, raw = await self._parse_one_page(html, current_url, max_links, results)
                total_containers += c_cnt if c_cnt > 0 else 0
                total_raw += raw

                print(f"本页结果: 容器={c_cnt}, 原始链接={raw}, 总结果={len(results)}")

                if len(results) >= max_links:
                    truncated = True
                    print(f"达到最大链接数 {max_links}，停止爬取")
                    break

                # 使用增强的分页逻辑
                next_url = await self._get_next_page_url(html, current_url, page_num)

                if not next_url:
                    print("未找到下一页，爬取结束")
                    break

                current_url = next_url

            except Exception as e:
                print(f"处理第 {page_num + 1} 页时出错: {e}")
                import traceback
                traceback.print_exc()
                break

        unique = len(results)
        meta = {
            "containers": total_containers or (1 if unique else 0),
            "raw": total_raw,
            "unique": unique,
            "dedup": max(0, total_raw - unique),
            "pages": pages,
            "truncated": truncated,
            "strategy": self.recipe.load_strategy,
            "base_url": self.base_url,
            "pagination_mode": getattr(self.recipe, 'pagination_mode', 'none'),
            "pagination_working": pages > 1,
        }

        print(f"\n=== 爬取完成 ===")
        print(f"总页数: {pages}")
        print(f"总结果: {unique}")
        print(f"分页是否工作: {pages > 1}")

        return list(results.values()), meta


async def run_single_scrape_test(recipe: ScraperRecipe | dict, max_links: int = 10):
    """统一测试入口"""
    recipe_obj = recipe if isinstance(recipe, ScraperRecipe) else ScraperRecipe(**recipe)
    scraper = Scraper(recipe_obj)
    items, meta = await scraper.scrape(max_links=max_links)
    return {
        "results": [it.model_dump() for it in items],
        "meta": meta,
    }
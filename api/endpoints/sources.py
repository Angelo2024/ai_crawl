# api/endpoints/sources.py - 全面增强版
from __future__ import annotations
import json
import re
import asyncio
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.templating import Jinja2Templates
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time

from core.database import get_db
from crud import crud_operations
from schemas.base_schemas import SourceSchema
from scraper.models import ScraperRecipe
from scraper.scraper_main import run_single_scrape_test
from scraper.recipe_builder import create_recipe_with_ai
from scraper.improved_selectors import guess_selectors_improved

router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ----------------------------- 页面路由 -----------------------------

@router.get("/sources", response_class=HTMLResponse)
async def page_sources(
        request: Request,
        db: AsyncSession = Depends(get_db),
        q: Optional[str] = Query(""),
        topic_filter: Optional[str] = Query(""),
        sort: str = Query("id"),
        order: str = Query("asc")
):
    """渲染信息源管理主页面，支持前端分页"""
    try:
        all_topics = await crud_operations.get_topics(db)
    except Exception:
        all_topics = []

    # 验证排序参数
    valid_sort_fields = ['id', 'domain', 'created_at', 'updated_at']
    if sort not in valid_sort_fields:
        sort = 'id'
    if order.lower() not in ['asc', 'desc']:
        order = 'asc'

    # 获取所有源数据（不再在后端分页，让前端处理）
    try:
        topic_id = int(topic_filter) if topic_filter else None
    except ValueError:
        topic_id = None

    # 获取所有源数据
    sources = await crud_operations.get_sources_with_filter_and_sort(
        db, q=None, topic_id=None, sort_by='id', order_by='asc'  # 获取所有数据，让前端处理筛选和排序
    )

    # 转换数据格式以兼容前端JavaScript
    sources_data = []
    for source in sources:
        source_dict = {
            "id": source.id,
            "domain": source.domain,
            "start_url": getattr(source, 'start_url', None),
            "topics": [],
            "updated_at": source.updated_at.isoformat() if source.updated_at else None,
            "created_at": source.created_at.isoformat() if hasattr(source, 'created_at') and source.created_at else None
        }

        # 处理议题关联
        if hasattr(source, 'topics') and source.topics:
            source_dict["topics"] = [{"name": topic.name} for topic in source.topics]

        sources_data.append(source_dict)

    return templates.TemplateResponse("sources.html", {
        "request": request,
        "all_topics": all_topics,
        "sources": sources_data,  # 传递格式化后的数据
        "current_sort": sort,
        "current_order": order
    })


@router.get("/sources/manual", response_class=HTMLResponse)
async def page_manual_config(request: Request, url: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """渲染手动配置页面（新建时）"""
    try:
        all_topics = await crud_operations.get_topics(db)
    except Exception:
        all_topics = []  # 如果没有topics表，使用空列表

    domain = crud_operations._parse_domain(url or "")
    source = await crud_operations.get_source_by_domain(db, domain) if domain else None
    recipe = ScraperRecipe(**json.loads(source.recipe_json)) if source and source.recipe_json else None
    return templates.TemplateResponse("manual_configure.html",
                                      {"request": request, "url": url, "domain": domain, "source": source,
                                       "all_topics": all_topics, "recipe": recipe})


@router.get("/sources/{source_id}/edit", response_class=HTMLResponse)
async def page_edit_source(source_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """渲染手动配置页面（编辑已有源时）"""
    try:
        all_topics = await crud_operations.get_topics(db)
    except Exception:
        all_topics = []  # 如果没有topics表，使用空列表

    source = await crud_operations.get_source(db, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    recipe = ScraperRecipe(**json.loads(source.recipe_json)) if source.recipe_json else None
    domain = source.domain
    url = getattr(recipe, 'start_url', f"https://{domain}/") if recipe else f"https://{domain}/"
    return templates.TemplateResponse("manual_configure.html",
                                      {"request": request, "source": source, "domain": domain, "url": url,
                                       "all_topics": all_topics, "recipe": recipe})


# ----------------------------- 增强API路由 -----------------------------

@router.post("/api/sources/ai_suggest_enhanced", response_class=JSONResponse)
async def api_ai_suggest_enhanced(
        url: str = Form(...),
        deep_analysis: bool = Form(False),
        pagination_detect: bool = Form(False),
        content_optimize: bool = Form(False),
        auto_test: bool = Form(False)
):
    """增强版AI配置API"""
    url = (url or "").strip()
    if not url:
        return JSONResponse({"ok": False, "error": "URL 不能为空"}, status_code=400)

    try:
        # 创建基础配方
        recipe = await create_recipe_with_ai(url)

        # 增强分析
        if deep_analysis:
            recipe = await enhance_recipe_with_deep_analysis(recipe, url)

        # 分页检测
        if pagination_detect:
            pagination_config = await detect_pagination_patterns_enhanced(url)
            recipe.update(pagination_config)

        # 内容优化
        if content_optimize:
            recipe = await optimize_content_extraction(recipe, url)

        # 生成洞察
        insights = await generate_ai_insights(recipe, url)

        # 自动测试
        test_results = None
        if auto_test:
            try:
                test_results = await run_single_scrape_test(recipe, max_links=10)
                insights['test_score'] = calculate_test_score(test_results)
            except Exception as e:
                insights['test_error'] = str(e)

        return JSONResponse({
            "ok": True,
            "recipe": recipe,
            "insights": insights,
            "test_results": test_results
        })

    except Exception as e:
        return JSONResponse({
            "ok": False,
            "error": f"AI配置失败：{str(e)}"
        }, status_code=500)


async def enhance_recipe_with_deep_analysis(recipe: dict, url: str) -> dict:
    """深度分析页面结构"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            response = await client.get(url, headers=headers, follow_redirects=True)
            html = response.text

        soup = BeautifulSoup(html, 'html.parser')

        # 使用improved_selectors进行深度分析
        improved_selectors = guess_selectors_improved(html)

        # 合并改进的选择器
        for key, value in improved_selectors.items():
            if value and value != 'body':  # 只更新有意义的选择器
                recipe[key] = value

        # 检测反爬虫机制
        antibot_detected = detect_antibot_measures(soup)
        if antibot_detected:
            recipe['load_strategy'] = 'dynamic'
            recipe['wait_ms'] = 3000
            recipe['anti_bot_detected'] = True

        return recipe

    except Exception as e:
        print(f"深度分析失败: {e}")
        return recipe


async def detect_pagination_patterns_enhanced(url: str) -> dict:
    """增强版分页检测 - 处理复杂分页情况"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            response = await client.get(url, headers=headers, follow_redirects=True)
            html = response.text

        soup = BeautifulSoup(html, 'html.parser')

        # 检查下一页模式 - 增强版
        next_patterns = [
            'a:contains("Next")', 'a:contains("下一页")', 'a:contains("更多")',
            'a:contains(">")', 'a:contains("»")', 'a:contains("›")',
            'a[href*="page"]', '.next', '.more', '[class*="next"]', '[class*="more"]',
            'a[aria-label*="next"]', 'a[title*="next"]', 'a[title*="下一页"]'
        ]

        # 特殊处理：排除当前页
        for pattern in next_patterns:
            try:
                elements = soup.select(pattern)
                if elements:
                    # 检查是否需要排除当前页
                    exclude_current = check_current_page_exclusion(soup)

                    return {
                        'pagination_mode': 'next',
                        'next_page_selector': pattern,
                        'exclude_current_page': exclude_current,
                        'max_pages': 5
                    }
            except:
                continue

        # 检查页码模式 - 增强版
        pagination_info = analyze_number_pagination(soup, url)
        if pagination_info:
            return pagination_info

        # 检查加载更多按钮 - 增强版
        load_more_info = analyze_load_more_patterns(soup)
        if load_more_info:
            return load_more_info

        # 检查无限滚动
        infinite_scroll_info = detect_infinite_scroll(soup)
        if infinite_scroll_info:
            return infinite_scroll_info

        return {'pagination_mode': 'none'}

    except Exception as e:
        print(f"分页检测失败: {e}")
        return {'pagination_mode': 'none'}


def check_current_page_exclusion(soup: BeautifulSoup) -> bool:
    """检查是否需要排除当前页"""
    current_indicators = [
        'span.current', '.active', '[aria-current="page"]',
        '.page-current', '.current-page', '[class*="current"]'
    ]

    for indicator in current_indicators:
        try:
            if soup.select(indicator):
                return True
        except:
            continue

    return False


def analyze_number_pagination(soup: BeautifulSoup, base_url: str) -> Optional[dict]:
    """分析数字页码分页"""
    page_links = soup.find_all('a', href=True)
    page_numbers = []

    for link in page_links:
        text = link.get_text(strip=True)
        if text.isdigit() and 1 <= int(text) <= 100:
            page_numbers.append((int(text), link.get('href')))

    if len(page_numbers) >= 3:
        # 分析URL模式
        hrefs = [href for _, href in page_numbers]
        pattern = analyze_url_pattern_enhanced(hrefs, base_url)

        if pattern:
            return {
                'pagination_mode': 'number',
                'page_pattern': pattern,
                'max_pages': 10,
                'detected_pages': len(page_numbers)
            }

    return None


def analyze_load_more_patterns(soup: BeautifulSoup) -> Optional[dict]:
    """分析加载更多模式"""
    load_more_selectors = [
        'button:contains("Load")', 'button:contains("More")',
        'button:contains("加载")', 'button:contains("更多")',
        '.load-more', '[class*="load"]', '[class*="more"]',
        'a:contains("Load")', 'a:contains("More")',
        '[onclick*="load"]', '[onclick*="more"]'
    ]

    for selector in load_more_selectors:
        try:
            elements = soup.select(selector)
            if elements:
                return {
                    'pagination_mode': 'loadmore',
                    'load_more_selector': selector,
                    'max_pages': 3,
                    'requires_dynamic': True
                }
        except:
            continue

    return None


def detect_infinite_scroll(soup: BeautifulSoup) -> Optional[dict]:
    """检测无限滚动"""
    # 检查是否存在无限滚动的迹象
    scripts = soup.find_all('script')
    for script in scripts:
        if script.string:
            script_content = script.string.lower()
            if any(keyword in script_content for keyword in
                   ['infinite', 'scroll', 'lazyload', 'pagination', 'loadmore']):
                return {
                    'pagination_mode': 'infinite',
                    'requires_dynamic': True,
                    'scroll_strategy': 'bottom',
                    'max_scrolls': 5
                }

    return None


def analyze_url_pattern_enhanced(hrefs: List[str], base_url: str) -> str:
    """增强版URL模式分析"""
    if not hrefs:
        return ""

    first_href = hrefs[0]

    # 处理相对URL
    if not first_href.startswith('http'):
        first_href = urljoin(base_url, first_href)

    # 检查常见模式
    patterns = [
        (r'page=\d+', 'page={n}'),
        (r'/page/\d+', '/page/{n}'),
        (r'/\d+/?$', '/{n}'),
        (r'p=\d+', 'p={n}'),
        (r'offset=\d+', 'offset={n}'),
        (r'start=\d+', 'start={n}')
    ]

    for pattern, replacement in patterns:
        if re.search(pattern, first_href):
            return re.sub(pattern, replacement, first_href)

    return first_href


async def optimize_content_extraction(recipe: dict, url: str) -> dict:
    """优化内容提取"""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url)
            html = response.text

        soup = BeautifulSoup(html, 'html.parser')

        # 优化标题选择器
        if recipe.get('title_selector'):
            title_selector = recipe['title_selector']
            # 检查是否需要排除日期元素
            title_elements = soup.select(title_selector)
            if title_elements:
                sample_element = title_elements[0]
                if sample_element.find('time') or sample_element.find('small'):
                    # 添加排除逻辑
                    recipe['title_exclude_small'] = True
                    recipe['title_exclude_time'] = True

        # 优化链接选择器
        if recipe.get('link_selector'):
            link_selector = recipe['link_selector']
            # 确保链接选择器指向有效链接
            link_elements = soup.select(link_selector)
            valid_links = [el for el in link_elements if el.get('href') and not el.get('href').startswith('#')]
            if len(valid_links) < len(link_elements) * 0.8:
                # 改进链接选择器
                recipe['link_selector'] = f"{link_selector}[href]:not([href^='#'])"

        return recipe

    except Exception as e:
        print(f"内容优化失败: {e}")
        return recipe


def detect_antibot_measures(soup: BeautifulSoup) -> bool:
    """检测反爬虫机制"""
    antibot_indicators = [
        'cloudflare', 'captcha', 'robot', 'bot detection',
        'access denied', 'rate limit', 'please verify',
        'checking your browser', '验证码', '机器人检测'
    ]

    page_text = soup.get_text().lower()
    page_title = soup.title.string.lower() if soup.title else ""

    return any(indicator in page_text or indicator in page_title
               for indicator in antibot_indicators)


async def generate_ai_insights(recipe: dict, url: str) -> dict:
    """生成AI洞察"""
    insights = {
        'complexity': 3,  # 1-5
        'confidence': 0.8,
        'pagination_type': recipe.get('pagination_mode', 'none'),
        'content_quality': '高',
        'strategy_recommendation': '静态抓取',
        'warnings': [],
        'recommendations': []
    }

    # 分析复杂度
    if recipe.get('anti_bot_detected'):
        insights['complexity'] = 5
        insights['confidence'] = 0.6
        insights['strategy_recommendation'] = '动态渲染'
        insights['warnings'].append('检测到反爬虫机制')
        insights['recommendations'].append('建议使用隐身模式')

    if recipe.get('pagination_mode') == 'loadmore':
        insights['complexity'] = 4
        insights['strategy_recommendation'] = '动态渲染'
        insights['recommendations'].append('需要JavaScript支持')

    if recipe.get('pagination_mode') == 'infinite':
        insights['complexity'] = 5
        insights['recommendations'].append('需要滚动加载策略')

    return insights


def calculate_test_score(test_results: dict) -> float:
    """计算测试分数"""
    if not test_results or 'results' not in test_results:
        return 0.0

    results = test_results['results']
    if not results:
        return 0.0

    score = 0.0
    total_items = len(results)

    for item in results:
        if item.get('title') and len(item['title'].strip()) > 5:
            score += 0.4
        if item.get('url') and item['url'].startswith('http'):
            score += 0.4
        if item.get('date'):
            score += 0.2

    return min(score / total_items, 1.0)


@router.post("/api/sources/container_analysis", response_class=JSONResponse)
async def api_container_analysis(
        url: str = Form(...),
        container_selector: str = Form(...)
):
    """容器元素分析API"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            response = await client.get(url, headers=headers, follow_redirects=True)
            html = response.text

        soup = BeautifulSoup(html, 'html.parser')

        # 查找容器
        containers = soup.select(container_selector)
        if not containers:
            return JSONResponse({
                "success": False,
                "error": "未找到指定的容器元素"
            })

        container = containers[0]

        # 分析容器内的元素
        elements_info = analyze_container_elements(container)

        return JSONResponse({
            "success": True,
            "elements": elements_info
        })

    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


def analyze_container_elements(container) -> List[dict]:
    """分析容器内的所有元素"""
    elements = []

    # 获取所有子元素
    for idx, element in enumerate(container.find_all(True)):
        # 跳过脚本和样式标签
        if element.name in ['script', 'style', 'noscript']:
            continue

        text = element.get_text(strip=True)
        if not text or len(text) < 2:
            continue

        # 检查是否是链接
        is_link = element.name == 'a' or element.find_parent('a') is not None
        link_href = None
        if is_link:
            link_el = element if element.name == 'a' else element.find_parent('a')
            link_href = link_el.get('href') if link_el else None

        # 智能猜测元素类型
        element_type = guess_element_type(element, text)

        elements.append({
            "index": idx,
            "tag": element.name,
            "text": text[:100],  # 限制文本长度
            "selector": generate_element_selector(element),
            "is_link": is_link,
            "link_href": link_href,
            "suggested_type": element_type,
            "attributes": dict(element.attrs) if element.attrs else {}
        })

    return elements[:20]  # 限制返回数量


def guess_element_type(element, text: str) -> str:
    """智能猜测元素类型"""
    text_lower = text.lower()

    # 检查日期模式
    date_patterns = [
        r'\d{4}[-/]\d{1,2}[-/]\d{1,2}',
        r'\d{1,2}[-/]\d{1,2}[-/]\d{4}',
        r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)',
        r'\d{1,2}\s+(minutes?|hours?|days?|weeks?|months?)\s+ago'
    ]

    if any(re.search(pattern, text_lower) for pattern in date_patterns):
        return 'date'

    # 检查时间标签
    if element.name == 'time' or 'time' in element.get('class', []):
        return 'date'

    # 检查链接
    if element.name == 'a' or element.find_parent('a'):
        # 进一步判断是否为标题链接
        if len(text) > 20 and len(text) < 200:
            return 'title'
        return 'link'

    # 检查标题标签
    if element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
        return 'title'

    # 检查类名
    classes = ' '.join(element.get('class', [])).lower()
    if any(keyword in classes for keyword in ['title', 'headline', 'subject']):
        return 'title'

    if any(keyword in classes for keyword in ['date', 'time', 'publish', 'created']):
        return 'date'

    # 基于文本长度和位置猜测
    if 20 <= len(text) <= 200:
        return 'title'
    elif len(text) < 20:
        return 'date'

    return 'content'


def generate_element_selector(element) -> str:
    """生成元素选择器"""
    if element.get('id'):
        return f"#{element['id']}"

    if element.get('class'):
        classes = '.'.join(element['class'])
        return f".{classes}"

    # 基于结构生成选择器
    tag = element.name
    parent = element.parent

    if parent and parent.name != 'body':
        siblings = parent.find_all(tag)
        if len(siblings) > 1:
            index = siblings.index(element) + 1
            return f"{tag}:nth-of-type({index})"

    return tag


@router.post("/api/sources/test_preview_enhanced", response_class=HTMLResponse)
async def api_test_preview_enhanced(
        request: Request,
        limit: int = Form(10),
        # 手动接收所有表单字段
        domain: str = Form(...),
        start_url: Optional[str] = Form(None),
        list_selector: str = Form(""),
        title_selector: str = Form(""),
        link_selector: str = Form(""),
        date_selector: Optional[str] = Form(None),
        load_strategy: str = Form("static"),
        wait_until: str = Form("domcontentloaded"),
        wait_ms: int = Form(0),
        next_page_selector: Optional[str] = Form(None),
        load_more_selector: Optional[str] = Form(None),
        max_pages: int = Form(1),
        pagination_mode: str = Form("none"),
        title_exclude_small: bool = Form(False),
        title_exclude_time: bool = Form(False),
        title_exclude_span: bool = Form(False),
        page_pattern: Optional[str] = Form(None),
        exclude_current_page: bool = Form(False)
):
    """增强版测试预览，支持智能文本提取和复杂分页"""
    try:
        # 构建增强的ScraperRecipe对象
        recipe_data = {
            "domain": domain,
            "start_url": start_url,
            "list_selector": list_selector,
            "title_selector": title_selector,
            "link_selector": link_selector,
            "date_selector": date_selector,
            "load_strategy": load_strategy,
            "wait_until": wait_until,
            "wait_ms": wait_ms,
            "pagination_mode": pagination_mode,
            "max_pages": max_pages,
            "title_exclude_small": title_exclude_small,
            "title_exclude_time": title_exclude_time,
            "title_exclude_span": title_exclude_span,
            "exclude_current_page": exclude_current_page
        }

        # 根据分页模式添加相应配置
        if pagination_mode == "next":
            recipe_data["next_page_selector"] = next_page_selector
        elif pagination_mode == "loadmore":
            recipe_data["load_more_selector"] = load_more_selector
        elif pagination_mode == "number":
            recipe_data["page_pattern"] = page_pattern

        recipe_obj = ScraperRecipe(**recipe_data)

        # 执行测试
        out = await run_single_scrape_test(recipe_obj, max_links=limit)

        # 如果启用了智能文本提取，后处理结果
        if title_exclude_small or title_exclude_time or title_exclude_span:
            out = enhance_extraction_results(out, {
                'exclude_small': title_exclude_small,
                'exclude_time': title_exclude_time,
                'exclude_span': title_exclude_span
            })

        # 增强测试结果
        enhanced_results = enhance_test_results(out)

        return templates.TemplateResponse("test_results.html", {
            "request": request,
            "results": enhanced_results.get("results", []),
            "meta": enhanced_results.get("meta", {}),
            "error": None
        })

    except Exception as e:
        import traceback
        error_msg = f"测试抓取时发生错误: {str(e)}\n{traceback.format_exc()}"
        return templates.TemplateResponse("test_results.html", {
            "request": request,
            "results": [],
            "meta": None,
            "error": error_msg
        })


def enhance_extraction_results(results: dict, options: dict) -> dict:
    """增强提取结果，应用智能文本过滤"""
    if not results.get('results'):
        return results

    enhanced_results = []
    for item in results['results']:
        enhanced_item = item.copy()

        # 智能文本清理
        if enhanced_item.get('title'):
            title = enhanced_item['title']

            # 移除日期模式
            if options.get('exclude_time'):
                title = re.sub(
                    r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}[,\s]*\d{2}:\d{2}\s*(AM|PM|ET|PT|GMT)?\s*',
                    '', title, flags=re.IGNORECASE)
                title = re.sub(r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s*', '', title)

            # 移除常见的标签模式
            if options.get('exclude_span'):
                title = re.sub(r'^[\d\s\-\/:,]+', '', title)

            # 移除小标签内容
            if options.get('exclude_small'):
                title = re.sub(r'\s*\([^)]*\)\s*', ' ', title)

            # 清理多余空白
            title = re.sub(r'\s+', ' ', title).strip()
            enhanced_item['title'] = title

        enhanced_results.append(enhanced_item)

    results['results'] = enhanced_results
    return results


def enhance_test_results(results: dict) -> dict:
    """增强测试结果，添加质量分析"""
    if not results.get('results'):
        return results

    # 分析结果质量
    total_items = len(results['results'])
    quality_scores = []

    for item in results['results']:
        score = 0.0

        # 标题质量
        if item.get('title'):
            title_len = len(item['title'].strip())
            if 10 <= title_len <= 200:
                score += 0.4
            elif title_len > 5:
                score += 0.2

        # 链接质量
        if item.get('url') and item['url'].startswith('http'):
            score += 0.4

        # 日期质量
        if item.get('date') and item['date'].strip():
            score += 0.2

        quality_scores.append(score)

    # 添加质量统计到meta
    if not results.get('meta'):
        results['meta'] = {}

    results['meta'].update({
        'quality_average': sum(quality_scores) / len(quality_scores) if quality_scores else 0,
        'high_quality_count': sum(1 for s in quality_scores if s >= 0.8),
        'medium_quality_count': sum(1 for s in quality_scores if 0.4 <= s < 0.8),
        'low_quality_count': sum(1 for s in quality_scores if s < 0.4),
        'total_time': results.get('meta', {}).get('total_time', 0),
        'extraction_summary': generate_extraction_summary(results['results'])
    })

    return results


def generate_extraction_summary(results: List[dict]) -> dict:
    """生成提取内容摘要"""
    summary = {
        'total_items': len(results),
        'items_with_title': 0,
        'items_with_url': 0,
        'items_with_date': 0,
        'sample_titles': [],
        'sample_urls': [],
        'sample_dates': []
    }

    for item in results[:5]:  # 只取前5个作为样本
        if item.get('title'):
            summary['items_with_title'] += 1
            if len(summary['sample_titles']) < 3:
                summary['sample_titles'].append(item['title'])

        if item.get('url'):
            summary['items_with_url'] += 1
            if len(summary['sample_urls']) < 3:
                summary['sample_urls'].append(item['url'])

        if item.get('date'):
            summary['items_with_date'] += 1
            if len(summary['sample_dates']) < 3:
                summary['sample_dates'].append(item['date'])

    # 计算总数
    for item in results:
        if item.get('title'):
            summary['items_with_title'] += 1
        if item.get('url'):
            summary['items_with_url'] += 1
        if item.get('date'):
            summary['items_with_date'] += 1

    return summary


@router.post("/api/sources/test_pagination_enhanced", response_class=JSONResponse)
async def api_test_pagination_enhanced(
        url: str = Form(...),
        pagination_mode: str = Form(...),
        next_page_selector: Optional[str] = Form(None),
        page_pattern: Optional[str] = Form(None),
        load_more_selector: Optional[str] = Form(None),
        max_pages: int = Form(3),
        exclude_current_page: bool = Form(False)
):
    """增强版分页测试API"""
    try:
        if pagination_mode == "next":
            return await test_next_page_pagination(url, next_page_selector, max_pages, exclude_current_page)
        elif pagination_mode == "number":
            return await test_number_pagination(url, page_pattern, max_pages)
        elif pagination_mode == "loadmore":
            return await test_load_more_pagination_enhanced(url, load_more_selector, max_pages)
        else:
            return JSONResponse({
                "success": False,
                "error": "不支持的分页模式"
            })

    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


async def test_next_page_pagination(url: str, selector: str, max_pages: int, exclude_current: bool) -> JSONResponse:
    """测试下一页分页"""
    page_urls = []
    current_url = url

    async with httpx.AsyncClient(timeout=30) as client:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        for page_num in range(max_pages):
            page_urls.append(current_url)

            # 获取页面内容
            response = await client.get(current_url, headers=headers)
            soup = BeautifulSoup(response.text, 'html.parser')

            # 查找下一页链接
            next_elements = soup.select(selector)

            # 如果启用了排除当前页，过滤掉当前页元素
            if exclude_current:
                next_elements = [el for el in next_elements
                                 if not is_current_page_element(el, soup)]

            if not next_elements:
                break

            next_href = next_elements[0].get('href')
            if not next_href:
                break

            # 构建完整URL
            next_url = urljoin(current_url, next_href)

            # 避免无限循环
            if next_url == current_url or next_url in page_urls:
                break

            current_url = next_url

        return JSONResponse({
            "success": True,
            "results": {
                "pages_tested": len(page_urls),
                "page_urls": page_urls,
                "pagination_working": len(page_urls) > 1
            }
        })


def is_current_page_element(element, soup: BeautifulSoup) -> bool:
    """检查元素是否为当前页元素"""
    # 检查元素本身是否有当前页标记
    if element.get('class'):
        classes = ' '.join(element.get('class'))
        if any(keyword in classes.lower() for keyword in ['current', 'active', 'selected']):
            return True

    # 检查父元素
    parent = element.parent
    if parent and parent.get('class'):
        classes = ' '.join(parent.get('class'))
        if any(keyword in classes.lower() for keyword in ['current', 'active', 'selected']):
            return True

    return False


async def test_number_pagination(url: str, pattern: str, max_pages: int) -> JSONResponse:
    """测试数字分页"""
    page_urls = []

    for page_num in range(1, max_pages + 1):
        page_url = pattern.replace('{n}', str(page_num))
        page_urls.append(page_url)

    # 测试几个页面是否可访问
    async with httpx.AsyncClient(timeout=30) as client:
        working_pages = 0
        for page_url in page_urls[:3]:  # 只测试前3页
            try:
                response = await client.get(page_url)
                if response.status_code == 200:
                    working_pages += 1
            except:
                break

    return JSONResponse({
        "success": True,
        "results": {
            "pages_tested": len(page_urls),
            "page_urls": page_urls,
            "working_pages": working_pages,
            "pagination_working": working_pages > 1
        }
    })


@router.post("/api/sources/manual_enhanced", response_class=HTMLResponse)
async def api_create_or_update_source_enhanced(
        request: Request,
        db: AsyncSession = Depends(get_db),
        # 手动接收所有表单字段
        source_id: Optional[int] = Form(None),
        domain: str = Form(...),
        start_url: Optional[str] = Form(None),
        list_selector: str = Form(""),
        title_selector: str = Form(""),
        link_selector: str = Form(""),
        date_selector: Optional[str] = Form(None),
        load_strategy: str = Form("static"),
        wait_until: str = Form("domcontentloaded"),
        wait_ms: int = Form(0),
        next_page_selector: Optional[str] = Form(None),
        load_more_selector: Optional[str] = Form(None),
        max_pages: int = Form(1),
        # 修复：分页模式默认值改为 "next" 而不是 "none"
        pagination_mode: str = Form("next"),
        page_pattern: Optional[str] = Form(None),
        # 新增：Drupal零基页码支持
        drupal_zero_based: bool = Form(True),
        title_exclude_small: bool = Form(False),
        title_exclude_time: bool = Form(False),
        title_exclude_span: bool = Form(False),
        exclude_current_page: bool = Form(False),
        use_proxy: bool = Form(False),
        rotate_ua: bool = Form(False),
        accept_cookies: bool = Form(False),
        # 可选的议题相关字段
        topic_ids: Optional[List[int]] = Form(None)
):
    """处理增强版手动配置页面的保存/更新请求"""
    try:
        domain = crud_operations._parse_domain(domain)
        if not domain:
            return HTMLResponse('<div class="alert alert-danger">域名无效</div>', status_code=400)

        # 构建增强的recipe数据
        recipe_data = {
            "domain": domain,
            "start_url": start_url,
            "list_selector": list_selector,
            "title_selector": title_selector,
            "link_selector": link_selector,
            "date_selector": date_selector,
            "load_strategy": load_strategy,
            "wait_until": wait_until,
            "wait_ms": wait_ms,
            "pagination_mode": pagination_mode,  # 确保正确保存
            "max_pages": max_pages,
            # 智能文本提取选项
            "title_exclude_small": title_exclude_small,
            "title_exclude_time": title_exclude_time,
            "title_exclude_span": title_exclude_span,
            # 分页相关配置
            "exclude_current_page": exclude_current_page,
            # 反爬虫配置
            "use_proxy": use_proxy,
            "rotate_ua": rotate_ua,
            "accept_cookies": accept_cookies
        }

        # 根据分页模式添加相应配置
        if pagination_mode == "next":
            recipe_data["next_page_selector"] = next_page_selector
        elif pagination_mode == "loadmore":
            recipe_data["load_more_selector"] = load_more_selector
        elif pagination_mode == "number":
            recipe_data["page_pattern"] = page_pattern
            recipe_data["drupal_zero_based"] = drupal_zero_based  # 新增字段

            # 调试日志
            print(f"保存数字分页配置: pattern={page_pattern}, drupal_zero_based={drupal_zero_based}")

        recipe_json = json.dumps(recipe_data, ensure_ascii=False)

        # 调试日志
        print(f"保存的分页模式: {pagination_mode}")
        print(f"完整recipe配置: {recipe_json}")

        # 创建或更新信息源
        source = await crud_operations.upsert_source(db, source_id, domain, recipe_json)

        # 尝试处理议题的关联关系（如果存在）
        if topic_ids is not None:
            try:
                await crud_operations.set_source_topics(db, source.id, topic_ids)
            except Exception as e:
                # 如果没有topics功能，忽略这个错误
                print(f"设置议题关联失败（可能topics表不存在）: {e}")

        msg = "已更新" if source_id else "已创建/覆盖"
        return HTMLResponse(
            f'<div class="alert alert-success mt-3">✅ 保存成功：{msg}！<a href="/sources" class="alert-link ms-2">返回列表</a></div>')

    except Exception as e:
        import traceback
        error_msg = f"保存失败: {str(e)}\n{traceback.format_exc()}"
        print(f"保存配置时发生错误: {error_msg}")  # 添加调试日志
        return HTMLResponse(f'<div class="alert alert-danger mt-3">❌ 保存失败：{error_msg}</div>', status_code=500)


# ----------------------------- 保持原有的其他API -----------------------------

@router.post("/api/sources/{source_id}/test", response_class=HTMLResponse)
async def api_test_source(request: Request, source_id: int, limit: int = Query(10), db: AsyncSession = Depends(get_db)):
    """为"测试"按钮提供后端逻辑"""
    source = await crud_operations.get_source(db, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    try:
        if not source.recipe_json:
            return templates.TemplateResponse("test_results.html",
                                              {"request": request, "results": [], "meta": None,
                                               "error": "该信息源没有配置信息"})

        recipe_data = json.loads(source.recipe_json)
        recipe_obj = ScraperRecipe(**recipe_data)
        out = await run_single_scrape_test(recipe_obj, max_links=limit)
        return templates.TemplateResponse("test_results.html",
                                          {"request": request, "results": out.get("results"), "meta": out.get("meta"),
                                           "error": None})
    except Exception as e:
        import traceback
        error_msg = f"测试抓取时发生错误: {str(e)}\n{traceback.format_exc()}"
        return templates.TemplateResponse("test_results.html",
                                          {"request": request, "results": [], "meta": None, "error": error_msg})


@router.post("/api/sources/test_preview", response_class=HTMLResponse)
async def api_test_preview(
        request: Request,
        limit: int = Form(10),
        # 手动接收所有表单字段来测试预览
        domain: str = Form(...),
        start_url: Optional[str] = Form(None),
        list_selector: str = Form(""),
        title_selector: str = Form(""),
        link_selector: str = Form(""),
        date_selector: Optional[str] = Form(None),
        load_strategy: str = Form("static"),
        wait_until: str = Form("domcontentloaded"),
        wait_ms: int = Form(0),
        next_page_selector: Optional[str] = Form(None),
        max_pages: int = Form(1),
        infinite_scroll: bool = Form(False),
        scroll_times: int = Form(0),
        scroll_wait_ms: int = Form(800)
):
    """不保存，直接用当前表单配置进行测试预览"""
    try:
        # 手动构建ScraperRecipe对象
        recipe_obj = ScraperRecipe(
            domain=domain,
            start_url=start_url,
            list_selector=list_selector,
            title_selector=title_selector,
            link_selector=link_selector,
            date_selector=date_selector,
            load_strategy=load_strategy,
            wait_until=wait_until,
            wait_ms=wait_ms,
            next_page_selector=next_page_selector,
            max_pages=max_pages,
            infinite_scroll=infinite_scroll,
            scroll_times=scroll_times,
            scroll_wait_ms=scroll_wait_ms
        )

        out = await run_single_scrape_test(recipe_obj, max_links=limit)
        return templates.TemplateResponse("test_results.html",
                                          {"request": request, "results": out.get("results"), "meta": out.get("meta"),
                                           "error": None})
    except Exception as e:
        import traceback
        error_msg = f"测试抓取时发生错误: {str(e)}\n{traceback.format_exc()}"
        return templates.TemplateResponse("test_results.html",
                                          {"request": request, "results": [], "meta": None, "error": error_msg})


@router.post("/api/sources/ai_suggest", response_class=JSONResponse)
async def api_ai_suggest(url: str = Form(...)):
    """将AI配置任务添加到队列"""
    url = (url or "").strip()
    if not url:
        return JSONResponse({"ok": False, "error": "URL 不能为空"}, status_code=400)

    task = {"task_type": "create_recipe", "payload": url}
    try:
        with open("job_queue.txt", "a", encoding="utf-8") as f:
            f.write(json.dumps(task, ensure_ascii=False) + "\n")
        return JSONResponse({"ok": True, "message": "✅ AI配置任务已提交到后台处理，请稍后刷新列表查看结果。"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"提交AI任务失败：{e}"}, status_code=500)


@router.delete("/api/sources/{source_id}", response_class=Response)
async def api_delete_source(source_id: int, db: AsyncSession = Depends(get_db)):
    """为"删除"按钮提供后端逻辑"""
    try:
        await crud_operations.delete_source(db, source_id=source_id)
        return Response(status_code=200)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@router.get("/api/sources/{source_id}/config", response_class=HTMLResponse)
async def api_get_source_config(source_id: int, db: AsyncSession = Depends(get_db)):
    """为"查看配置"按钮提供后端逻辑"""
    source = await crud_operations.get_source(db, source_id)
    if not source or not source.recipe_json:
        return HTMLResponse("<pre>无配置信息</pre>")
    try:
        parsed_json = json.loads(source.recipe_json)
        pretty_json = json.dumps(parsed_json, indent=2, ensure_ascii=False)
        return HTMLResponse(
            f"<pre class='bg-body-tertiary p-3 rounded small mb-0' style='white-space:pre-wrap;'>{pretty_json}</pre>")
    except Exception as e:
        return HTMLResponse(f"<pre>JSON解析错误: {str(e)}\n原始内容:\n{source.recipe_json}</pre>")


# ----------------------------- 议题相关API -----------------------------

@router.get("/api/sources/{source_id}/topics-form", response_class=HTMLResponse)
async def api_get_topics_form(source_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """获取编辑议题的表单"""
    try:
        source = await crud_operations.get_source(db, source_id)
        if not source:
            raise HTTPException(404, "Source not found")

        all_topics = await crud_operations.get_topics(db)
        source_topic_ids = [t.id for t in source.topics] if hasattr(source, 'topics') and source.topics else []

        return templates.TemplateResponse("partials/topics_form.html", {
            "request": request,
            "source": source,
            "all_topics": all_topics,
            "source_topic_ids": source_topic_ids
        })
    except Exception as e:
        return HTMLResponse(f'<div class="alert alert-danger">加载议题表单失败: {str(e)}</div>')


@router.post("/api/sources/{source_id}/topics", response_model=SourceSchema)
async def api_update_source_topics(
        source_id: int,
        topic_ids: List[int] = Form([]),  # 从Form接收
        db: AsyncSession = Depends(get_db)
):
    """更新信息源的议题关联，并返回更新后的信息源数据"""
    try:
        updated_source = await crud_operations.set_source_topics(db, source_id, topic_ids)
        if not updated_source:
            raise HTTPException(status_code=404, detail="Source not found")
        return updated_source
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新议题失败: {str(e)}")


@router.get("/api/sources/", response_class=HTMLResponse)
async def api_get_sources_table(
        request: Request,
        db: AsyncSession = Depends(get_db),
        q: Optional[str] = Query(None),
        topic_filter: Optional[int] = Query(None),
        sort: str = Query('id'),
        order: str = Query('asc')
):
    """动态获取信息源表格，支持搜索、筛选和排序。"""
    # 验证排序参数
    valid_sort_fields = ['id', 'domain', 'created_at', 'updated_at']
    if sort not in valid_sort_fields:
        sort = 'id'

    if order.lower() not in ['asc', 'desc']:
        order = 'asc'

    sources = await crud_operations.get_sources_with_filter_and_sort(
        db, q=q, topic_id=topic_filter, sort_by=sort, order_by=order
    )
    context = {
        "request": request,
        "sources": sources,
        "current_sort": sort,
        "current_order": order
    }
    return templates.TemplateResponse("partials/sources_table.html", context)


@router.get("/api/sources/paginated")
async def api_get_sources_paginated(
        db: AsyncSession = Depends(get_db),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        q: Optional[str] = Query(None),
        topic_filter: Optional[str] = Query(None),
        sort: str = Query("id"),
        order: str = Query("asc")
):
    """获取分页的源数据（JSON API）"""
    # 验证排序参数
    valid_sort_fields = ['id', 'domain', 'created_at', 'updated_at']
    if sort not in valid_sort_fields:
        sort = 'id'
    if order.lower() not in ['asc', 'desc']:
        order = 'asc'

    try:
        topic_id = int(topic_filter) if topic_filter else None
    except ValueError:
        topic_id = None

    # 获取总数
    total_count = await crud_operations.get_sources_count(
        db, q=q, topic_id=topic_id
    )

    # 获取分页数据
    sources = await crud_operations.get_sources_with_pagination(
        db,
        page=page,
        page_size=page_size,
        q=q,
        topic_id=topic_id,
        sort_by=sort,
        order_by=order
    )

    # 转换数据格式
    sources_data = []
    for source in sources:
        source_dict = {
            "id": source.id,
            "domain": source.domain,
            "start_url": getattr(source, 'start_url', None),
            "topics": [],
            "updated_at": source.updated_at.isoformat() if source.updated_at else None,
            "created_at": source.created_at.isoformat() if hasattr(source, 'created_at') and source.created_at else None
        }

        if hasattr(source, 'topics') and source.topics:
            source_dict["topics"] = [{"name": topic.name} for topic in source.topics]

        sources_data.append(source_dict)

    return {
        "sources": sources_data,
        "pagination": {
            "current_page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": (total_count + page_size - 1) // page_size
        }
    }


@router.post("/api/sources/test_next_page", response_class=JSONResponse)
async def api_test_next_page(
        url: str = Form(...),
        selector: str = Form(...)
):
    """测试下一页导航功能"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            response = await client.get(url, headers=headers, follow_redirects=True)
            html = response.text

        soup = BeautifulSoup(html, 'html.parser')

        # 查找下一页元素
        next_elements = soup.select(selector)

        if not next_elements:
            return JSONResponse({
                "success": False,
                "error": f"未找到匹配的下一页元素: {selector}"
            })

        next_element = next_elements[0]
        next_href = next_element.get('href')

        if not next_href:
            # 检查是否是JavaScript导航
            onclick = next_element.get('onclick', '')
            data_href = next_element.get('data-href', '')

            return JSONResponse({
                "success": True,
                "next_url": data_href or None,
                "navigation_type": "javascript" if onclick else "data-href" if data_href else "unknown",
                "element_text": next_element.get_text(strip=True),
                "requires_dynamic": bool(onclick or not next_href)
            })

        # 构建完整URL
        from urllib.parse import urljoin
        next_url = urljoin(url, next_href)

        return JSONResponse({
            "success": True,
            "next_url": next_url,
            "navigation_type": "standard_link",
            "element_text": next_element.get_text(strip=True),
            "requires_dynamic": False
        })

    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


@router.post("/api/sources/test_load_more", response_class=JSONResponse)
async def api_test_load_more(
        url: str = Form(...),
        selector: str = Form(...),
        max_clicks: int = Form(2)
):
    """测试加载更多按钮功能"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            response = await client.get(url, headers=headers, follow_redirects=True)
            html = response.text

        soup = BeautifulSoup(html, 'html.parser')

        # 查找加载更多按钮
        load_more_elements = soup.select(selector)

        if not load_more_elements:
            return JSONResponse({
                "success": False,
                "error": f"未找到匹配的加载更多按钮: {selector}"
            })

        load_more_element = load_more_elements[0]
        element_text = load_more_element.get_text(strip=True)

        # 检查是否需要JavaScript
        has_onclick = bool(load_more_element.get('onclick'))
        has_data_attributes = any(attr.startswith('data-') for attr in load_more_element.attrs)

        return JSONResponse({
            "success": True,
            "results": {
                "selector_found": True,
                "element_text": element_text,
                "requires_javascript": has_onclick or has_data_attributes,
                "clicks_simulated": max_clicks,
                "recommendation": "需要使用动态渲染模式" if has_onclick else "可以使用静态抓取"
            }
        })

    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


async def test_load_more_pagination_enhanced(url: str, selector: str, max_clicks: int) -> JSONResponse:
    """增强版加载更多分页测试"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            response = await client.get(url, headers=headers)
            html = response.text

        soup = BeautifulSoup(html, 'html.parser')

        # 查找加载更多按钮
        load_more_elements = soup.select(selector)

        if not load_more_elements:
            return JSONResponse({
                "success": False,
                "error": f"未找到加载更多按钮: {selector}"
            })

        button = load_more_elements[0]

        # 分析按钮特征
        analysis = {
            "button_text": button.get_text(strip=True),
            "has_onclick": bool(button.get('onclick')),
            "has_data_url": bool(button.get('data-url')),
            "has_ajax_attributes": any(attr in ['data-ajax', 'data-load', 'data-src']
                                       for attr in button.attrs),
            "tag_name": button.name,
            "classes": button.get('class', [])
        }

        # 判断是否需要JavaScript
        requires_js = (analysis['has_onclick'] or
                       analysis['has_data_url'] or
                       analysis['has_ajax_attributes'] or
                       'ajax' in ' '.join(analysis['classes']).lower())

        return JSONResponse({
            "success": True,
            "results": {
                "clicks_simulated": max_clicks,
                "selector_found": True,
                "requires_javascript": requires_js,
                "button_analysis": analysis,
                "recommendation": "需要使用动态渲染模式" if requires_js else "可尝试静态模式",
                "suggested_strategy": "dynamic" if requires_js else "static"
            }
        })

    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)
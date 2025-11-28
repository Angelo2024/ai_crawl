import sys
import os
import asyncio

# Windows 修复 (仅保留这一行兼容性代码)
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import nest_asyncio

nest_asyncio.apply()

import json
from datetime import datetime, timedelta
from sqlmodel import SQLModel, create_engine, Session, select
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
import dateparser
from models import SiteConfig, Article, GlobalSettings
from bs4 import BeautifulSoup
from openai import OpenAI
from collections import Counter

sqlite_file_name = "database.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"
engine = create_engine(sqlite_url)

# ⚠️ 填入你的 Key
AI_CLIENT = OpenAI(
    api_key="sk-5836d26f5793456d80465828e44b48de",
    base_url="https://api.deepseek.com"
)


def init_db():
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        if not session.exec(select(GlobalSettings)).first():
            session.add(GlobalSettings())
            session.commit()


def ensure_http(url: str) -> str:
    if not url: return ""
    url = url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        return "https://" + url
    return url


def parse_date_smart(date_str: str, format_str: str = None):
    if not date_str: return None
    date_str = date_str.strip()
    if format_str:
        try:
            return datetime.strptime(date_str, format_str)
        except:
            pass
    return dateparser.parse(date_str)


# === 核心：批量爬取逻辑 (支持 {n} 分页) ===
async def crawl_all_sites(site_ids: list, days_back: int, max_pages: int):
    stats = {"total_crawled": 0, "new_added": 0, "duplicates": 0, "details": []}

    with Session(engine) as session:
        cutoff_date = datetime.now() - timedelta(days=days_back)
        browser_config = BrowserConfig(headless=False, verbose=True, user_agent_mode="random")

        async with AsyncWebCrawler(config=browser_config) as crawler:
            for site_id in site_ids:
                config = session.get(SiteConfig, site_id)
                if not config: continue
                if not config.is_active:
                    print(f"[SKIP] 跳过未启用: {config.name}")
                    continue

                site_stat = {"name": config.name, "new": 0, "dup": 0}
                base_url = ensure_http(config.url)

                # === 判断分页模式 ===
                # 模式 A: URL 包含 {n} -> 数字分页
                # 模式 B: CSS 选择器 -> 动态翻页
                is_number_pagination = "{n}" in base_url

                print(f"[INFO] 开始爬取: {config.name} (模式: {'数字分页' if is_number_pagination else 'CSS翻页'})")

                current_url = base_url
                page_num = 1

                while page_num <= max_pages:
                    # 1. 确定当前页 URL
                    if is_number_pagination:
                        current_url = base_url.replace("{n}", str(page_num))
                    # else: CSS 模式下 current_url 会在循环末尾更新

                    if not current_url: break

                    print(f"   🕷️ 抓取第 {page_num} 页: {current_url}")

                    # 2. 构建提取规则
                    fields = [
                        {"name": "title", "selector": config.title_selector, "type": "text"},
                        {"name": "url", "selector": config.link_selector, "type": "attribute", "attribute": "href"},
                    ]
                    if config.date_selector:
                        fields.append({"name": "date", "selector": config.date_selector, "type": "text"})

                    # 只有 CSS 模式才需要提取下一页链接
                    if config.next_page_selector and not is_number_pagination:
                        fields.append({"name": "next_page", "selector": config.next_page_selector, "type": "attribute",
                                       "attribute": "href"})

                    schema = {"baseSelector": config.list_selector, "fields": fields}

                    run_config = CrawlerRunConfig(
                        extraction_strategy=JsonCssExtractionStrategy(schema),
                        cache_mode=CacheMode.BYPASS,
                        js_code="window.scrollTo(0, document.body.scrollHeight);",
                        wait_for="body"
                    )

                    result = await crawler.arun(url=current_url, config=run_config)

                    if not result.success:
                        print(f"   [ERROR] 页面加载失败")
                        break

                    try:
                        items = json.loads(result.extracted_content)
                    except:
                        items = []

                    if not items:
                        print(f"   [WARN] 本页无数据")
                        break

                    next_page_link = None
                    has_valid_date_in_page = False  # 本页是否有符合时间的数据

                    # 3. 处理数据
                    for item in items:
                        # 提取下一页 (仅 CSS 模式)
                        if not is_number_pagination and config.next_page_selector and item.get(
                                'next_page') and not next_page_link:
                            next_page_link = item.get('next_page')

                        if not item.get('title') or not item.get('url'): continue

                        full_url = item['url']
                        if not full_url.startswith('http'):
                            from urllib.parse import urljoin
                            full_url = urljoin(current_url, full_url)

                        pub_date = parse_date_smart(item.get('date'), config.date_format)

                        # 宽松过滤：如果有日期且太旧则跳过；无日期则保留
                        if pub_date:
                            if pub_date < cutoff_date:
                                continue
                            else:
                                has_valid_date_in_page = True
                        else:
                            has_valid_date_in_page = True  # 无日期也算有效，防止漏抓

                        exists = session.exec(select(Article).where(Article.url == full_url)).first()
                        if exists:
                            site_stat["dup"] += 1
                            stats["duplicates"] += 1
                        else:
                            article = Article(
                                site_id=config.id,
                                title=item['title'],
                                url=full_url,
                                publish_date=pub_date
                            )
                            session.add(article)
                            site_stat["new"] += 1
                            stats["new_added"] += 1

                        stats["total_crawled"] += 1

                    session.commit()
                    page_num += 1

                    # 4. 翻页判断
                    if is_number_pagination:
                        # 数字模式：如果本页完全没有符合日期的数据，可能后面更旧了，可以选择提前停止
                        # 但为了保险，我们只依赖 max_pages 限制，或者如果提取到的 items 为空则停止
                        pass
                    else:
                        # CSS 模式：如果没有下一页链接，停止
                        if next_page_link:
                            if not next_page_link.startswith('http'):
                                from urllib.parse import urljoin
                                next_page_link = urljoin(current_url, next_page_link)
                            current_url = next_page_link
                        else:
                            print("   🏁 无下一页，停止")
                            break

                stats["details"].append(site_stat)

    return stats


# === AI 分析 ===
async def analyze_specific_articles(article_ids: list):
    results = []
    with Session(engine) as session:
        settings = session.exec(select(GlobalSettings)).first()
        if not settings: return 0

        comps = json.loads(settings.competitors_json)
        comp_str = f"CN: {', '.join(comps.get('中文名', []))}; EN: {', '.join(comps.get('英文名', []))}"
        topics_str = ", ".join(json.loads(settings.topics_json))
        categories_dict = json.loads(settings.categories_json)
        categories_str = "\n".join([f"- {k}: {v}" for k, v in categories_dict.items()])
        current_date = datetime.now().strftime("%Y-%m-%d")

        articles = session.exec(select(Article).where(Article.id.in_(article_ids))).all()
        if not articles: return 0

        browser_config = BrowserConfig(headless=False, user_agent_mode="random")

        async with AsyncWebCrawler(config=browser_config) as crawler:
            for article in articles:
                target_url = ensure_http(article.url)
                print(f"[AI] 分析: {article.title}")

                result = await crawler.arun(url=target_url, cache_mode=CacheMode.BYPASS, magic=True)

                if result.success:
                    article.content_raw = result.markdown
                    content_snippet = result.markdown[:6000]

                    prompt = f"""
                    你是情报分析师。今天是 {current_date}。
                    【客户画像】{settings.client_profile}
                    【重点关注竞争对手】{comp_str}
                    【分类标准】议题: {topics_str}
                    【新闻内容】{content_snippet}
                    请返回 JSON：{{
                        "议题": "...", "类别": "...", "摘要": "...", 
                        "中文标题": "...", "英文标题": "...",
                        "评分": <0-10>, "打分理由": "...",
                        "评分详情": {{ "战略": 0, "行业": 0, "时效": 0, "风险": 0, "落地": 0 }}
                    }}
                    """
                    try:
                        response = AI_CLIENT.chat.completions.create(
                            model="deepseek-chat",
                            messages=[{"role": "user", "content": prompt}],
                            response_format={"type": "json_object"}
                        )
                        ai_data = json.loads(response.choices[0].message.content)
                        article.ai_topic = ai_data.get("议题")
                        article.ai_category = ai_data.get("类别")
                        article.ai_summary = ai_data.get("摘要")
                        article.new_title = ai_data.get("中文标题")
                        article.title_en = ai_data.get("英文标题")
                        article.ai_score = ai_data.get("评分")
                        article.ai_reasoning = ai_data.get("打分理由")
                        article.ai_score_details = json.dumps(ai_data.get("评分详情", {}), ensure_ascii=False)
                        article.ai_status = "done"
                    except Exception as e:
                        print(f"[ERROR] AI: {e}")
                        article.ai_status = "error"
                else:
                    article.ai_status = "error"
                session.add(article)
                session.commit()
                results.append(article)
    return len(results)


# === AI 自动探测 ===
async def auto_detect_config(url: str):
    target_url = ensure_http(url)
    print(f"[DETECT] 探测: {target_url}")
    browser_config = BrowserConfig(headless=True)

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=target_url, magic=True, cache_mode=CacheMode.BYPASS)
        if not result.success: return {"error": result.error_message}
        html = result.html

    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style']): tag.decompose()
    clean_html = str(soup.body)[:30000]

    prompt = f"""
    分析 HTML 找出新闻列表 CSS 选择器。
    特别任务：观察 HTML 里的日期格式。
    返回 JSON: list, title, link, date, date_format, next_page.
    HTML: {clean_html}
    """
    try:
        response = AI_CLIENT.chat.completions.create(model="deepseek-chat",
                                                     messages=[{"role": "user", "content": prompt}],
                                                     response_format={"type": "json_object"})
        result_data = json.loads(response.choices[0].message.content)
        # 确保返回的URL包含正确的协议前缀
        result_data["url"] = ensure_http(result_data.get("url", url))
        return result_data
    except Exception as e:
        return {"error": str(e)}


async def test_crawler_config(url, selectors):
    target_url = ensure_http(url)
    print(f"[TEST] 测试: {target_url}")
    browser_config = BrowserConfig(headless=False, verbose=True)
    fields = [{"name": "title", "selector": selectors['title'], "type": "text"},
              {"name": "url", "selector": selectors['link'], "type": "attribute", "attribute": "href"}]
    if selectors.get('date'): fields.append({"name": "date", "selector": selectors['date'], "type": "text"})
    schema = {"baseSelector": selectors['list'], "fields": fields}
    run_config = CrawlerRunConfig(extraction_strategy=JsonCssExtractionStrategy(schema), cache_mode=CacheMode.BYPASS,
                                  js_code="window.scrollTo(0, document.body.scrollHeight);", wait_for="body")
    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=target_url, config=run_config)
            if not result.success: return {"success": False, "error": result.error_message}
            items = json.loads(result.extracted_content)
            return {"success": True, "count": len(items), "data": items[:3]}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def test_pagination_logic(url: str, selectors: dict):
    """
    尝试抓取前 2 页，验证分页配置是否正确
    """
    base_url = ensure_http(url)
    print(f"[TEST PAGINATION] 开始测试: {base_url}")

    # 判断模式
    is_number_pagination = "{n}" in base_url

    report = {
        "mode": "数字分页 {n}" if is_number_pagination else "CSS 按钮翻页",
        "pages": []
    }

    browser_config = BrowserConfig(headless=False, verbose=True)

    # 构建 schema
    fields = [
        {"name": "title", "selector": selectors['title'], "type": "text"}
    ]
    if selectors.get('next_page') and not is_number_pagination:
        fields.append(
            {"name": "next_page", "selector": selectors['next_page'], "type": "attribute", "attribute": "href"})

    schema = {"baseSelector": selectors['list'], "fields": fields}

    run_config = CrawlerRunConfig(
        extraction_strategy=JsonCssExtractionStrategy(schema),
        cache_mode=CacheMode.BYPASS,
        js_code="window.scrollTo(0, document.body.scrollHeight);",
        wait_for="body"
    )

    current_url = base_url
    page_num = 1

    async with AsyncWebCrawler(config=browser_config) as crawler:
        # 只测前 2 页
        while page_num <= 2:
            # 1. 计算 URL
            if is_number_pagination:
                target_url = base_url.replace("{n}", str(page_num))
            else:
                target_url = current_url

            if not target_url:
                report["pages"].append(f"第 {page_num} 页: 无法获取 URL，停止。")
                break

            print(f"   Testing Page {page_num}: {target_url}")

            # 2. 抓取
            result = await crawler.arun(url=target_url, config=run_config)

            if not result.success:
                report["pages"].append(f"第 {page_num} 页: 抓取失败 ({result.error_message})")
                break

            # 3. 分析结果
            try:
                items = json.loads(result.extracted_content)
            except:
                items = []

            item_count = len(items)
            first_title = items[0]['title'] if items and items[0].get('title') else "无标题"

            page_info = {
                "page": page_num,
                "url": target_url,
                "status": "Success",
                "item_count": item_count,
                "first_item": first_title
            }

            # 4. 寻找下一页 (仅 CSS 模式)
            if not is_number_pagination:
                next_link = None
                for item in items:
                    if item.get('next_page'):
                        next_link = item.get('next_page')
                        break

                if next_link:
                    page_info["next_button_found"] = "✅ 找到下一页链接"
                    page_info["next_url_raw"] = next_link
                    # 补全 URL
                    if not next_link.startswith('http'):
                        from urllib.parse import urljoin
                        next_link = urljoin(target_url, next_link)
                    current_url = next_link
                else:
                    page_info["next_button_found"] = "❌ 未找到下一页链接 (Selector失效或无更多页)"
                    current_url = None  # 停止

            report["pages"].append(page_info)

            # 如果 CSS 模式没找到下一页，就不测第 2 页了
            if not is_number_pagination and not current_url:
                break

            page_num += 1

    return report
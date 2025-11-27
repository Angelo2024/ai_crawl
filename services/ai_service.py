# services/ai_service.py
import os
import json
import re
import logging
import aiohttp
import asyncio
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from datetime import datetime
import dateutil.parser
from datetime import timedelta

# 确保加载环境变量
load_dotenv()


class NewsArticle(BaseModel):
    source: str
    title: str
    url: str
    publish_date: str
    content: str
    content_hash: str


class ContentFetchResult(BaseModel):
    success: bool
    content: str
    error_message: Optional[str] = None
    source: str  # "cached" or "fetched" or "fallback"


class DateExtractionResult(BaseModel):
    """日期提取结果"""
    success: bool
    date: Optional[str] = None
    confidence: float = 0.0  # 0-1之间，表示提取的可信度
    method: str = ""  # 提取方法："meta_tag", "json_ld", "text_pattern", "url_pattern"
    error_message: Optional[str] = None


class MockConfig:
    OFFICIAL_TOPICS: list = ["健康与安全", "清洁技术机遇", "绿色建筑", "应对气候变化", "生物多样性", "其他"]
    OFFICIAL_CATEGORIES: dict = {
        "政策动态": "指由国家部门发布的、与议题相关的产业政策或声明...",
        "前沿资讯": "包括议题下的技术与创新动态、市场与竞争动态...",
        "必读报告": "只要是发布报告就是这个分类"
    }
    DEFAULT_CLIENT_PROFILE: str = (
        "中国建筑国际控股有限公司（CSCI）是一家在香港上市的大型建筑及基础设施综合企业，隶属于建筑与工程行业。"
        "公司业务主要分为五大板块："
        "**建筑相关投资项目（51.1%，收入588.4亿港元）**：公司最大收入来源，以投资者身份参与城市更新、基础设施和房地产项目的前期投资与开发，"
        "通过EPC（设计-采购-施工）模式回收投资，主要包括城市更新改造、保障房建设、产业园区开发等，典型项目如深圳前海片区城市更新项目；"
        "**建筑合约工程（41.3%，收入475.3亿港元）**：传统核心业务，承接政府、企业或私人开发商的建筑工程项目，"
        "涵盖房建工程（住宅、写字楼、商业综合体）、公共工程（学校、医院、政府建筑）、基础设施工程（道路、隧道、桥梁、轨道交通），"
        "典型项目如香港将军澳日出康城住宅项目；"
        "**外墙/立面业务（3.4%，收入39.4亿港元）**：专业化程度较高的细分业务，从事建筑幕墙、立面、玻璃幕墙和金属装饰工程的设计制造安装，"
        "包括高层建筑玻璃幕墙、建筑立面装饰、绿色节能幕墙等；"
        "**基础设施营运（0.63%，收入7.2亿港元）**：投资并运营已建成基础设施，通过收费获得长期稳定现金流，"
        "包括收费道路隧道桥梁运营、公共停车场运营、环保设施水务设施运营等；"
        "**其他业务（3.5%，收入40.7亿港元）**：包括项目咨询、工程管理、造价咨询、厂房重建改造、建筑材料销售、机械设备租赁、投资物业租赁等辅助性业务。"
        "从地域分布看，公司业务主要集中在大中华地区：中国大陆占54.8%（604.2亿港元），香港占37.3%（410.9亿港元），澳门占8.0%（87.8亿港元）。"
        "公司依托在城市更新、基础设施建设、高端建筑等领域的丰富经验，以及数字化建造技术和跨区域管理能力，"
        "持续在绿色建筑、智慧城市、城市更新等前沿领域深化布局。"
    )
    CONTENT_MAX_LENGTH: int = 8000
    FETCH_TIMEOUT: int = 30  # 网页抓取超时时间
    USE_URL_FETCH: bool = True  # 是否启用URL抓取
    ENABLE_DATE_EXTRACTION: bool = True  # 是否启用深度日期提取


config = MockConfig()

# 初始化日志记录器
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 初始化AI客户端
try:
    from openai import AsyncOpenAI

    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

    if not DEEPSEEK_API_KEY:
        logger.error("❌ 未设置 DEEPSEEK_API_KEY 环境变量")
        print("❌ DEEPSEEK_API_KEY 未找到，请检查 .env 文件")
        AI_CLIENT = None
    else:
        AI_CLIENT = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com/v1"
        )
        logger.info(f"✅ DeepSeek AI 客户端初始化成功，密钥: {DEEPSEEK_API_KEY[:10]}...")
        print(f"✅ AI服务就绪，密钥: {DEEPSEEK_API_KEY[:10]}...")

except ImportError:
    logger.error("openai 库未安装。请运行 'pip install openai'。")
    AI_CLIENT = None


async def fetch_webpage_content(url: str) -> ContentFetchResult:
    """
    异步抓取网页内容并清理
    """
    try:
        timeout = aiohttp.ClientTimeout(total=config.FETCH_TIMEOUT)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return ContentFetchResult(
                        success=False,
                        content="",
                        error_message=f"HTTP {response.status}",
                        source="fetched"
                    )

                html_content = await response.text()

                # 使用BeautifulSoup清理HTML
                soup = BeautifulSoup(html_content, 'html.parser')

                # 移除不需要的标签
                for tag in soup(["script", "style", "nav", "header", "footer", "aside", "advertisement"]):
                    tag.decompose()

                # 提取主要内容
                # 优先查找常见的内容容器
                content_selectors = [
                    'article',
                    '.content',
                    '.article-content',
                    '.post-content',
                    '.entry-content',
                    'main',
                    '#content'
                ]

                extracted_content = ""
                for selector in content_selectors:
                    elements = soup.select(selector)
                    if elements:
                        extracted_content = elements[0].get_text(strip=True)
                        break

                # 如果没找到特定容器，使用body内容
                if not extracted_content:
                    body = soup.find('body')
                    if body:
                        extracted_content = body.get_text(strip=True)

                # 清理文本
                lines = extracted_content.split('\n')
                cleaned_lines = [line.strip() for line in lines if line.strip()]
                cleaned_content = '\n'.join(cleaned_lines)

                return ContentFetchResult(
                    success=True,
                    content=cleaned_content[:config.CONTENT_MAX_LENGTH],
                    source="fetched"
                )

    except asyncio.TimeoutError:
        return ContentFetchResult(
            success=False,
            content="",
            error_message="请求超时",
            source="fetched"
        )
    except Exception as e:
        return ContentFetchResult(
            success=False,
            content="",
            error_message=str(e),
            source="fetched"
        )


async def extract_date_from_url(url: str, date_selectors: List[str] = None) -> DateExtractionResult:
    """
    从具体URL中提取日期信息

    Args:
        url: 要提取日期的URL
        date_selectors: 可选的日期选择器列表，用于定向查找

    Returns:
        DateExtractionResult: 日期提取结果
    """
    logger.info(f"🕐 开始从URL提取日期: {url}")

    try:
        timeout = aiohttp.ClientTimeout(total=config.FETCH_TIMEOUT)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    return DateExtractionResult(
                        success=False,
                        error_message=f"HTTP {response.status}"
                    )

                html_content = await response.text()
                soup = BeautifulSoup(html_content, 'html.parser')

                # 方法1: 优先使用配置的日期选择器
                if date_selectors:
                    for selector in date_selectors:
                        try:
                            elements = soup.select(selector.strip())
                            for element in elements:
                                date_text = element.get_text(strip=True)
                                if date_text:
                                    parsed_date = parse_date_text(date_text)
                                    if parsed_date:
                                        logger.info(f"✅ 通过配置选择器提取到日期: {parsed_date}")
                                        return DateExtractionResult(
                                            success=True,
                                            date=parsed_date,
                                            confidence=0.9,
                                            method=f"configured_selector: {selector}"
                                        )
                        except Exception as e:
                            logger.warning(f"选择器 {selector} 执行失败: {e}")
                            continue

                # 方法2: 尝试从meta标签提取
                meta_result = extract_date_from_meta_tags(soup)
                if meta_result.success:
                    logger.info(f"✅ 从Meta标签提取到日期: {meta_result.date}")
                    return meta_result

                # 方法3: 尝试从JSON-LD结构化数据提取
                jsonld_result = extract_date_from_jsonld(soup)
                if jsonld_result.success:
                    logger.info(f"✅ 从JSON-LD提取到日期: {jsonld_result.date}")
                    return jsonld_result

                # 方法4: 通过常见的时间标签和类名提取
                common_result = extract_date_from_common_patterns(soup)
                if common_result.success:
                    logger.info(f"✅ 从常见模式提取到日期: {common_result.date}")
                    return common_result

                # 方法5: 尝试从URL路径中提取日期
                url_result = extract_date_from_url_path(url)
                if url_result.success:
                    logger.info(f"✅ 从URL路径提取到日期: {url_result.date}")
                    return url_result

                # 方法6: 从页面文本中通过模式匹配提取
                text_result = extract_date_from_text_patterns(soup.get_text())
                if text_result.success:
                    logger.info(f"✅ 从文本模式提取到日期: {text_result.date}")
                    return text_result

                logger.warning(f"⚠️ 未能从URL提取到日期: {url}")
                return DateExtractionResult(
                    success=False,
                    error_message="未找到可识别的日期信息"
                )

    except asyncio.TimeoutError:
        return DateExtractionResult(
            success=False,
            error_message="请求超时"
        )
    except Exception as e:
        logger.error(f"❌ 日期提取出错: {e}")
        return DateExtractionResult(
            success=False,
            error_message=str(e)
        )


def extract_date_from_meta_tags(soup: BeautifulSoup) -> DateExtractionResult:
    """从meta标签提取日期"""
    meta_selectors = [
        'meta[property="article:published_time"]',
        'meta[property="article:modified_time"]',
        'meta[name="publishdate"]',
        'meta[name="publication_date"]',
        'meta[name="date"]',
        'meta[name="DC.Date"]',
        'meta[name="pubdate"]',
        'meta[itemprop="datePublished"]',
        'meta[itemprop="dateCreated"]'
    ]

    for selector in meta_selectors:
        meta_tag = soup.select_one(selector)
        if meta_tag:
            content = meta_tag.get('content')
            if content:
                parsed_date = parse_date_text(content)
                if parsed_date:
                    return DateExtractionResult(
                        success=True,
                        date=parsed_date,
                        confidence=0.95,
                        method=f"meta_tag: {selector}"
                    )

    return DateExtractionResult(success=False, method="meta_tag")


def extract_date_from_jsonld(soup: BeautifulSoup) -> DateExtractionResult:
    """从JSON-LD结构化数据提取日期"""
    json_scripts = soup.find_all('script', type='application/ld+json')

    for script in json_scripts:
        try:
            data = json.loads(script.string)

            # 处理数组格式的JSON-LD
            if isinstance(data, list):
                data = data[0] if data else {}

            # 查找日期字段
            date_fields = [
                'datePublished', 'dateCreated', 'dateModified',
                'publishedDate', 'createdDate', 'modifiedDate'
            ]

            for field in date_fields:
                if field in data:
                    date_value = data[field]
                    parsed_date = parse_date_text(str(date_value))
                    if parsed_date:
                        return DateExtractionResult(
                            success=True,
                            date=parsed_date,
                            confidence=0.9,
                            method=f"json_ld: {field}"
                        )

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            continue

    return DateExtractionResult(success=False, method="json_ld")


def extract_date_from_common_patterns(soup: BeautifulSoup) -> DateExtractionResult:
    """从常见的HTML模式提取日期"""
    # 常见的时间标签和属性
    time_selectors = [
        'time[datetime]',
        'time[pubdate]',
        '.publish-date',
        '.publication-date',
        '.article-date',
        '.post-date',
        '.date-published',
        '.entry-date',
        '.news-date',
        '[class*="date"]',
        '[id*="date"]'
    ]

    for selector in time_selectors:
        try:
            elements = soup.select(selector)
            for element in elements:
                # 优先检查datetime属性
                datetime_attr = element.get('datetime')
                if datetime_attr:
                    parsed_date = parse_date_text(datetime_attr)
                    if parsed_date:
                        return DateExtractionResult(
                            success=True,
                            date=parsed_date,
                            confidence=0.85,
                            method=f"common_pattern_attr: {selector}"
                        )

                # 然后检查文本内容
                text_content = element.get_text(strip=True)
                if text_content:
                    parsed_date = parse_date_text(text_content)
                    if parsed_date:
                        return DateExtractionResult(
                            success=True,
                            date=parsed_date,
                            confidence=0.75,
                            method=f"common_pattern_text: {selector}"
                        )
        except Exception:
            continue

    return DateExtractionResult(success=False, method="common_patterns")


def extract_date_from_url_path(url: str) -> DateExtractionResult:
    """从URL路径中提取日期"""
    try:
        # 匹配URL中的日期模式
        date_patterns = [
            r'/(\d{4})/(\d{1,2})/(\d{1,2})/',  # /2024/01/15/
            r'/(\d{4})-(\d{1,2})-(\d{1,2})/',  # /2024-01-15/
            r'/(\d{4})(\d{2})(\d{2})/',  # /20240115/
            r'[?&]date=(\d{4}-\d{1,2}-\d{1,2})',  # ?date=2024-01-15
            r'[?&]year=(\d{4})',  # ?year=2024
        ]

        for pattern in date_patterns:
            match = re.search(pattern, url)
            if match:
                groups = match.groups()
                if len(groups) == 3:  # 年月日
                    year, month, day = groups
                    try:
                        date_obj = datetime(int(year), int(month), int(day))
                        return DateExtractionResult(
                            success=True,
                            date=date_obj.isoformat(),
                            confidence=0.8,
                            method=f"url_path: {pattern}"
                        )
                    except ValueError:
                        continue
                elif len(groups) == 1:  # 只有年份
                    year = groups[0]
                    if year.isdigit() and 2000 <= int(year) <= 2030:
                        # 使用年初作为默认日期
                        date_obj = datetime(int(year), 1, 1)
                        return DateExtractionResult(
                            success=True,
                            date=date_obj.isoformat(),
                            confidence=0.6,
                            method=f"url_path_year: {pattern}"
                        )

    except Exception as e:
        logger.warning(f"URL日期提取失败: {e}")

    return DateExtractionResult(success=False, method="url_path")


def extract_date_from_text_patterns(text: str) -> DateExtractionResult:
    """从页面文本中通过模式匹配提取日期"""
    # 限制文本长度，避免处理过长内容
    text = text[:5000]

    # 日期文本模式（中英文）
    date_patterns = [
        # ISO格式
        r'(\d{4}-\d{1,2}-\d{1,2})',
        r'(\d{4}/\d{1,2}/\d{1,2})',

        # 中文格式
        r'(\d{4})年(\d{1,2})月(\d{1,2})日',
        r'(\d{4})年(\d{1,2})月',

        # 英文格式
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})',
        r'(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})',

        # 相对时间
        r'(\d+)\s*days?\s*ago',
        r'(\d+)\s*hours?\s*ago',
        r'yesterday',
        r'today'
    ]

    for pattern in date_patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            matched_text = match.group(0)
            parsed_date = parse_date_text(matched_text)
            if parsed_date:
                return DateExtractionResult(
                    success=True,
                    date=parsed_date,
                    confidence=0.7,
                    method=f"text_pattern: {pattern}"
                )

    return DateExtractionResult(success=False, method="text_pattern")


def parse_date_text(date_text: str) -> Optional[str]:
    """
    统一的日期解析函数，尝试多种解析方法

    Args:
        date_text: 要解析的日期文本

    Returns:
        解析成功返回ISO格式日期字符串，失败返回None
    """
    if not date_text or not date_text.strip():
        return None

    date_text = date_text.strip()

    try:
        # 方法1: 使用dateutil的智能解析
        parsed = dateutil.parser.parse(date_text, fuzzy=True)

        # 验证日期合理性（1990-2030年之间）
        if 1990 <= parsed.year <= 2030:
            return parsed.isoformat()

    except (ValueError, TypeError, OverflowError):
        pass

    try:
        # 方法2: 处理中文日期格式
        chinese_match = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', date_text)
        if chinese_match:
            year, month, day = chinese_match.groups()
            parsed = datetime(int(year), int(month), int(day))
            return parsed.isoformat()

        chinese_match = re.match(r'(\d{4})年(\d{1,2})月', date_text)
        if chinese_match:
            year, month = chinese_match.groups()
            parsed = datetime(int(year), int(month), 1)
            return parsed.isoformat()
    except (ValueError, TypeError):
        pass

    try:
        # 方法3: 处理相对时间
        if 'ago' in date_text.lower():
            days_match = re.search(r'(\d+)\s*days?\s*ago', date_text, re.IGNORECASE)
            if days_match:
                days_ago = int(days_match.group(1))
                if days_ago <= 365:  # 最多一年前
                    date_obj = datetime.now() - timedelta(days=days_ago)
                    return date_obj.isoformat()

            hours_match = re.search(r'(\d+)\s*hours?\s*ago', date_text, re.IGNORECASE)
            if hours_match:
                hours_ago = int(hours_match.group(1))
                if hours_ago <= 24 * 7:  # 最多一周前
                    date_obj = datetime.now() - timedelta(hours=hours_ago)
                    return date_obj.isoformat()

        if date_text.lower() in ['yesterday', '昨天']:
            date_obj = datetime.now() - timedelta(days=1)
            return date_obj.isoformat()

        if date_text.lower() in ['today', '今天']:
            date_obj = datetime.now()
            return date_obj.isoformat()

    except (ValueError, TypeError):
        pass

    return None


async def get_article_content_with_date_extraction(article: NewsArticle, date_selectors: List[str] = None) -> tuple[
    ContentFetchResult, Optional[DateExtractionResult]]:
    """
    获取文章内容并尝试提取日期

    Returns:
        tuple: (content_result, date_result)
    """
    # 首先获取内容
    content_result = await get_article_content(article)

    # 如果文章已经有日期且不启用日期提取，则跳过
    if not config.ENABLE_DATE_EXTRACTION or (article.publish_date and article.publish_date.strip()):
        return content_result, None

    # 尝试从URL提取日期
    date_result = await extract_date_from_url(article.url, date_selectors)

    return content_result, date_result


async def get_article_content(article: NewsArticle) -> ContentFetchResult:
    """
    获取文章内容，优先使用URL抓取，失败时回退到缓存内容
    """
    # 如果禁用URL抓取，直接使用缓存内容
    if not config.USE_URL_FETCH:
        return ContentFetchResult(
            success=True,
            content=article.content[:config.CONTENT_MAX_LENGTH],
            source="cached"
        )

    # 尝试抓取URL内容
    logger.info(f"正在抓取URL内容: {article.url}")
    fetch_result = await fetch_webpage_content(article.url)

    if fetch_result.success and len(fetch_result.content.strip()) > 100:
        logger.info(f"✅ 成功抓取URL内容，长度: {len(fetch_result.content)}")
        return fetch_result
    else:
        # 抓取失败，使用缓存内容作为回退
        logger.warning(f"⚠️ URL抓取失败 ({fetch_result.error_message})，使用缓存内容作为回退")
        return ContentFetchResult(
            success=True,
            content=article.content[:config.CONTENT_MAX_LENGTH],
            error_message=fetch_result.error_message,
            source="fallback"
        )


# services/ai_service.py - 修改评分维度部分

async def analyze_article_with_deepseek(
        article: NewsArticle,
        client_profile: Optional[str] = None,
        date_selectors: List[str] = None
) -> Dict[str, Any]:
    """
    使用DeepSeek API对单篇文章进行全面的AI分析，支持日期提取
    """
    if not AI_CLIENT:
        raise ConnectionError("AI 客户端未初始化，无法执行分析。")

    # 获取文章内容和日期（如果需要）
    content_result, date_result = await get_article_content_with_date_extraction(
        article, date_selectors
    )

    content = content_result.content
    content_source = content_result.source

    # 如果成功提取到日期，更新文章对象
    extracted_date = None
    if date_result and date_result.success:
        extracted_date = date_result.date
        # 更新article对象的日期（用于后续数据库更新）
        article.publish_date = extracted_date
        logger.info(f"📅 成功从URL提取日期: {extracted_date} (方法: {date_result.method})")

    # 获取当前日期
    from datetime import datetime, timezone
    current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 构建提示词
    topics_str = "、".join(config.OFFICIAL_TOPICS)
    categories_str = "\n".join([f"  - **{k}**: {v}" for k, v in config.OFFICIAL_CATEGORIES.items()])
    profile_to_use = client_profile or config.DEFAULT_CLIENT_PROFILE

    # 根据内容来源调整提示词
    content_note = ""
    if content_source == "fetched":
        content_note = "（内容来源：实时抓取的最新网页内容）"
    elif content_source == "fallback":
        content_note = f"（内容来源：缓存内容，URL抓取失败：{content_result.error_message}）"
    else:
        content_note = "（内容来源：缓存内容）"

    # 日期信息提示
    date_note = ""
    if extracted_date:
        date_note = f"（日期信息：从URL提取到发布日期 {extracted_date}）"
    elif article.publish_date:
        date_note = f"（日期信息：已有发布日期 {article.publish_date}）"
    else:
        date_note = "（日期信息：未找到发布日期）"

    prompt = f"""你是为特定企业客户服务的首席商业情报分析师。

### 当前日期 ###
今天是：{current_date}

### 客户画像 (Client Profile) ###
{profile_to_use}

### 你的核心任务 ###
基于上述**客户画像**，筛选和评估那些**可能对该客户业务产生重大影响**的ESG情报。你必须逻辑严谨、客观，并以该客户的视角为中心。

### 分析规则 ###
1. **判定时效性**: 根据情报的发布日期和当前日期({current_date})，严格判断其时效性，如果文章里有说明新闻发生的具体时间，请按照那个时间评价时效性。
2. **评估可靠性**: 基于情报的来源，评估其可信度。
3. **衡量业务影响**: 这是最重要的维度。深入分析情报内容，判断其对客户业务决策的潜在影响。
4. **生成摘要**: 生成一段不超过200字的、客观的情报摘要，不会因为客户业务而改变。
5. **生成新标题**: 基于文章内容生成一个更准确、简洁的新标题来替换原标题。
6. **严格格式化**: 必须以指定的JSON格式输出，不包含任何额外说明。

### 评分维度定义 ###
- **战略相关性 (权重30%)**: 情报与客户战略目标、重点市场、核心产品线的匹配度
  - 1-3分: 基本无关或次要关联
  - 4-6分: 与客户某个业务板块有中等相关性
  - 7-10分: 直接涉及客户核心战略或主营业务
- **行业影响力 (权重20%)**: 事件对行业格局的潜在影响
  - 1-3分: 影响极小，仅为行业背景信息
  - 4-6分: 行业内值得关注的事件
  - 7-10分: 可能改变行业格局或发展趋势的重大事件
- **时效性紧迫性 (权重20%)**: 情报的时效价值和紧迫性
  - 计算方法：将发布日期与当前日期({current_date})对比
  - 如果发布日期在过去7天内：得8-10分
  - 如果发布日期在过去30天内：得5-7分
  - 如果发布日期超过30天：得1-4分
  - 如果是未来的重要时间给8-10分
- **业务机会风险强度 (权重15%)**: 该情报对客户可能带来的机会或风险强弱
  - 1-3分: 几乎无实际商业影响
  - 4-6分: 存在中等程度的机会或威胁
  - 7-10分: 可能显著改变客户收益、成本或市场地位
- **可操作性 (权重15%)**: 情报能否转化为明确的行动建议
  - 1-3分: 纯背景信息，无明确行动指向
  - 4-6分: 有一定启发价值，可作为决策参考
  - 7-10分: 可直接落地执行或制定具体应对方案

### 官方列表 ###
官方议题: {topics_str}
议题说明：
官方类别:
{categories_str}

### 待分析的情报原文 ###
发布日期: {article.publish_date}
标题: {article.title}
来源URL: {article.url}
内容 {content_note}{date_note}: {content}

请严格按照以下JSON格式输出分析结果：
{{
    "议题": "<从官方列表中选择最贴切的议题>",
    "类别": "<从官方列表中选择最贴切的类别>",
    "摘要": "<情报摘要，客观即可，无需面对客户评价，150-200字，格式需要按照XX于（时间，具体到日）做了XX>",
    "新标题": "<根据文章内容生成更准确简洁的新标题，不超过50字>",
    "评分详情": {{
        "战略相关性": {{ "分数": <0-10整数>, "理由": "<打分理由>" }},
        "行业影响力": {{ "分数": <0-10整数>, "理由": "<打分理由>" }},
        "时效性紧迫性": {{ "分数": <0-10整数>, "理由": "<基于当前日期{current_date}的时效性判断理由>" }},
        "业务机会风险强度": {{ "分数": <0-10整数>, "理由": "<打分理由>" }},
        "可操作性": {{ "分数": <0-10整数>, "理由": "<打分理由>" }}
    }}
}}"""

    try:
        response = await AI_CLIENT.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2000,
            response_format={"type": "json_object"}
        )

        output_text = response.choices[0].message.content
        logger.info(f"DeepSeek API 原始返回: {output_text[:200]}...")

        analysis_results = _parse_ai_output(output_text)

        if _validate_analysis_result(analysis_results):
            # 添加元数据信息
            meta_info = {
                "content_source": content_source,
                "content_length": len(content),
                "fetch_error": content_result.error_message if content_result.error_message else None,
                "analysis_date": current_date
            }

            # 如果提取到了日期，添加日期提取信息
            if date_result:
                meta_info.update({
                    "date_extracted": date_result.success,
                    "extracted_date": date_result.date if date_result.success else None,
                    "date_extraction_method": date_result.method if date_result.success else None,
                    "date_confidence": date_result.confidence if date_result.success else None
                })

            analysis_results["_meta"] = meta_info
            logger.info(f"AI成功分析文章: {article.title[:30]}... (内容来源: {content_source})")
            return analysis_results
        else:
            logger.error(f"AI返回了无效的分析结果: {article.title[:30]}...")
            # 返回默认结果而不是抛出异常
            return {
                "议题": config.OFFICIAL_TOPICS[0],
                "类别": list(config.OFFICIAL_CATEGORIES.keys())[0],
                "摘要": article.title,
                "新标题": article.title[:50],
                "评分详情": {
                    "战略相关性": {"分数": 0, "理由": "默认评分"},
                    "行业影响力": {"分数": 0, "理由": "默认评分"},
                    "时效性紧迫性": {"分数": 0, "理由": "默认评分"},
                    "业务机会风险强度": {"分数": 0, "理由": "默认评分"},
                    "可操作性": {"分数": 0, "理由": "默认评分"}
                },
                "_meta": {
                    "content_source": content_source,
                    "content_length": len(content),
                    "fetch_error": "AI分析结果验证失败",
                    "analysis_date": current_date,
                    "date_extracted": date_result.success if date_result else False,
                    "extracted_date": date_result.date if date_result and date_result.success else None
                }
            }

    except Exception as e:
        logger.error(f"调用DeepSeek API时发生错误: {e}", exc_info=True)
        # 返回默认结果而不是抛出异常，确保系统稳定性
        return {
            "议题": config.OFFICIAL_TOPICS[0],
            "类别": list(config.OFFICIAL_CATEGORIES.keys())[0],
            "摘要": f"分析失败: {article.title}",
            "新标题": article.title[:50],
            "评分详情": {
                "战略相关性": {"分数": 0, "理由": f"API调用失败: {str(e)[:50]}"},
                "行业影响力": {"分数": 0, "理由": "API调用失败"},
                "时效性紧迫性": {"分数": 0, "理由": "API调用失败"},
                "业务机会风险强度": {"分数": 0, "理由": "API调用失败"},
                "可操作性": {"分数": 0, "理由": "API调用失败"}
            },
            "_meta": {
                "content_source": content_source,
                "content_length": len(content) if 'content' in locals() else 0,
                "fetch_error": f"API调用失败: {str(e)}",
                "analysis_date": current_date,
                "date_extracted": False,
                "extracted_date": None
            }
        }


def _validate_analysis_result(result: Dict) -> bool:
    """
    验证AI返回的结果是否符合我们的规范。
    """
    if not result:
        return False

    required_keys = ["议题", "类别", "摘要", "新标题", "评分详情"]
    if not all(k in result for k in required_keys):
        logger.warning(f"AI分析结果缺少必要字段: {[k for k in required_keys if k not in result]}")
        return False

    if result.get("议题") not in config.OFFICIAL_TOPICS:
        logger.warning(f"AI返回了未知的议题: {result.get('议题')}")
        return False

    if result.get("类别") not in config.OFFICIAL_CATEGORIES:
        logger.warning(f"AI返回了未知的类别: {result.get('类别')}")
        return False

    score_details = result.get("评分详情")
    if not isinstance(score_details, dict) or not score_details:
        logger.warning("AI返回的评分详情格式不正确。")
        return False

    # 验证评分详情的结构
    required_score_keys = ["战略相关性", "行业影响力", "时效性紧迫性", "业务机会风险强度", "可操作性"]
    for score_key in required_score_keys:
        if score_key not in score_details:
            logger.warning(f"评分详情中缺少 {score_key}")
            return False
        score_item = score_details[score_key]
        if not isinstance(score_item, dict) or "分数" not in score_item:
            logger.warning(f"{score_key} 的评分格式不正确")
            return False

    return True


def _parse_ai_output(output_text: str) -> Dict[str, Any]:
    """
    从AI返回的文本中安全地解析出JSON对象。
    """
    try:
        # 先尝试直接解析JSON
        if output_text.strip().startswith('{'):
            return json.loads(output_text)

        # 再尝试从代码块中提取
        match = re.search(r"```json\s*([\s\S]+?)\s*```", output_text)
        if match:
            json_str = match.group(1)
        else:
            json_str = output_text

        # 查找JSON对象的开始和结束
        start = json_str.find('{')
        end = json_str.rfind('}') + 1

        if start != -1 and end != 0:
            return json.loads(json_str[start:end])
        return {}

    except (json.JSONDecodeError, IndexError, AttributeError) as e:
        logger.error(f"解析AI输出的JSON失败: {e}\n原始输出: {output_text[:300]}...")
        return {}


# 辅助函数：安全处理日期字段
def safe_get_publish_date(news_time) -> str:
    """
    安全地获取发布日期字符串
    """
    if hasattr(news_time, 'isoformat'):
        # datetime 对象
        return news_time.isoformat()
    elif isinstance(news_time, str):
        # 字符串
        return news_time
    else:
        # 其他类型或None
        return ""


# 使用示例和配置函数
def set_url_fetch_enabled(enabled: bool):
    """
    启用或禁用URL抓取功能
    """
    config.USE_URL_FETCH = enabled
    logger.info(f"URL抓取功能已{'启用' if enabled else '禁用'}")


def set_date_extraction_enabled(enabled: bool):
    """
    启用或禁用深度日期提取功能
    """
    config.ENABLE_DATE_EXTRACTION = enabled
    logger.info(f"深度日期提取功能已{'启用' if enabled else '禁用'}")


def set_fetch_timeout(timeout: int):
    """
    设置URL抓取超时时间
    """
    config.FETCH_TIMEOUT = timeout
    logger.info(f"URL抓取超时时间设置为 {timeout} 秒")


# 新增：批量日期补充功能
async def batch_extract_missing_dates(
        articles: List[Dict[str, Any]],
        date_selectors: List[str] = None
) -> List[Dict[str, Any]]:
    """
    批量为缺少日期的文章提取日期

    Args:
        articles: 文章列表，每个文章应包含 id, url, title 等字段
        date_selectors: 可选的日期选择器列表

    Returns:
        处理结果列表，包含成功提取的日期信息
    """
    logger.info(f"🕐 开始批量日期提取，共 {len(articles)} 篇文章")
    results = []

    # 过滤出没有日期的文章
    articles_without_date = [
        article for article in articles
        if not article.get('news_time') or not str(article.get('news_time')).strip()
    ]

    logger.info(f"📊 发现 {len(articles_without_date)} 篇文章缺少日期")

    for i, article in enumerate(articles_without_date, 1):
        try:
            logger.info(f"⏳ [{i}/{len(articles_without_date)}] 处理文章: {article.get('title', '无标题')[:50]}...")

            url = article.get('url')
            if not url:
                results.append({
                    'id': article.get('id'),
                    'success': False,
                    'error': 'URL为空'
                })
                continue

            # 提取日期
            date_result = await extract_date_from_url(url, date_selectors)

            if date_result.success:
                results.append({
                    'id': article.get('id'),
                    'success': True,
                    'extracted_date': date_result.date,
                    'method': date_result.method,
                    'confidence': date_result.confidence,
                    'url': url
                })
                logger.info(f"✅ 成功提取: {date_result.date} (置信度: {date_result.confidence:.2f})")
            else:
                results.append({
                    'id': article.get('id'),
                    'success': False,
                    'error': date_result.error_message,
                    'url': url
                })
                logger.warning(f"❌ 提取失败: {date_result.error_message}")

            # 避免过快请求
            if i < len(articles_without_date):
                await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"❌ 处理文章时出错: {e}")
            results.append({
                'id': article.get('id'),
                'success': False,
                'error': str(e),
                'url': article.get('url')
            })

    success_count = sum(1 for r in results if r['success'])
    logger.info(f"🎉 批量日期提取完成: 成功 {success_count}/{len(results)} 篇")

    return results
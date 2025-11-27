# -*- coding: utf-8 -*-
"""
增强版 Recipe Builder: 智能选择器生成和页面结构分析
"""

import re
from typing import Dict, List, Optional, Tuple, Any
from bs4 import BeautifulSoup, Tag
from html import unescape
import urllib.parse


def _clean_text(s: str) -> str:
    """清理文本内容"""
    return re.sub(r"\s+", " ", unescape((s or "").strip()))


def _is_navigation_element(el: Tag) -> bool:
    """判断元素是否为导航、页眉、页脚等非内容区域"""
    if not isinstance(el, Tag):
        return False

    # 检查class和id中的关键词
    text_attrs = []
    if el.get("class"):
        text_attrs.extend(el.get("class", []))
    if el.get("id"):
        text_attrs.append(el.get("id"))

    text_str = " ".join(text_attrs).lower()

    # 排除的关键词
    exclude_keywords = [
        "nav", "menu", "header", "footer", "sidebar", "widget",
        "ad", "banner", "promotion", "social", "share", "comment",
        "reply", "pagination", "breadcrumb", "search", "filter",
        "tag-cloud", "archive", "related", "recommended"
    ]

    return any(keyword in text_str for keyword in exclude_keywords)


def _get_article_containers(soup: BeautifulSoup) -> List[Tag]:
    """获取可能的文章容器，优先考虑语义化标签"""
    candidates = []

    # 1. 查找语义化的容器标签
    semantic_tags = ["main", "article", "section", "div"]
    for tag_name in semantic_tags:
        elements = soup.find_all(tag_name)
        candidates.extend(elements)

    # 2. 查找具有特定class的容器
    class_patterns = [
        r"post", r"article", r"entry", r"item", r"card", r"news",
        r"content", r"main", r"primary", r"list", r"grid",
        r"feed", r"stream", r"loop", r"wrap", r"container"
    ]

    for pattern in class_patterns:
        elements = soup.find_all(attrs={"class": re.compile(pattern, re.I)})
        candidates.extend(elements)

    # 去重并过滤导航元素
    unique_candidates = []
    seen = set()
    for el in candidates:
        if id(el) not in seen and not _is_navigation_element(el):
            unique_candidates.append(el)
            seen.add(id(el))

    return unique_candidates


def _score_container_advanced(el: Tag) -> Dict[str, Any]:
    """高级容器评分，返回详细评分信息"""
    if not isinstance(el, Tag):
        return {"score": 0, "reasons": [], "details": {}}

    score = 0
    reasons = []
    details = {}

    # 1. 基础内容评分
    links = el.find_all("a", href=True)
    link_count = len(links)
    details["link_count"] = link_count

    # 过滤有效链接（排除导航、标签等）
    valid_links = [a for a in links if _is_valid_content_link(a)]
    valid_link_count = len(valid_links)
    details["valid_link_count"] = valid_link_count

    score += min(valid_link_count * 3, 60)  # 每个有效链接3分，最多60分
    if valid_link_count > 3:
        reasons.append(f"包含{valid_link_count}个有效内容链接")

    # 2. 标题元素评分
    headings = el.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
    heading_count = len(headings)
    details["heading_count"] = heading_count
    score += heading_count * 5  # 每个标题5分
    if heading_count > 2:
        reasons.append(f"包含{heading_count}个标题元素")

    # 3. 文本内容质量评分
    text_content = _clean_text(el.get_text())
    text_length = len(text_content)
    details["text_length"] = text_length

    if text_length > 500:
        score += 15
        reasons.append("包含丰富文本内容")
    elif text_length > 200:
        score += 8
        reasons.append("包含适量文本内容")

    # 4. 语义化标签评分
    if el.name in ["main", "article", "section"]:
        score += 20
        reasons.append(f"使用语义化标签: {el.name}")

    # 5. Class名称评分
    classes = el.get("class", [])
    class_str = " ".join(classes).lower()
    details["classes"] = classes

    content_keywords = [
        ("post", 12), ("article", 12), ("news", 10), ("story", 8),
        ("entry", 8), ("item", 6), ("content", 8), ("main", 10),
        ("primary", 8), ("list", 7), ("feed", 8), ("stream", 7),
        ("loop", 6), ("wrap", 4), ("container", 5), ("grid", 6)
    ]

    for keyword, points in content_keywords:
        if keyword in class_str:
            score += points
            reasons.append(f"包含关键词: {keyword}")

    # 6. 结构评分 - 检查是否包含重复的文章结构
    article_like = el.find_all(
        ["article", "div"],
        class_=re.compile(r"post|article|entry|item|news|story", re.I)
    )
    details["article_like_count"] = len(article_like)

    if len(article_like) >= 3:
        score += 25
        reasons.append(f"包含{len(article_like)}个文章项")
    elif len(article_like) >= 2:
        score += 15
        reasons.append(f"包含{len(article_like)}个文章项")

    # 7. 链接质量深度评分
    quality_links = 0
    for link in valid_links[:15]:  # 检查前15个有效链接
        href = link.get("href", "")
        link_text = _clean_text(link.get_text())

        # 高质量链接特征
        if (len(link_text) > 8 and len(link_text) < 200 and
                not re.search(r"(tag|category|author|page/\d+|#)", href, re.I) and
                not re.search(r"(more|continue|read\s*more|阅读更多)", link_text.lower())):
            quality_links += 1

    score += quality_links * 4
    details["quality_links"] = quality_links
    if quality_links > 3:
        reasons.append(f"包含{quality_links}个高质量链接")

    # 8. 内容密度评分
    if valid_link_count > 0:
        content_density = text_length / valid_link_count
        if 50 < content_density < 500:  # 适中的内容密度
            score += 10
            reasons.append("内容密度适中")
        details["content_density"] = content_density

    # 9. 嵌套层级评分
    depth = _get_element_depth(el)
    details["depth"] = depth
    if 2 <= depth <= 6:  # 适中的嵌套深度
        score += 8
        reasons.append("DOM层次深度适中")

    # 10. 时间/日期元素检测
    time_elements = el.find_all(["time", "span"], class_=re.compile(r"date|time", re.I))
    if time_elements:
        score += 5
        reasons.append("包含时间日期信息")
        details["has_dates"] = True

    return {
        "score": min(score, 100),  # 限制最高分数
        "reasons": reasons,
        "details": details
    }


def _is_valid_content_link(link: Tag) -> bool:
    """判断是否为有效的内容链接"""
    if not link or not link.get("href"):
        return False

    href = link.get("href", "").lower()
    text = _clean_text(link.get_text()).lower()

    # 排除的链接类型
    exclude_patterns = [
        r"javascript:", r"mailto:", r"tel:", r"#",
        r"/tag/", r"/tags/", r"/category/", r"/categories/",
        r"/author/", r"/search", r"/login", r"/register",
        r"\.pdf$", r"\.doc", r"\.zip", r"\.exe"
    ]

    for pattern in exclude_patterns:
        if re.search(pattern, href):
            return False

    # 排除的文本模式
    exclude_texts = [
        "more", "read more", "continue", "阅读更多", "查看更多",
        "share", "分享", "comment", "评论", "like", "点赞"
    ]

    for exclude_text in exclude_texts:
        if exclude_text in text:
            return False

    # 必须有meaningful的文本
    return len(text.strip()) > 3


def _get_element_depth(element: Tag) -> int:
    """获取元素在DOM中的深度"""
    depth = 0
    current = element
    while current and current.parent and current.parent.name != '[document]':
        depth += 1
        current = current.parent
        if depth > 20:  # 防止无限循环
            break
    return depth


def _find_best_container(soup: BeautifulSoup) -> Tuple[Optional[Tag], Dict]:
    """找到最佳的文章列表容器"""
    candidates = _get_article_containers(soup)

    if not candidates:
        return None, {"error": "未找到候选容器"}

    # 评分所有候选容器
    scored_candidates = []
    for candidate in candidates:
        score_info = _score_container_advanced(candidate)
        if score_info["score"] > 15:  # 提高最低分数阈值
            scored_candidates.append((candidate, score_info))

    if not scored_candidates:
        # 如果没有高分候选者，尝试更宽松的标准
        for candidate in candidates:
            score_info = _score_container_advanced(candidate)
            if score_info["score"] > 8:
                scored_candidates.append((candidate, score_info))

    if not scored_candidates:
        return None, {"error": "所有候选容器评分过低"}

    # 按分数排序
    scored_candidates.sort(key=lambda x: x[1]["score"], reverse=True)

    best_container, best_score = scored_candidates[0]

    # 返回最佳容器和评分详情
    return best_container, {
        "selected": True,
        "score": best_score["score"],
        "reasons": best_score["reasons"],
        "details": best_score["details"],
        "alternatives": len(scored_candidates) - 1,
        "container_tag": best_container.name,
        "container_classes": best_container.get("class", [])
    }


def _generate_flexible_selector(el: Tag, max_parts: int = 3) -> str:
    """生成更灵活和稳定的CSS选择器"""
    if not isinstance(el, Tag):
        return ""

    # 优先使用ID（但要排除动态生成的ID）
    if el.get("id") and not re.search(r'\d{4,}|[a-f0-9]{8,}', el.get("id")):
        return f"#{el.get('id')}"

    # 使用稳定的class组合
    classes = el.get("class", [])
    if classes:
        # 过滤掉可能不稳定的class
        stable_classes = [
            cls for cls in classes
            if (not re.search(r'\d{3,}|[a-f0-9]{8,}', cls) and
                len(cls) > 2 and
                not cls.startswith('wp-') and  # WordPress动态class
                not cls.startswith('elementor-'))  # Elementor动态class
        ]
        if stable_classes:
            # 选择最具描述性的class
            priority_classes = []
            for cls in stable_classes[:3]:  # 最多3个class
                if any(keyword in cls.lower() for keyword in
                       ['post', 'article', 'entry', 'item', 'content', 'main', 'list']):
                    priority_classes.insert(0, cls)  # 优先级高的放前面
                else:
                    priority_classes.append(cls)

            if priority_classes:
                return f".{'.'.join(priority_classes[:2])}"  # 最多使用2个class

    # 使用标签名 + 位置（更精确的定位）
    if el.parent:
        siblings = [sibling for sibling in el.parent.children
                    if isinstance(sibling, Tag) and sibling.name == el.name]
        if len(siblings) == 1:
            return el.name
        else:
            try:
                idx = siblings.index(el) + 1
                return f"{el.name}:nth-of-type({idx})"
            except ValueError:
                return el.name

    return el.name


def _find_relative_selectors(container: Tag, target_type: str) -> Optional[str]:
    """在容器内查找相对选择器，增强版"""
    selectors_to_try = []

    if target_type == "title":
        selectors_to_try = [
            # 标题内的链接（最常见）
            "h1 a", "h2 a", "h3 a", "h4 a", "h5 a", "h6 a",
            # 常见标题class
            ".title a", ".post-title a", ".entry-title a", ".article-title a",
            ".headline a", ".news-title a", ".story-title a",
            # 直接的标题元素
            "h1", "h2", "h3", "h4", "h5", "h6",
            ".title", ".post-title", ".entry-title",
            # 包含特定路径的链接
            "a[href*='post']", "a[href*='article']", "a[href*='news']",
            # 排除标签和分类链接的通用链接
            "a:not([href*='tag']):not([href*='category']):not([href*='author'])"
        ]
    elif target_type == "link":
        selectors_to_try = [
            # 标题链接
            "h1 a", "h2 a", "h3 a", "h4 a", "h5 a", "h6 a",
            ".title a", ".post-title a", ".entry-title a",
            # 内容链接
            "a[href*='post']", "a[href*='article']", "a[href*='news']",
            ".read-more a", ".continue a", ".post-link a",
            # 通用链接（排除导航）
            "a:not([href*='tag']):not([href*='category']):not([href*='author']):not([href*='#'])"
        ]
    elif target_type == "date":
        selectors_to_try = [
            # 语义化时间标签
            "time", "time[datetime]",
            # 常见日期class
            ".date", ".post-date", ".entry-date", ".article-date",
            ".published", ".timestamp", ".time", ".datetime",
            ".news-date", ".story-date", ".publish-date",
            # 包含日期属性的元素
            "[datetime]", "[data-date]", "[data-time]",
            # 通用日期模式
            "span:contains('202')", "div:contains('202')",  # 假设是2020年代
            ".meta .date", ".post-meta .date", ".entry-meta .date"
        ]
    else:
        return None

    for selector in selectors_to_try:
        try:
            elements = container.select(selector)
            if elements:
                # 验证找到的元素是否合理
                if target_type in ["title", "link"]:
                    valid_elements = []
                    for el in elements:
                        text = el.get_text(strip=True)
                        if (len(text) > 5 and len(text) < 200 and
                                not _is_navigation_text(text)):
                            valid_elements.append(el)

                    if len(valid_elements) >= 2:  # 至少找到2个有效元素
                        return selector

                elif target_type == "date":
                    valid_elements = []
                    for el in elements:
                        text = el.get_text(strip=True)
                        if (len(text) > 3 and
                                _looks_like_date(text)):
                            valid_elements.append(el)

                    if valid_elements:
                        return selector
        except Exception:
            continue  # 忽略CSS选择器语法错误

    return None


def _is_navigation_text(text: str) -> bool:
    """判断文本是否为导航文本"""
    text_lower = text.lower().strip()
    nav_keywords = [
        'home', 'about', 'contact', 'login', 'register', 'menu',
        '首页', '关于', '联系', '登录', '注册', '菜单',
        'next', 'previous', 'more', 'read more', 'continue',
        '下一页', '上一页', '更多', '阅读更多', '继续'
    ]

    return any(keyword in text_lower for keyword in nav_keywords)


def _looks_like_date(text: str) -> bool:
    """判断文本是否看起来像日期"""
    date_patterns = [
        r'\d{4}[-/]\d{1,2}[-/]\d{1,2}',  # 2023-01-01
        r'\d{1,2}[-/]\d{1,2}[-/]\d{4}',  # 01-01-2023
        r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec).*\d{4}',  # Jan 2023
        r'\d{4}年\d{1,2}月\d{1,2}日',  # 2023年1月1日
        r'\d{1,2}:\d{2}',  # 时间格式
        r'(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)',  # 星期
        r'(今天|昨天|前天|明天)',  # 中文日期
        r'\d+\s*(minutes?|hours?|days?|weeks?)\s*ago',  # 相对时间
        r'\d+\s*(分钟|小时|天|周)\s*前'  # 中文相对时间
    ]

    return any(re.search(pattern, text, re.IGNORECASE) for pattern in date_patterns)


def analyze_pagination_structure(soup: BeautifulSoup, base_url: str = "") -> Dict[str, Any]:
    """分析页面的分页结构"""
    analysis = {
        "type": "unknown",
        "confidence": 0.0,
        "patterns": [],
        "next_page_selector": None,
        "page_pattern": None,
        "load_more_selector": None
    }

    # 查找分页容器
    pagination_containers = []

    # 1. 语义化分页容器
    nav_elements = soup.find_all('nav', class_=re.compile(r'page|pagination', re.I))
    pagination_containers.extend(nav_elements)

    # 2. 通用分页容器
    div_elements = soup.find_all('div', class_=re.compile(r'page|pagination|pager', re.I))
    pagination_containers.extend(div_elements)

    # 3. 如果没有找到专门的分页容器，查找包含分页链接的容器
    if not pagination_containers:
        potential_containers = soup.find_all(['div', 'section', 'footer'])
        for container in potential_containers:
            if _has_pagination_indicators(container):
                pagination_containers.append(container)

    for container in pagination_containers:
        # 分析"下一页"模式
        next_analysis = _analyze_next_page_pattern(container)
        if next_analysis["confidence"] > analysis["confidence"]:
            analysis.update(next_analysis)

        # 分析页码模式
        number_analysis = _analyze_number_pagination(container, base_url)
        if number_analysis["confidence"] > analysis["confidence"]:
            analysis.update(number_analysis)

    # 查找"加载更多"按钮（可能在分页容器外）
    load_more_analysis = _analyze_load_more_pattern(soup)
    if load_more_analysis["confidence"] > analysis["confidence"]:
        analysis.update(load_more_analysis)

    # 检测无限滚动
    infinite_analysis = _analyze_infinite_scroll(soup)
    if infinite_analysis["confidence"] > analysis["confidence"]:
        analysis.update(infinite_analysis)

    return analysis


def _has_pagination_indicators(container: Tag) -> bool:
    """检查容器是否包含分页指示器"""
    text = container.get_text().lower()
    links = container.find_all('a', href=True)

    # 检查是否有分页相关的文本
    pagination_keywords = [
        'next', 'previous', 'prev', 'page', '下一页', '上一页', '页',
        'first', 'last', 'more', '更多', '1', '2', '3'
    ]

    has_keywords = any(keyword in text for keyword in pagination_keywords)
    has_multiple_links = len(links) > 2

    return has_keywords and has_multiple_links


def _analyze_next_page_pattern(container: Tag) -> Dict[str, Any]:
    """分析下一页模式"""
    next_patterns = [
        (r'next|下一页|siguiente|prÃ³ximo|suivant|weiter', 0.9),
        (r'>|»|›|→', 0.8),
        (r'more|更多|más|plus|show\s*more', 0.7)
    ]

    analysis = {"type": "next", "confidence": 0.0}

    links = container.find_all('a', href=True)

    for link in links:
        link_text = link.get_text(strip=True).lower()
        link_title = (link.get('title') or '').lower()
        combined_text = f"{link_text} {link_title}"

        for pattern, confidence in next_patterns:
            if re.search(pattern, combined_text, re.IGNORECASE):
                if confidence > analysis["confidence"]:
                    analysis.update({
                        "confidence": confidence,
                        "next_page_selector": _generate_flexible_selector(link),
                        "element": link
                    })
                break

    return analysis


def _analyze_number_pagination(container: Tag, base_url: str) -> Dict[str, Any]:
    """分析页码分页模式"""
    analysis = {"type": "number", "confidence": 0.0}

    links = container.find_all('a', href=True)
    page_links = []

    for link in links:
        text = link.get_text(strip=True)
        if text.isdigit() and 1 <= int(text) <= 999:
            page_links.append((int(text), link.get('href'), link))

    if len(page_links) >= 3:  # 至少需要3个页码链接
        page_links.sort(key=lambda x: x[0])  # 按页码排序

        # 分析URL模式
        hrefs = [href for _, href, _ in page_links]
        pattern = _detect_url_pattern(hrefs)

        if pattern:
            analysis.update({
                "confidence": 0.8,
                "page_pattern": pattern,
                "page_links": page_links
            })

    return analysis


def _analyze_load_more_pattern(soup: BeautifulSoup) -> Dict[str, Any]:
    """分析加载更多模式"""
    analysis = {"type": "loadmore", "confidence": 0.0}

    # 查找加载更多按钮
    load_more_selectors = [
        ('button', r'load|more|show|加载|更多|显示', 0.9),
        ('a', r'load.*more|show.*more|加载.*更多|显示.*更多', 0.8),
        ('[class*="load"]', '', 0.7),
        ('[class*="more"]', '', 0.7),
        ('[id*="load"]', '', 0.6),
        ('[id*="more"]', '', 0.6)
    ]

    for selector_tag, text_pattern, base_confidence in load_more_selectors:
        elements = soup.select(selector_tag) if selector_tag.startswith('[') else soup.find_all(selector_tag)

        for element in elements:
            element_text = element.get_text(strip=True).lower()
            element_class = ' '.join(element.get('class', [])).lower()
            element_id = (element.get('id') or '').lower()

            combined_text = f"{element_text} {element_class} {element_id}"

            confidence = base_confidence
            if text_pattern and re.search(text_pattern, combined_text, re.IGNORECASE):
                confidence += 0.1

            # 额外检查
            if any(keyword in combined_text for keyword in ['load', 'more', '加载', '更多']):
                confidence += 0.05

            if confidence > analysis["confidence"]:
                analysis.update({
                    "confidence": confidence,
                    "load_more_selector": _generate_flexible_selector(element),
                    "element": element
                })

    return analysis


def _analyze_infinite_scroll(soup: BeautifulSoup) -> Dict[str, Any]:
    """分析无限滚动模式"""
    analysis = {"type": "infinite", "confidence": 0.0}

    # 检查无限滚动的指示器
    infinite_indicators = [
        'infinite', 'scroll', 'lazy', 'autopager', 'endless',
        '无限', '滚动', '懒加载'
    ]

    page_text = soup.get_text().lower()
    page_html = str(soup).lower()

    indicator_count = sum(1 for indicator in infinite_indicators
                          if indicator in page_html)

    if indicator_count > 0:
        analysis["confidence"] = min(0.6 + indicator_count * 0.1, 0.9)

    # 检查是否有Ajax加载相关的脚本
    scripts = soup.find_all('script')
    for script in scripts:
        script_content = script.get_text().lower()
        if any(keyword in script_content for keyword in
               ['ajax', 'fetch', 'infinite', 'scroll', 'loadmore']):
            analysis["confidence"] = max(analysis["confidence"], 0.7)
            break

    return analysis


def _detect_url_pattern(hrefs: List[str]) -> Optional[str]:
    """检测URL中的页码模式"""
    if len(hrefs) < 2:
        return None

    first_href = hrefs[0]
    second_href = hrefs[1]

    # 常见的页码模式
    patterns = [
        (r'page=(\d+)', 'page={n}'),
        (r'/page/(\d+)', '/page/{n}'),
        (r'/p(\d+)', '/p{n}'),
        (r'_(\d+)\.html', '_{n}.html'),
        (r'/(\d+)/?$', '/{n}'),
        (r'p=(\d+)', 'p={n}')
    ]

    for pattern, replacement in patterns:
        matches = [re.search(pattern, href) for href in hrefs[:3]]
        if all(matches):
            # 验证页码是连续的
            page_numbers = [int(match.group(1)) for match in matches if match]
            if len(page_numbers) >= 2 and page_numbers[1] == page_numbers[0] + 1:
                return re.sub(pattern, replacement, first_href)

    return None


def guess_selectors_improved(html: str) -> Dict[str, Optional[str]]:
    """改进版选择器猜测，支持更多场景和更准确的识别"""
    soup = BeautifulSoup(html, "html.parser")

    # 1. 找到最佳容器
    container, container_info = _find_best_container(soup)

    if not container:
        return {
            "list_selector": "body",
            "title_selector": "a",
            "link_selector": "a",
            "date_selector": None,
            "debug_info": container_info
        }

    # 2. 生成容器选择器
    list_selector = _generate_flexible_selector(container)

    # 3. 在容器内查找相对选择器
    title_selector = _find_relative_selectors(container, "title")
    link_selector = _find_relative_selectors(container, "link")
    date_selector = _find_relative_selectors(container, "date")

    # 4. 后备选择器策略
    if not link_selector:
        # 尝试在容器内找到最佳链接
        links = container.find_all("a", href=True)
        valid_links = [a for a in links if _is_valid_content_link(a)]
        if valid_links:
            link_selector = "a[href]:not([href*='tag']):not([href*='category'])"
        else:
            link_selector = "a"

    if not title_selector:
        # 如果没有找到专门的标题选择器，使用链接选择器
        title_selector = link_selector

    # 5. 分析分页结构
    pagination_info = analyze_pagination_structure(soup)

    # 6. 优化选择器
    optimized_selectors = _optimize_selectors({
        "list_selector": list_selector,
        "title_selector": title_selector,
        "link_selector": link_selector,
        "date_selector": date_selector
    }, container)

    return {
        **optimized_selectors,
        "pagination_info": pagination_info,
        "debug_info": {
            **container_info,
            "container_selector": list_selector,
            "found_selectors": {
                "title": title_selector,
                "link": link_selector,
                "date": date_selector
            },
            "optimization_applied": True
        }
    }


def _optimize_selectors(selectors: Dict[str, str], container: Tag) -> Dict[str, str]:
    """优化选择器以提高准确性和稳定性"""
    optimized = selectors.copy()

    # 优化标题选择器
    if selectors.get("title_selector"):
        title_elements = container.select(selectors["title_selector"])
        if title_elements:
            # 检查是否需要排除某些子元素
            sample_element = title_elements[0]
            has_time = sample_element.find("time")
            has_small = sample_element.find("small")

            if has_time or has_small:
                # 添加排除逻辑的建议
                optimized["title_extraction_hints"] = {
                    "exclude_time": bool(has_time),
                    "exclude_small": bool(has_small),
                    "needs_text_cleaning": True
                }

    # 优化链接选择器
    if selectors.get("link_selector"):
        link_elements = container.select(selectors["link_selector"])
        if link_elements:
            # 检查链接质量
            valid_count = sum(1 for el in link_elements if _is_valid_content_link(el))
            if valid_count < len(link_elements) * 0.7:
                # 添加更严格的过滤
                base_selector = selectors["link_selector"]
                optimized[
                    "link_selector"] = f"{base_selector}[href]:not([href^='#']):not([href*='tag']):not([href*='category'])"

    return optimized


# 测试函数
def test_selector_detection_enhanced(html: str, url: str = "") -> Dict[str, Any]:
    """增强版测试选择器检测效果"""
    result = guess_selectors_improved(html)

    print("=== 增强版选择器检测结果 ===")
    print(f"List Selector: {result['list_selector']}")
    print(f"Title Selector: {result['title_selector']}")
    print(f"Link Selector: {result['link_selector']}")
    print(f"Date Selector: {result['date_selector']}")

    if "debug_info" in result:
        debug = result["debug_info"]
        print(f"\n=== 调试信息 ===")
        print(f"容器评分: {debug.get('score', 'N/A')}")
        print(f"选择理由: {', '.join(debug.get('reasons', []))}")
        print(f"备选容器数: {debug.get('alternatives', 'N/A')}")

        if "details" in debug:
            details = debug["details"]
            print(f"有效链接数: {details.get('valid_link_count', 'N/A')}")
            print(f"标题元素数: {details.get('heading_count', 'N/A')}")
            print(f"文本长度: {details.get('text_length', 'N/A')}")

    if "pagination_info" in result:
        pagination = result["pagination_info"]
        print(f"\n=== 分页信息 ===")
        print(f"分页类型: {pagination.get('type', 'unknown')}")
        print(f"置信度: {pagination.get('confidence', 0):.2f}")
        if pagination.get('next_page_selector'):
            print(f"下一页选择器: {pagination['next_page_selector']}")

    return result
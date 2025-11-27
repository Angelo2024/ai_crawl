from dataclasses import dataclass, asdict, fields
from typing import Optional, List
from fastapi import Form
import inspect


# 这个 dataclass 用于 API 中接收表单数据
@dataclass
class ScraperRecipe:
    domain: str = ""
    start_url: Optional[str] = None
    list_selector: str = ""
    title_selector: str = ""
    link_selector: str = ""
    date_selector: Optional[str] = None
    load_strategy: str = "static"
    wait_until: str = "domcontentloaded"
    wait_ms: int = 0
    next_page_selector: Optional[str] = None
    max_pages: int = 1
    infinite_scroll: Optional[bool] = False
    scroll_times: int = 0
    scroll_wait_ms: int = 800

    # 分页相关字段
    pagination_mode: str = "none"
    load_more_selector: Optional[str] = None
    page_pattern: Optional[str] = None

    # 新增：Drupal分页支持
    drupal_zero_based: bool = False

    # 智能文本提取选项
    title_exclude_small: bool = False
    title_exclude_time: bool = False
    title_exclude_span: bool = False
    exclude_current_page: bool = False

    # 反爬虫配置
    use_proxy: bool = False
    rotate_ua: bool = False
    accept_cookies: bool = False

    def to_dict(self):
        return asdict(self)

    @classmethod
    def as_form(cls):
        """修复后的表单依赖注入方法"""

        async def _as_form(
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
                scroll_wait_ms: int = Form(800),
                # 分页字段
                pagination_mode: str = Form("none"),
                load_more_selector: Optional[str] = Form(None),
                page_pattern: Optional[str] = Form(None),
                drupal_zero_based: bool = Form(False),  # 新增
                # 智能文本提取选项
                title_exclude_small: bool = Form(False),
                title_exclude_time: bool = Form(False),
                title_exclude_span: bool = Form(False),
                exclude_current_page: bool = Form(False),
                # 反爬虫配置
                use_proxy: bool = Form(False),
                rotate_ua: bool = Form(False),
                accept_cookies: bool = Form(False)
        ) -> "ScraperRecipe":
            return cls(
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
                scroll_wait_ms=scroll_wait_ms,
                pagination_mode=pagination_mode,
                load_more_selector=load_more_selector,
                page_pattern=page_pattern,
                drupal_zero_based=drupal_zero_based,  # 新增
                title_exclude_small=title_exclude_small,
                title_exclude_time=title_exclude_time,
                title_exclude_span=title_exclude_span,
                exclude_current_page=exclude_current_page,
                use_proxy=use_proxy,
                rotate_ua=rotate_ua,
                accept_cookies=accept_cookies
            )

        return _as_form

    def __post_init__(self):
        """数据验证和自动配置"""
        # 自动检测Drupal分页系统
        if (self.page_pattern and
                ('drupal' in self.domain.lower() or
                 'data-options' in str(self.__dict__).lower() or
                 self.pagination_mode == 'number')):

            # 如果URL模式看起来像Drupal，自动启用零基页码
            if not hasattr(self, '_drupal_auto_detected'):
                self.drupal_zero_based = True
                self._drupal_auto_detected = True

        # 验证分页配置
        if self.pagination_mode == 'number' and not self.page_pattern:
            raise ValueError("数字分页模式需要设置page_pattern")

        if self.pagination_mode == 'next' and not self.next_page_selector:
            raise ValueError("下一页链接模式需要设置next_page_selector")

        if self.pagination_mode == 'loadmore' and not self.load_more_selector:
            raise ValueError("加载更多模式需要设置load_more_selector")

        # 自动调整最大页数
        if self.max_pages <= 0:
            self.max_pages = 1

        # 自动调整等待时间
        if self.wait_ms < 0:
            self.wait_ms = 0


# 这个 dataclass 用于 scraper_main.py 内部处理抓取结果
@dataclass
class ScrapedItem:
    title: str
    url: str
    date: Optional[str] = None

    def model_dump(self):
        return asdict(self)

    def __post_init__(self):
        """数据清理和验证"""
        # 清理标题
        if self.title:
            self.title = self.title.strip()
            import re
            self.title = re.sub(r'\s+', ' ', self.title)

        # 验证URL
        if self.url and not self.url.startswith(('http://', 'https://')):
            raise ValueError(f"无效的URL: {self.url}")

        # 清理日期
        if self.date:
            self.date = self.date.strip()


# 分页配置验证器
class PaginationValidator:
    """分页配置验证器"""

    @staticmethod
    def validate_recipe(recipe: ScraperRecipe) -> List[str]:
        """验证配置并返回警告列表"""
        warnings = []

        # 检查分页配置
        if recipe.pagination_mode == 'number':
            if not recipe.page_pattern:
                warnings.append("数字分页模式需要设置URL模式")
            elif '{n}' not in recipe.page_pattern:
                warnings.append("URL模式中缺少{n}占位符")

            # 检查Drupal配置
            if recipe.drupal_zero_based and recipe.max_pages > 20:
                warnings.append("Drupal分页建议限制最大页数以避免过多请求")

        elif recipe.pagination_mode == 'next':
            if not recipe.next_page_selector:
                warnings.append("下一页链接模式需要设置选择器")

        elif recipe.pagination_mode == 'loadmore':
            if not recipe.load_more_selector:
                warnings.append("加载更多模式需要设置选择器")
            if recipe.load_strategy == 'static':
                warnings.append("加载更多通常需要动态渲染模式")

        # 检查选择器配置
        if not recipe.list_selector:
            warnings.append("建议设置列表容器选择器以提高准确性")

        if not recipe.title_selector:
            warnings.append("建议设置标题选择器")

        # 检查反爬虫配置
        if recipe.use_proxy and recipe.load_strategy == 'static':
            warnings.append("代理通常与动态渲染配合使用效果更好")

        return warnings

    @staticmethod
    def suggest_improvements(recipe: ScraperRecipe) -> List[str]:
        """建议改进方案"""
        suggestions = []

        # 性能优化建议
        if recipe.wait_ms > 5000:
            suggestions.append("等待时间过长可能影响抓取效率")

        if recipe.max_pages > 50:
            suggestions.append("页数过多建议分批处理或增加并发控制")

        # 稳定性建议
        if recipe.load_strategy == 'static' and recipe.pagination_mode == 'loadmore':
            suggestions.append("加载更多功能建议使用动态渲染模式")

        if not recipe.rotate_ua and recipe.max_pages > 10:
            suggestions.append("多页抓取建议启用User-Agent轮换")

        # Drupal特定建议
        if recipe.drupal_zero_based:
            suggestions.append("已启用Drupal零基页码，页码将从0开始计算")

        return suggestions


# 配置工厂类
class RecipeFactory:
    """配置工厂，用于创建不同类型的抓取配置"""

    @staticmethod
    def create_drupal_recipe(domain: str, start_url: str, query_params: dict) -> ScraperRecipe:
        """创建Drupal分页配置"""
        # 构建URL模式
        page_pattern = f"{start_url.split('?')[0]}?"

        # 处理查询参数
        params = []
        for key, value in query_params.items():
            if key == 'page':
                params.append('page={n}')
            elif isinstance(value, list):
                for v in value:
                    params.append(f"{key}[]={v}")
            else:
                params.append(f"{key}={value}")

        page_pattern += '&'.join(params)

        return ScraperRecipe(
            domain=domain,
            start_url=start_url,
            pagination_mode='number',
            page_pattern=page_pattern,
            drupal_zero_based=True,
            load_strategy='dynamic',  # Drupal通常需要JavaScript
            max_pages=10,
            wait_ms=2000
        )

    @staticmethod
    def create_simple_recipe(domain: str, start_url: str) -> ScraperRecipe:
        """创建简单配置"""
        return ScraperRecipe(
            domain=domain,
            start_url=start_url,
            pagination_mode='none',
            load_strategy='static',
            max_pages=1
        )

    @staticmethod
    def create_next_page_recipe(domain: str, start_url: str, selector: str) -> ScraperRecipe:
        """创建下一页链接配置"""
        return ScraperRecipe(
            domain=domain,
            start_url=start_url,
            pagination_mode='next',
            next_page_selector=selector,
            max_pages=5,
            exclude_current_page=True
        )
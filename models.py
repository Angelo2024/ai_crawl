from typing import Optional
from datetime import datetime
from sqlmodel import Field, SQLModel


# 1. 全局设置表
class GlobalSettings(SQLModel, table=True):
    __table_args__ = {"extend_existing": True}
    id: Optional[int] = Field(default=1, primary_key=True)
    client_profile: str = Field(default="默认客户画像：关注科技与金融领域的投资机会...")
    competitors_json: str = Field(default='{"中文名": ["竞对A", "竞对B"], "英文名": ["CompA", "CompB"]}')
    topics_json: str = Field(
        default='["健康与安全", "清洁技术机遇", "绿色建筑", "应对气候变化", "生物多样性", "竞争对手动态", "其他"]')
    categories_json: str = Field(default='{"政策动态": "...", "前沿资讯": "...", "必读报告": "..."}')


# 2. 网站配置表
class SiteConfig(SQLModel, table=True):
    __table_args__ = {"extend_existing": True}
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    url: str

    is_active: bool = Field(default=True)

    list_selector: str
    title_selector: str
    link_selector: str
    date_selector: Optional[str] = None
    next_page_selector: Optional[str] = None
    date_format: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.now)


# 3. 文章数据表
class Article(SQLModel, table=True):
    __table_args__ = {"extend_existing": True}
    id: Optional[int] = Field(default=None, primary_key=True)
    site_id: int
    title: str
    url: str
    publish_date: Optional[datetime] = None
    crawled_at: datetime = Field(default_factory=datetime.now)

    content_raw: Optional[str] = None

    ai_status: str = "pending"
    ai_summary: Optional[str] = None
    ai_category: Optional[str] = None
    ai_topic: Optional[str] = None

    # 标题字段
    new_title: Optional[str] = None  # 中文标题
    title_en: Optional[str] = None  # 英文标题 (新)

    ai_score: Optional[float] = None
    ai_reasoning: Optional[str] = None
    ai_score_details: Optional[str] = None
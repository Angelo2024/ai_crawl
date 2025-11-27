# schemas/intelligence_schemas.py
from __future__ import annotations
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


# ===== 基础模型 =====

class IntelligenceSourceBase(BaseModel):
    url: str
    title: Optional[str] = None
    domain: Optional[str] = None


class CompetitorBase(BaseModel):
    name: str
    description: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)


class IntelligenceScoreDetail(BaseModel):
    """AI评分详情"""
    score: float = Field(ge=0, le=10)
    reason: str


class IntelligenceScore(BaseModel):
    """多维度评分"""
    importance: Optional[IntelligenceScoreDetail] = None
    credibility: Optional[IntelligenceScoreDetail] = None
    relevance: Optional[IntelligenceScoreDetail] = None
    timeliness: Optional[IntelligenceScoreDetail] = None
    impact: Optional[IntelligenceScoreDetail] = None


# ===== 请求模型 =====

class IntelligenceCreate(BaseModel):
    """创建情报"""
    title: str = Field(..., max_length=500)
    summary: Optional[str] = None
    content: Optional[str] = None
    topic: Optional[str] = Field(None, max_length=100)
    category: Optional[str] = Field(None, max_length=50)
    tags: List[str] = Field(default_factory=list)
    news_time: Optional[datetime] = None
    sources: List[IntelligenceSourceBase] = Field(default_factory=list)
    competitor_ids: List[int] = Field(default_factory=list)


class IntelligenceUpdate(BaseModel):
    """更新情报"""
    title: Optional[str] = Field(None, max_length=500)
    summary: Optional[str] = None
    content: Optional[str] = None
    topic: Optional[str] = Field(None, max_length=100)
    category: Optional[str] = Field(None, max_length=50)
    tags: Optional[List[str]] = None
    news_time: Optional[datetime] = None
    ai_score: Optional[float] = Field(None, ge=0, le=10)
    score_dimensions: Optional[Dict[str, Any]] = None
    quality_status: Optional[str] = Field(None, pattern="^(pending|approved|rejected)$")
    competitor_ids: Optional[List[int]] = None


class IntelligenceFilter(BaseModel):
    """筛选条件"""
    title: Optional[str] = None
    topic: Optional[str] = None
    category: Optional[str] = None
    competitor: Optional[str] = None
    quality_status: Optional[str] = Field(None, pattern="^(pending|approved|rejected)$")
    min_score: Optional[float] = Field(None, ge=0, le=10)
    max_score: Optional[float] = Field(None, ge=0, le=10)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    tags: Optional[List[str]] = None


class MergeRequest(BaseModel):
    """合并请求"""
    intelligence_ids: List[int] = Field(..., min_items=2, description="要合并的情报ID列表，至少2个")
    merged_title: str = Field(..., max_length=500, description="合并后的标题")
    merged_summary: str = Field(..., description="合并后的摘要")
    merged_topic: str = Field(..., max_length=100, description="合并后的议题")
    merged_category: str = Field(..., max_length=50, description="合并后的类别")
    additional_source: Optional[str] = Field(None, description="额外的信息源URL")
    competitor_ids: List[int] = Field(default_factory=list, description="相关竞争对手ID列表")
    delete_originals: bool = Field(default=True, description="是否删除原始情报")


class MergeByUrlRequest(BaseModel):
    """通过URL合并请求"""
    url: str = Field(..., description="用于查找相同报道的URL")
    selected_ids: List[int] = Field(..., min_items=1, description="当前选中的情报ID列表")
    merge_title: Optional[str] = Field(None, max_length=500, description="自定义合并标题")
    merge_summary: Optional[str] = Field(None, description="自定义合并摘要")
    delete_originals: bool = Field(default=True, description="是否删除原始情报")


class CrawlRequest(BaseModel):
    """爬虫请求"""
    topic: str = Field(..., max_length=100)
    category: str = Field(..., max_length=50)
    websites: List[str] = Field(..., min_items=1)
    interval: str = Field(default="6h", pattern="^(1h|6h|12h|24h)$")
    max_items: int = Field(default=50, ge=1, le=200)


class BatchProcessRequest(BaseModel):
    """批量处理请求"""
    intelligence_ids: List[int] = Field(..., min_items=1)
    action: str = Field(..., pattern="^(ai_process|quality_approve|quality_reject|delete|merge)$")
    merge_data: Optional[MergeRequest] = Field(None, description="合并操作的详细数据")


class AIProcessRequest(BaseModel):
    """AI处理请求"""
    intelligence_ids: List[int] = Field(..., min_items=1, description="要处理的情报ID列表")
    use_enhanced_prompt: bool = Field(default=False, description="是否使用增强提示词")
    custom_client_profile: Optional[str] = Field(None, description="自定义客户画像")


# ===== 响应模型 =====

class IntelligenceSourceResponse(BaseModel):
    """情报来源响应"""
    id: int
    url: str
    title: Optional[str] = None
    domain: str
    fetch_time: datetime

    model_config = ConfigDict(from_attributes=True)


class CompetitorResponse(BaseModel):
    """竞争对手响应"""
    id: int
    name: str
    description: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class IntelligenceResponse(BaseModel):
    """情报响应"""
    id: int
    title: str
    summary: Optional[str] = None
    content: Optional[str] = None
    topic: Optional[str] = None
    category: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    news_time: Optional[datetime] = None
    collect_time: datetime
    update_time: datetime
    ai_score: float
    score_dimensions: Dict[str, Any]
    quality_status: str
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_comment: Optional[str] = None
    is_merged: bool
    merged_count: int
    original_ids: Optional[List[int]] = None
    sources: List[IntelligenceSourceResponse]
    competitors: List[CompetitorResponse]

    model_config = ConfigDict(from_attributes=True)


class IntelligenceListResponse(BaseModel):
    """情报列表响应"""
    items: List[IntelligenceResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class MergeResponse(BaseModel):
    """合并响应"""
    status: str = Field(description="操作状态")
    message: str = Field(description="操作结果消息")
    merged_id: Optional[int] = Field(None, description="合并后的情报ID")
    merged_count: int = Field(description="合并的情报数量")
    deleted_ids: Optional[List[int]] = Field(None, description="被删除的原始情报ID列表")


class AIProcessResponse(BaseModel):
    """AI处理响应"""
    id: int
    status: str = Field(description="处理状态: success/error")
    ai_score: Optional[float] = Field(None, description="AI评分")
    topic: Optional[str] = Field(None, description="识别的议题")
    summary: Optional[str] = Field(None, description="AI生成的摘要")
    category: Optional[str] = Field(None, description="识别的类别")
    dimensions: Optional[Dict[str, Any]] = Field(None, description="评分维度详情")
    message: Optional[str] = Field(None, description="处理消息或错误信息")


class BatchAIProcessResponse(BaseModel):
    """批量AI处理响应"""
    status: str = Field(description="整体处理状态")
    message: str = Field(description="处理结果消息")
    success_count: int = Field(description="成功处理的数量")
    total_count: int = Field(description="总处理数量")
    results: List[AIProcessResponse] = Field(description="详细处理结果")


class CrawlResult(BaseModel):
    """爬虫结果"""
    url: str
    status: str
    count: Optional[int] = None
    message: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


class CrawlTopicResult(BaseModel):
    """议题爬取结果"""
    topic_id: int
    topic_name: str
    sources: List[CrawlResult]
    total_items: int
    total_saved: int


class CrawlResponse(BaseModel):
    """爬取响应"""
    status: str
    message: str
    total_crawled: int
    total_saved: int
    time_range: Dict[str, str]
    results: List[CrawlTopicResult]


class ExportRequest(BaseModel):
    """导出请求"""
    format: str = Field(default="csv", pattern="^(csv|json|excel)$")
    intelligence_ids: Optional[List[int]] = None
    include_fields: List[str] = Field(default_factory=lambda: [
        "id", "title", "summary", "topic", "category", "news_time",
        "collect_time", "ai_score", "quality_status", "competitors", "sources"
    ])


class TimeRangeFilter(BaseModel):
    """时间范围筛选"""
    range_type: str = Field(..., pattern="^(today|yesterday|week|month|custom)$")
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class DuplicateDetectionRequest(BaseModel):
    """重复检测请求"""
    intelligence_id: int = Field(..., description="要检测的情报ID")
    similarity_threshold: float = Field(default=0.8, ge=0.1, le=1.0, description="相似度阈值")
    check_fields: List[str] = Field(default=["title", "summary"], description="检测字段")


class DuplicateDetectionResponse(BaseModel):
    """重复检测响应"""
    intelligence_id: int
    duplicates: List[Dict[str, Any]]
    similarity_scores: Dict[int, float]
    recommendations: List[str]
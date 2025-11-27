# models/intelligence_models.py - 修复版
from __future__ import annotations
from datetime import datetime
from typing import List, Optional, Dict, Any

from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, Boolean,
    Table, ForeignKey, JSON, func
)
from sqlalchemy.orm import relationship, Mapped, mapped_column

from core.database import Base

# ===== 关联表 =====

# 情报与竞争对手的多对多关系
intelligence_competitor = Table(
    "intelligence_competitor",
    Base.metadata,
    Column("intelligence_id", Integer, ForeignKey("intelligence.id"), primary_key=True),
    Column("competitor_id", Integer, ForeignKey("competitors.id"), primary_key=True)
)

# 情报与客户的多对多关系（用于分发）
intelligence_client = Table(
    "intelligence_client",
    Base.metadata,
    Column("intelligence_id", Integer, ForeignKey("intelligence.id"), primary_key=True),
    Column("client_id", Integer, ForeignKey("clients.id"), primary_key=True),
    Column("sent_at", DateTime, default=datetime.utcnow)
)


# ===== 模型定义 =====

class Intelligence(Base):
    """情报主表 - 修复版"""
    __tablename__ = "intelligence"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # 基本信息 - 确保title不为空
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 分类信息
    topic: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    tags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 改为Text存储JSON字符串

    # 时间信息 - 添加服务器默认值
    news_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    collect_time: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now()
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now()
    )

    # AI评分信息 - 添加服务器默认值
    ai_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0.0")
    score_dimensions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 改为Text存储JSON字符串

    # 质量管理 - 添加服务器默认值
    quality_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True
    )
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    review_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 合并信息 - 添加服务器默认值
    is_merged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    merged_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    original_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 改为Text存储JSON字符串

    # 关系 - 使用lazy="select"避免异步问题
    sources: Mapped[List["IntelligenceSource"]] = relationship(
        "IntelligenceSource",
        back_populates="intelligence",
        cascade="all, delete-orphan",
        lazy="select"  # 改为select，避免selectin的异步问题
    )

    competitors: Mapped[List["Competitor"]] = relationship(
        "Competitor",
        secondary=intelligence_competitor,
        back_populates="intelligence_items",
        lazy="select"  # 改为select
    )

    def __repr__(self) -> str:
        return f"<Intelligence id={self.id} title={self.title[:50]}...>"

    # 添加属性方法来处理JSON字段
    @property
    def tags_list(self) -> List[str]:
        """获取标签列表"""
        if not self.tags:
            return []
        try:
            import json
            return json.loads(self.tags)
        except:
            return []

    @tags_list.setter
    def tags_list(self, value: List[str]):
        """设置标签列表"""
        import json
        self.tags = json.dumps(value) if value else None

    @property
    def score_dimensions_dict(self) -> Dict[str, Any]:
        """获取评分维度字典"""
        if not self.score_dimensions:
            return {}
        try:
            import json
            return json.loads(self.score_dimensions)
        except:
            return {}

    @score_dimensions_dict.setter
    def score_dimensions_dict(self, value: Dict[str, Any]):
        """设置评分维度字典"""
        import json
        self.score_dimensions = json.dumps(value) if value else None

    @property
    def original_ids_list(self) -> List[int]:
        """获取原始ID列表"""
        if not self.original_ids:
            return []
        try:
            import json
            return json.loads(self.original_ids)
        except:
            return []

    @original_ids_list.setter
    def original_ids_list(self, value: List[int]):
        """设置原始ID列表"""
        import json
        self.original_ids = json.dumps(value) if value else None


class IntelligenceSource(Base):
    """情报来源表 - 修复版"""
    __tablename__ = "intelligence_sources"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    intelligence_id: Mapped[int] = mapped_column(ForeignKey("intelligence.id"), index=True)

    url: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    domain: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    fetch_time: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now()
    )

    # 关系
    intelligence: Mapped["Intelligence"] = relationship(
        "Intelligence",
        back_populates="sources"
    )

    def __repr__(self) -> str:
        return f"<IntelligenceSource url={self.url}>"


class Competitor(Base):
    """竞争对手表 - 修复版"""
    __tablename__ = "competitors"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 改为Text存储JSON字符串

    # 关系
    intelligence_items: Mapped[List["Intelligence"]] = relationship(
        "Intelligence",
        secondary=intelligence_competitor,
        back_populates="competitors",
        lazy="select"  # 改为select
    )

    @property
    def keywords_list(self) -> List[str]:
        """获取关键词列表"""
        if not self.keywords:
            return []
        try:
            import json
            return json.loads(self.keywords)
        except:
            return []

    @keywords_list.setter
    def keywords_list(self, value: List[str]):
        """设置关键词列表"""
        import json
        self.keywords = json.dumps(value) if value else None

    def __repr__(self) -> str:
        return f"<Competitor name={self.name}>"
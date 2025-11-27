from __future__ import annotations
from datetime import datetime
from sqlalchemy import DateTime
from typing import List, Optional

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Table,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship, Mapped, mapped_column

from core.database import Base

# -----------------------------
#  多对多关联表
# -----------------------------

# 议题 <-> 信息源
topic_source_association = Table(
    "topic_source_association",
    Base.metadata,
    Column("topic_id", Integer, ForeignKey("topics.id"), primary_key=True),
    Column("source_id", Integer, ForeignKey("sources.id"), primary_key=True),
)

# 客户 <-> 议题
client_topic_association = Table(
    "client_topic_association",
    Base.metadata,
    Column("client_id", Integer, ForeignKey("clients.id"), primary_key=True),
    Column("topic_id", Integer, ForeignKey("topics.id"), primary_key=True),
)


# -----------------------------
#  模型定义
# -----------------------------
class Client(Base):
    """客户端模型"""
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    description: Mapped[str] = mapped_column(String, nullable=True)

    # 关系：客户订阅了哪些议题
    topics: Mapped[List["Topic"]] = relationship(
        "Topic",
        secondary=client_topic_association,
        back_populates="clients",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Client id={self.id} name={self.name!r}>"


class Source(Base):
    """信息源模型"""
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("domain", name="uq_sources_domain"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    domain: Mapped[str] = mapped_column(String, unique=True, index=True)
    recipe_json: Mapped[str] = mapped_column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系：该源属于哪些议题
    topics: Mapped[List["Topic"]] = relationship(
        "Topic",
        secondary=topic_source_association,
        back_populates="sources",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Source id={self.id} domain={self.domain!r}>"


class Topic(Base):
    """议题模型"""
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)

    # --- 关键修复：移除了错误的单引号 ---
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # 关系：该议题下包含哪些信息源
    sources: Mapped[List["Source"]] = relationship(
        "Source",
        secondary=topic_source_association,
        back_populates="topics",
        lazy="selectin",
    )

    # 关系：哪些客户订阅了该议题
    clients: Mapped[List["Client"]] = relationship(
        "Client",
        secondary=client_topic_association,
        back_populates="topics",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Topic id={self.id} name={self.name!r}>"
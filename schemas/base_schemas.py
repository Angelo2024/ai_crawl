# schemas/base_schemas.py

from __future__ import annotations
from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from datetime import datetime

# --- Source Schemas ---
class SourceBase(BaseModel):
    domain: str
    is_common: bool = False
    recipe_json: str

class SourceCreate(SourceBase):
    pass

class SourceSchema(SourceBase):
    id: int
    class Config:
        orm_mode = True


# --- 新增：Topic Schemas ---
class TopicBase(BaseModel):
    name: str
    description: Optional[str] = None

class TopicCreate(TopicBase):
    pass

class TopicSchema(TopicBase):
    id: int
    # 在返回一个议题时，也带上它包含的信息源列表
    sources: List[SourceSchema] = []
    class Config:
        orm_mode = True


# --- Client Schemas ---
class ClientBase(BaseModel):
    name: str
    description: Optional[str] = None

class ClientCreate(ClientBase):
    pass

class ClientSchema(ClientBase):
    id: int
    # 在返回一个客户时，也带上他订阅的议题列表
    topics: List[TopicSchema] = []
    class Config:
        orm_mode = True


# 为 Topic 模型创建一个对应的Schema
class TopicSchema(BaseModel):
    id: int
    name: str
    description: Optional[str] = None

    # 关键配置：允许Pydantic从ORM对象属性中读取数据
    model_config = ConfigDict(from_attributes=True)


# 为 Source 模型创建一个对应的Schema
class SourceSchema(BaseModel):
    id: int
    domain: str
    created_at: datetime
    updated_at: datetime
    topics: List[TopicSchema] = []  # 嵌套使用 TopicSchema

    # 关键配置：允许Pydantic从ORM对象属性中读取数据
    model_config = ConfigDict(from_attributes=True)
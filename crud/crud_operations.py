from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from urllib.parse import urlparse
from typing import Optional, List
from datetime import datetime
from zoneinfo import ZoneInfo # 推荐使用标准库

# 确保导入了所有模型
from models.base_models import Client, Source, Topic
from schemas.base_schemas import ClientCreate, SourceCreate, TopicCreate

# 创建代表北京/上海时区的 ZoneInfo 对象
beijing_tz = ZoneInfo("Asia/Shanghai")

# --- Helper Functions ---
def _parse_domain(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw: return ""
    p = urlparse(raw if "://" in raw else f"https://{raw}")
    host = p.netloc or p.path
    host = host.split("/")[0]
    if host.startswith("www."): host = host[4:]
    return host.lower()


def _boolify(v) -> bool:
    if isinstance(v, bool): return v
    if v is None: return False
    return str(v).strip().lower() in ("1", "true", "on", "yes", "y")


# --- Client CRUD ---
async def get_client(db: AsyncSession, client_id: int):
    result = await db.execute(select(Client).options(selectinload(Client.topics)).filter(Client.id == client_id))
    return result.scalar_one_or_none()


async def get_client_by_name(db: AsyncSession, name: str):
    result = await db.execute(select(Client).filter(Client.name == name))
    return result.scalar_one_or_none()


async def get_clients(db: AsyncSession, skip: int = 0, limit: int = 100):
    result = await db.execute(select(Client).order_by(Client.id).offset(skip).limit(limit))
    return result.scalars().all()


async def create_client(db: AsyncSession, client: ClientCreate):
    db_client = Client(name=client.name, description=client.description)
    db.add(db_client)
    await db.commit()
    await db.refresh(db_client)
    return db_client


async def delete_client(db: AsyncSession, client_id: int):
    client = await get_client(db, client_id)
    if client:
        await db.delete(client)
        await db.commit()
    return client


# --- Source CRUD ---
async def get_source(db: AsyncSession, source_id: int):
    result = await db.execute(select(Source).filter(Source.id == source_id))
    return result.scalar_one_or_none()


async def get_source_by_domain(db: AsyncSession, domain: str):
    result = await db.execute(select(Source).filter(Source.domain == domain))
    return result.scalar_one_or_none()


async def get_sources(db: AsyncSession, skip: int = 0, limit: int = 100):
    result = await db.execute(select(Source).order_by(Source.id).offset(skip).limit(limit))
    return result.scalars().all()


async def create_source(db: AsyncSession, source: SourceCreate):
    db_source = Source(domain=source.domain, recipe_json=source.recipe_json)
    db.add(db_source)
    await db.commit()
    await db.refresh(db_source)
    return db_source


async def update_source_recipe(db: AsyncSession, source_id: int, recipe_json: str):
    source = await get_source(db, source_id)
    if source:
        source.recipe_json = recipe_json
        await db.commit()
        await db.refresh(source)
    return source


async def upsert_source(db: AsyncSession, source_id: Optional[int], domain: str, recipe_json: str):
    if source_id:
        # 更新现有记录
        stmt = update(Source).where(Source.id == source_id).values(
            domain=domain,
            recipe_json=recipe_json,
            updated_at=datetime.now(beijing_tz)  # 手动设置更新时间
        )
        await db.execute(stmt)
        await db.commit()
        # 返回更新后的记录
        result = await db.execute(select(Source).where(Source.id == source_id))
        return result.scalar_one()
    else:
        # 创建新记录
        new_source = Source(
            domain=domain,
            recipe_json=recipe_json,
            created_at=datetime.now(beijing_tz),
            updated_at=datetime.now(beijing_tz)
        )
        db.add(new_source)
        await db.commit()
        await db.refresh(new_source)
        return new_source


async def delete_source(db: AsyncSession, source_id: int):
    source = await get_source(db, source_id)
    if source:
        await db.delete(source)
        await db.commit()
    return source


# --- 新增：Topic CRUD ---
async def get_topic(db: AsyncSession, topic_id: int):
    result = await db.execute(select(Topic).filter(Topic.id == topic_id))
    return result.scalar_one_or_none()


async def get_topics(db: AsyncSession, skip: int = 0, limit: int = 100):
    result = await db.execute(select(Topic).order_by(Topic.id).offset(skip).limit(limit))
    return result.scalars().all()


async def create_topic(db: AsyncSession, topic: TopicCreate):
    db_topic = Topic(name=topic.name, description=topic.description)
    db.add(db_topic)
    await db.commit()
    await db.refresh(db_topic)
    return db_topic


async def delete_topic(db: AsyncSession, topic_id: int):
    """根据ID删除议题"""
    topic = await get_topic(db, topic_id)
    if topic:
        await db.delete(topic)
        await db.commit()
    return topic


# --- 新增：Subscription Management ---
async def assign_topic_to_client(db: AsyncSession, client_id: int, topic_id: int):
    """为客户订阅一个议题"""
    client = await get_client(db, client_id)
    topic = await get_topic(db, topic_id)
    if client and topic and topic not in client.topics:
        client.topics.append(topic)
        await db.commit()
        await db.refresh(client)
    return client


async def unassign_topic_from_client(db: AsyncSession, client_id: int, topic_id: int):
    """为客户取消订阅一个议题"""
    client = await get_client(db, client_id)
    topic = await get_topic(db, topic_id)
    if client and topic and topic in client.topics:
        client.topics.remove(topic)
        await db.commit()
        await db.refresh(client)
    return client


async def delete_topic(db: AsyncSession, topic_id: int):
    """根据ID删除议题"""
    topic = await get_topic(db, topic_id)
    if topic:
        await db.delete(topic)
        await db.commit()
    return topic


async def update_topic(db: AsyncSession, topic_id: int, name: str, description: str):
    """根据ID更新一个议题"""
    topic = await get_topic(db, topic_id)
    if topic:
        topic.name = name
        topic.description = description
        await db.commit()
        await db.refresh(topic)
    return topic


async def set_source_topics(db: AsyncSession, source_id: int, topic_ids: List[int]):
    """为一个信息源设置其所属的所有议题，并返回预加载了议题的完整对象"""
    # 首先，使用一个简单的查询来获取 source 对象
    source = await get_source(db, source_id=source_id)
    if not source:
        return None

    # 查询所有需要关联的Topic对象
    if topic_ids:
        result = await db.execute(select(Topic).where(Topic.id.in_(topic_ids)))
        topics_to_assign = result.scalars().all()
    else:
        topics_to_assign = []

    # 直接替换关联列表
    source.topics = topics_to_assign
    await db.commit()

    # 在提交后，我们需要重新获取这个 source 对象以确保关联关系被正确加载
    refreshed_result = await db.execute(
        select(Source)
        .options(selectinload(Source.topics))  # 明确预加载 topics
        .filter(Source.id == source_id)
    )

    return refreshed_result.scalar_one_or_none()


async def get_sources_with_filter_and_sort(
    db: AsyncSession,
    q: Optional[str] = None,
    topic_id: Optional[int] = None,
    sort_by: str = 'id',
    order_by: str = 'asc'
) -> List[Source]:
    """
    获取信息源列表，支持按域名搜索、按议题筛选和排序。
    最新版：这是一个新增的、功能强大的查询函数。
    """
    # 基础查询，并使用 selectinload 预加载关联的议题，提高效率
    query = select(Source).options(selectinload(Source.topics))

    # 1. 应用域名搜索条件
    if q:
        query = query.filter(Source.domain.ilike(f"%{q.strip()}%"))

    # 2. 应用议题筛选条件
    if topic_id:
        # 通过 join 查询与特定议题关联的信息源
        query = query.join(Source.topics).filter(Topic.id == topic_id)

    # 3. 应用排序条件
    sort_column = getattr(Source, sort_by, Source.id)
    if order_by.lower() == 'desc':
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())

    result = await db.execute(query)
    return result.scalars().unique().all()


async def get_sources_count(
        db: AsyncSession,
        q: Optional[str] = None,
        topic_id: Optional[int] = None
) -> int:
    """获取符合条件的源总数"""
    query = db.query(Source)

    if q:
        query = query.filter(Source.domain.ilike(f"%{q}%"))

    if topic_id:
        # 假设你有 source_topics 关联表
        query = query.join(Source.topics).filter(Topic.id == topic_id)

    result = await query.count()
    return result


async def get_sources_with_pagination(
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20,
        q: Optional[str] = None,
        topic_id: Optional[int] = None,
        sort_by: str = "id",
        order_by: str = "asc"
) -> List[Source]:
    """获取分页的源数据"""
    query = db.query(Source)

    # 筛选条件
    if q:
        query = query.filter(Source.domain.ilike(f"%{q}%"))

    if topic_id:
        query = query.join(Source.topics).filter(Topic.id == topic_id)

    # 排序
    if hasattr(Source, sort_by):
        sort_column = getattr(Source, sort_by)
        if order_by.lower() == 'desc':
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())

    # 分页
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    # 预加载关联的 topics
    query = query.options(selectinload(Source.topics))

    result = await query.all()
    return result
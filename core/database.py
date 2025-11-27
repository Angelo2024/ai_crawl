# core/database.py - 修复版
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

DATABASE_URL = "sqlite+aiosqlite:///./intelli_scraper1.db"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=3600,
    # 关键修复：大幅增加连接池配置
    pool_size=50,          # 增加到50
    max_overflow=100,      # 增加到100
    pool_timeout=60,       # 增加超时时间
    connect_args={
        "check_same_thread": False,
        "timeout": 30
    }
)

# 使用新的 async_sessionmaker
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

Base = declarative_base()

async def get_db():
    """获取数据库session"""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception as e:
            await session.rollback()
            raise
        finally:
            await session.close()

# 添加专门的AI处理session获取器
async def get_ai_processing_db():
    """为AI处理专门创建独立的session - 修复版"""
    try:
        session = SessionLocal()
        return session
    except Exception as e:
        print(f"创建AI处理session失败: {e}")
        raise
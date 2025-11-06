# app/database/session.py
# 数据库会话管理

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database.models import engine

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """
    FastAPI 依赖项：获取数据库会话
    使用方式：
        from app.database.session import get_db
        db = next(get_db())
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

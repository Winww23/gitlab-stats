from app.database.models import Base, engine

# app/database/init_db.py

from app.database.models import Base, engine


def init_database():
    Base.metadata.create_all(bind=engine)
    print("✅ 数据库表已创建")


if __name__ == "__main__":
    init_database()

# app/database/models.py

from sqlalchemy import Column, Integer, String, DateTime, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import create_engine  # 新增：在 models.py 中创建 engine
from app.config import config

engine = create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False})  # 新增：创建 engine

Base = declarative_base()


class CommitRecord(Base):
    """
    提交记录表
    """
    __tablename__ = "commit_records"

    commit_id = Column(String(255), primary_key=True, index=True, nullable=False)  # GitLab commit hash
    project_id = Column(Integer, nullable=False)  # GitLab 项目 ID
    branch = Column(String(255), nullable=False)  # 分支名
    author_name = Column(String(255), nullable=False)  # 作者名（已映射）
    author_email = Column(String(255), nullable=False)  # 作者邮箱
    com_email = Column(String(255), nullable=False)  # 提交者邮箱
    commit_date = Column(DateTime, nullable=False)  # 提交日期（committed_date）
    additions = Column(Integer, nullable=False)  # 新增行数
    deletions = Column(Integer, nullable=False)  # 删除行数
    parent_ids = Column(String(512))  # 父提交 ID 列表（JSON 字符串存储）

    def __repr__(self):
        return f"<CommitRecord({self.commit_id[:8]}..., author={self.author_name}, +{self.additions}, -{self.deletions})>"

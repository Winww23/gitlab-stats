# app/services/sync_service.py

from typing import List
from app.utils.gitlab_client import get_commits_yesterday
from app.processor import load_mapping, process_commits
from app.database.session import SessionLocal
from app.database.models import CommitRecord, Base, engine
import datetime


def sync_yesterday_commits():
    """
    同步“昨天”的提交数据到数据库
    主流程：GitLab → 处理 → 数据库
    """
    # 1. 初始化数据库（如果表不存在则创建）
    Base.metadata.create_all(bind=engine)
    print("✅ 确保数据库表已存在")

    # 2. 加载作者映射表
    load_mapping()

    # 3. 从 GitLab 获取原始提交数据
    raw_commits = get_commits_yesterday()
    if not raw_commits:
        print("⚠️ 未获取到任何提交数据，同步结束")
        return

    # 4. 处理提交数据（过滤 + 映射 author_name）
    processed_commits = process_commits(raw_commits)
    if not processed_commits:
        print("⚠️ 处理后无有效提交，同步结束")
        return

    # 5. 写入数据库
    db = SessionLocal()
    try:
        # 统计去重：避免重复插入
        existing_commit_ids = {
            r[0] for r in db.query(CommitRecord.commit_id).all()
        }
        print(f"🔍 数据库中已有 {len(existing_commit_ids)} 条提交记录")

        new_records = []
        for commit in processed_commits:
            if commit['commit_id'] not in existing_commit_ids:
                # 构造数据库记录对象
                record = CommitRecord(
                    project_id=commit['project_id'],
                    branch=commit['branch'],
                    author_name=commit['author_name'],  # 已映射
                    author_email=commit['author_email'],
                    com_email=commit['com_email'],
                    commit_date=commit['commit_date'],
                    additions=commit['additions'],
                    deletions=commit['deletions'],
                    commit_id=commit['commit_id'],
                    parent_ids=str(commit['parent_ids'])  # 转为字符串存储
                )
                print(record)
                new_records.append(record)

        # 批量插入
        if new_records:
            db.bulk_save_objects(new_records)
            db.commit()
            print(f"✅ 成功插入 {len(new_records)} 条新提交记录")
        else:
            print("✅ 无新提交记录，无需插入")

    except Exception as e:
        db.rollback()
        print(f"❌ 数据库写入失败: {e}")
        raise
    finally:
        db.close()

    print("🎉 数据同步完成")

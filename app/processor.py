# app/processor.py

import pandas as pd
from typing import Dict, Any, List
from app.config import config

# 全局变量：存储 email -> name 映射表
email_mapping = {}


def load_mapping() -> None:
    """
    启动时加载 mapping.xlsx 文件，构建 email 到 name 的映射字典
    """
    try:
        # 读取 Excel 文件
        df = pd.read_excel(config.MAPPING_FILE)

        # 构建映射字典
        global email_mapping
        email_mapping = df.set_index('org')['res'].to_dict()

        print(f"✅ 成功加载 {len(email_mapping)} 条作者映射规则")

    except Exception as e:
        print(f"❌ 加载 mapping.xlsx 失败: {e}")
        email_mapping.clear()


def is_valid_commit(commit: Dict[str, Any]) -> bool:
    """
    判断提交是否有效（过滤 CICD、大提交、合并提交等）
    """
    author_name = str(commit.get('author_name', '')).lower()
    author_email = str(commit.get('author_email', '')).lower()
    message = str(commit.get('message', '')).lower()

    # 规则1：排除 CICD 提交
    for keyword in config.CICD_KEYWORDS:
        if keyword in author_name or keyword in author_email or keyword in message:
            return False

    # 规则2：additions <= 2000
    if commit.get('additions', 0) > config.MAX_ADDITIONS:
        return False

    # 规则3：非合并提交（parent_ids 长度 <= 1）
    if len(commit.get('parent_ids', [])) > 1:
        return False  # 是合并提交，排除

    return True


def process_commits(raw_commits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    批量处理提交记录：过滤 + 映射作者名（直接修改原始对象）

    Args:
        raw_commits: 从 GitLab API 获取的原始提交列表（会原地修改 author_name）

    Returns:
        处理后的提交列表（已过滤且 author_name 被映射）
    """
    processed_commits = []
    seen_commits_ids = set()  # 用于去重

    for commit in raw_commits:
        # 去重
        if commit['commit_id'] in seen_commits_ids:
            continue
        seen_commits_ids.add(commit['commit_id'])

        # 1. 检查是否有效
        if not is_valid_commit(commit):
            continue

        # 2. 获取author_name
        ogr = str(commit.get('author_name', '')).strip()

        # 3. 映射作者名
        if ogr and ogr in email_mapping:
            commit['author_name'] = email_mapping[ogr]

        # 4. 移除message（不存入数据库）
        commit.pop('message', None)

        # 5. 添加到结果列表
        processed_commits.append(commit)

    print(f"✅ 原始提交 {len(raw_commits)} 条，有效提交 {len(processed_commits)} 条")
    return processed_commits

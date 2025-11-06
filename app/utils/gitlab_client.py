# app/utils/gitlab_client.py

import gitlab
from datetime import datetime, timedelta
from typing import List, Dict, Any
from app.config import config
from concurrent.futures import ThreadPoolExecutor, as_completed
import time


def get_commits_yesterday() -> List[Dict[str, Any]]:
    """
    获取所有项目中 '昨天' 的提交记录（包含 additions/deletions）
    - 并发拉取项目列表（带重试）
    - 每个项目：获取所有分支，遍历每个分支拉取提交
    - 过滤合并提交、CI/CD 提交、过大的提交（additions > MAX_ADDITIONS）
    - 每成功一条提交，立即打印
    """

    # 初始化 GitLab 实例
    gl = gitlab.Gitlab(config.GITLAB_URL, private_token=config.GITLAB_TOKEN, timeout=30)
    try:
        gl.auth()
        print(f"✅ 认证成功，用户: {gl.user.username}")
    except Exception as e:
        print(f"❌ 认证失败: {e}")
        return []

    # ✅ 计算时间范围（UTC 00:00:00 ~ 23:59:59）
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    start_time = datetime.combine(yesterday, datetime.min.time())
    end_time = datetime.combine(yesterday, datetime.max.time())
    since = start_time.isoformat() + 'Z'
    until = end_time.isoformat() + 'Z'

    print(f"📅 查询时间范围: {since} 到 {until} (UTC)")

    all_commits = []
    projects = []

    # ✅ 并发拉取项目列表（带重试）
    print("📌 开始并发拉取项目列表（带重试）...")

    def fetch_project_page(page: int, max_retries=3) -> List:
        for attempt in range(max_retries):
            try:
                batch = gl.projects.list(page=page, per_page=100, archived=False, simple=True)
                if batch:
                    print(f"✅ 成功拉取第 {page} 页，{len(batch)} 个项目")
                return batch
            except gitlab.exceptions.GitlabHttpError as e:
                if e.response_code == 500 and attempt < max_retries - 1:
                    print(f"⚠️ 第 {page} 页 500 错误，第 {attempt + 1} 次重试...")
                    time.sleep(3)
                    continue
                else:
                    print(f"❌ 获取第 {page} 页失败 (HTTP {e.response_code}): {e}")
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"⚠️ 网络异常，第 {page} 页重试 {attempt + 1}/{max_retries}: {e}")
                    time.sleep(3)
                    continue
                else:
                    print(f"❌ 获取第 {page} 页失败（最终失败）: {e}")
        return []

    page = 1
    with ThreadPoolExecutor(max_workers=10) as executor:
        while True:
            # 并发请求 5 页
            futures = [executor.submit(fetch_project_page, p) for p in range(page, page + 5)]
            has_data = False
            for future in as_completed(futures):
                batch = future.result()
                if batch:
                    projects.extend(batch)
                    has_data = True

            # 如果连续 5 页都为空，说明拉取完成
            if not has_data:
                print(f"🔚 连续 5 页无数据，停止拉取项目列表")
                break

            # ✅ 实时打印累计项目数
            print(f"📌 已累计拉取 {len(projects)} 个项目...")

            page += 5

    if not projects:
        print("❌ 未获取到任何项目")
        return []

    print(f"✅ 共获取到 {len(projects)} 个项目，开始并发拉取各分支的昨日提交...")

    # ✅ 并发处理每个项目（获取其所有分支的提交）
    def fetch_commits_from_project(project) -> List[Dict]:
        commit_list = []
        project_name = getattr(project, 'path_with_namespace', project.id)

        try:
            # 获取完整项目对象（用于访问分支和提交）
            full_project = gl.projects.get(project.id)
        except Exception as e:
            print(f"❌ 无法加载项目 {project.id} ({project_name}): {e}")
            return []

        # 获取所有分支
        branches = []
        try:
            branches = full_project.branches.list(all=True)
        except Exception as e:
            print(f"⚠️ 无法获取项目 {project.id} ({project_name}) 的分支列表: {e}")

        if not branches:
            return []

        print(f"🔍 项目 [{project_name}] 共 {len(branches)} 个分支，开始检查...")

        # 遍历每个分支
        for branch_obj in branches:
            branch = branch_obj.name
            branch_commits = []

            # 获取该分支在时间范围内的提交（带重试）
            for retry in range(3):
                try:
                    branch_commits = full_project.commits.list(
                        ref_name=branch,
                        since=since,
                        until=until,
                        all=False,
                        per_page=100
                    )
                    break
                except Exception as e:
                    if retry < 2:
                        print(f"⚠️ 项目 {project.id} 分支 {branch} 提交拉取失败，重试 {retry + 1}/3")
                        time.sleep(3)
                    else:
                        print(f"❌ 项目 {project.id} 分支 {branch} 提交拉取失败: {e}")
                    branch_commits = []

            if not branch_commits:
                continue

            # 处理该分支的每一条提交
            for commit in branch_commits:
                try:
                    # 跳过合并提交
                    if hasattr(commit, 'parent_ids') and len(commit.parent_ids) > 1:
                        continue

                    # 获取提交详情
                    detail = None
                    for r in range(3):
                        try:
                            detail = full_project.commits.get(commit.id)
                            break
                        except Exception as e:
                            if r < 2:
                                time.sleep(2)
                            else:
                                print(f"⚠️ 获取提交详情失败 ({commit.id}): {e}")
                    if not detail:
                        continue

                    # 提取信息
                    author_name = detail.author_name or "Unknown"
                    author_email = (detail.author_email or "").lower()
                    committer_email = (detail.committer_email or "").lower()
                    message = (detail.message or "").strip()
                    additions = detail.stats.get('additions', 0) if detail.stats else 0
                    deletions = detail.stats.get('deletions', 0) if detail.stats else 0

                    # ✅ CI/CD 过滤（使用 config.CICD_KEYWORDS）
                    is_ci = (
                        'noreply' in committer_email or
                        'bot@' in committer_email or
                        any(kw.lower() in author_name.lower() for kw in config.CICD_KEYWORDS) or
                        any(kw.lower() in message.lower() for kw in config.CICD_KEYWORDS)
                    )
                    if is_ci:
                        continue

                    # ✅ 过大提交过滤
                    if additions > config.MAX_ADDITIONS:
                        print(f"🟡 跳过过大提交: {detail.id[:8]} | +{additions} (>{config.MAX_ADDITIONS})")
                        continue

                    # ✅ 解析提交时间
                    try:
                        commit_time_str = detail.committed_date
                        commit_time = datetime.fromisoformat(commit_time_str.replace('Z', '+00:00'))
                    except Exception as e:
                        print(f"⚠️ 时间解析失败 {commit_time_str}: {e}")
                        continue

                    # ✅ 构造数据库记录对象
                    record = {
                        'project_id': project.id,
                        'branch': branch,
                        'author_name': author_name,
                        'author_email': detail.author_email,
                        'com_email': detail.committer_email,
                        'commit_date': commit_time,
                        'additions': additions,
                        'deletions': deletions,
                        'commit_id': detail.id,
                        'parent_ids': detail.parent_ids or [],  # ← 不要 str()
                        'message': message
                    }

                    # ✅ 实时打印
                    print(
                        f"🟢 提交成功 | {author_name} | {project_name} | {branch} | {record['commit_id'][:8]} | "
                        f"+{record['additions']}/-{record['deletions']}"
                    )

                    commit_list.append(record)

                except Exception as e:
                    print(f"❌ 处理提交 {commit.id} 时异常: {e}")
                    continue

        return commit_list

    # ✅ 并发处理所有项目
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch_commits_from_project, project) for project in projects]
        for future in as_completed(futures):
            try:
                commit_list = future.result()
                all_commits.extend(commit_list)
            except Exception as e:
                print(f"❌ 项目处理任务异常: {e}")

    print(f"✅ 全部完成，共获取到 {len(all_commits)} 条有效提交记录")
    return all_commits
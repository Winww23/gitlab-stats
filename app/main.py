import uvicorn
from fastapi import FastAPI, Request, Depends, Query
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pandas.core._numba import executor
from sqlalchemy.orm import Session
from sqlalchemy import func
from apscheduler.schedulers.background import BackgroundScheduler
from starlette.responses import RedirectResponse
from fastapi import HTTPException

from app.services.sync_service import sync_yesterday_commits
from app.database.models import CommitRecord
from app.database.session import get_db
from sqlalchemy import func
from datetime import datetime, timedelta, date
import atexit
import asyncio
import os
from fastapi.responses import Response
import csv
from io import StringIO
import pandas as pd
from pytz import timezone
import re

app = FastAPI(title="GitLab 提交统计服务")

# ================================
# 📁 挂载静态文件和模板
# ================================
current_dir = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(current_dir, "static")
templates_dir = os.path.join(current_dir, "templates")

app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)

scheduler = BackgroundScheduler()


@app.get("/")
def dashboard(
        request: Request,
        days: int = Query(None),
        start_date: str = Query(None),
        end_date: str = Query(None),
        search: str = Query(None),
        page: int = Query(1, ge=1),
        db: Session = Depends(get_db)
):
    # 1. 确定时间范围
    if days is not None:
        # 目标：获取过去 N 个完整的自然日（不包含今天）
        today = datetime.now().date()  # 例如：2025-09-04
        start_date_obj = today - timedelta(days=days)  # 例如：9.4 - 1 = 9.3
        end_date_obj = today - timedelta(days=1)  # 例如：9.4 - 1 = 9.3（最后一天是昨天）

        # 设置时间边界：since = 00:00:00, until = 23:59:59
        since = datetime.combine(start_date_obj, datetime.min.time())  # 9.3 00:00:00
        until = datetime.combine(end_date_obj, datetime.max.time())  # 9.3 23:59:59
    else:
        # 自定义日期范围
        if start_date:
            since = datetime.fromisoformat(start_date)
            # 保持为当天 00:00:00（前端输入的是日期）
        else:
            since = None

        if end_date:
            # 关键：end_date 应包含当天的 23:59:59
            end_date_obj = datetime.fromisoformat(end_date)
            until = datetime.combine(end_date_obj, datetime.max.time())
        else:
            until = datetime.now()

    # 2. 查询有提交记录的人
    query = db.query(
        CommitRecord.author_name,
        func.sum(CommitRecord.additions).label("additions"),
        func.sum(CommitRecord.deletions).label("deletions")
    ).group_by(CommitRecord.author_name)

    if since:
        query = query.filter(CommitRecord.commit_date >= since)
    if until:
        query = query.filter(CommitRecord.commit_date <= until)

    result = query.all()

    # 转成字典：author_name -> {additions, deletions}
    commit_data = {
        row.author_name: {
            "additions": int(row.additions),
            "deletions": int(row.deletions)
        }
        for row in result
    }

    # 3. 读取 Excel 中的所有员工（含部门）
    all_employees = load_all_employees()

    # 4. 搜索过滤（按姓名）
    if search:
        # all_employees = [e for e in all_employees if search in search_names]
        search_terms = re.split(r'[,;\s\n]+', search)
        search_terms = [s.strip() for s in search_terms if s.strip()]
        print(f"🔍 模糊搜索关键词: {search_terms}")

        # 模糊匹配：名字中包含任意一个关键词
        all_employees = [
            e for e in all_employees
            if any(term in e["name"] for term in search_terms)
        ]

    # 5. 合并数据：所有人 + 补 0
    full_data = []
    for emp in all_employees:
        name = emp["name"]
        if name in commit_data:
            add = commit_data[name]["additions"]
            dels = commit_data[name]["deletions"]
        else:
            add, dels = 0, 0

        full_data.append({
            "author_name": name,
            "department": emp["department"],
            "additions": add,
            "deletions": dels,
            "net_lines": add - dels
        })

    # 6. 按新增行数排序
    full_data.sort(key=lambda x: x["additions"], reverse=True)

    # 7. 分页
    total = len(full_data)
    page_size = 15
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    data = full_data[start_idx:end_idx]

    if page < 1:
        page = 1

    # 计算最大页码
    max_page = (total // page_size) + (1 if total % page_size > 0 else 0)
    if max_page == 0:
        max_page = 1  # 至少一页

    # 如果当前页码大于最大页码，则重定向到最后一页
    # ✅ 防止 page 越界，自动跳转
    if page > max_page and max_page > 1:
        params = request.query_params.copy()
        params = dict(params)  # 转为普通字典
        params["page"] = max_page
        url = request.url.path + "?" + "&".join([f"{k}={v}" for k, v in params.items()])
        return RedirectResponse(url=url)

    # ✅ 正常分页
    data = full_data[start_idx:end_idx]

    return templates.TemplateResponse(
        "dashboard.html",
        context={
            "request": request,
            "data": data,
            "total": total,
            "page": page,
            "page_size": page_size,
            "max_page": max_page,
            "search": search or "",
            "days": days,
            "start_date": start_date,
            "end_date": end_date,
        }
    )


@app.get("/export")
def export_data(
        days: int = Query(None),
        start_date: str = Query(None),
        end_date: str = Query(None),
        search: str = Query(None),
        db: Session = Depends(get_db)
):
    # 时间范围
    if days is not None:
        since = datetime.now() - timedelta(days=days)
        until = datetime.now()
        data_range = f"last_{days}_days"
    else:
        since = datetime.fromisoformat(start_date) if start_date else None
        until = datetime.fromisoformat(end_date) if end_date else datetime.now()
        start_str = since.strftime("%m%d") if since else "from_start"
        end_str = until.strftime("%m%d")
        data_range = f"{start_str}_{end_str}"

    # 1. 查询有提交的人
    query = db.query(
        CommitRecord.author_name,
        func.sum(CommitRecord.additions).label("additions"),
        func.sum(CommitRecord.deletions).label("deletions")
    ).group_by(CommitRecord.author_name)

    if since:
        query = query.filter(CommitRecord.commit_date >= since)
    if until:
        query = query.filter(CommitRecord.commit_date <= until)

    result = query.all()
    commit_data = {
        row.author_name: {
            "additions": int(row.additions),
            "deletions": int(row.deletions)
        }
        for row in result
    }

    # 2. 读取所有员工（含部门）
    all_employees = load_all_employees()
    if search:
        all_employees = [e for e in all_employees if search in e["name"]]
    # 3. 合并数据

    full_data = []
    for emp in all_employees:
        name = emp["name"]
        if name in commit_data:
            add = commit_data[name]["additions"]
            dels = commit_data[name]["deletions"]
        else:
            add, dels = 0, 0

        full_data.append({
            "author_name": name,
            "department": emp["department"],
            "additions": add,
            "deletions": dels,
            "net_lines": add - dels
        })

    # 4. 排序
    full_data.sort(key=lambda x: x["additions"], reverse=True)

    # 5. 生成 CSV
    si = StringIO()
    writer = csv.writer(si, quoting=csv.QUOTE_ALL)
    writer.writerow(["排名", "姓名", "部门", "新增行数", "删除行数", "净增行数"])  # ✅ 含部门

    for idx, row in enumerate(full_data, start=1):
        writer.writerow([
            idx,
            row["author_name"],
            row["department"],
            row["additions"],
            row["deletions"],
            row["net_lines"]
        ])

    content = si.getvalue()
    si.close()

    today = datetime.now().strftime("%m%d")
    filename = f"{data_range}.csv"

    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/detail")
def detail_page(
    request: Request,
    author: str = Query(..., description="开发者姓名"),
    days: int = Query(None, ge=1, le=180),
    start_date: str = Query(None),
    end_date: str = Query(None),
    db: Session = Depends(get_db)
):
    """
    渲染开发者详情页面
    默认显示过去7天数据
    """
    if not author:
        raise HTTPException(status_code=400, detail="缺少 author 参数")

    # 检查作者是否存在
    exists = db.query(CommitRecord.author_name).filter(
        CommitRecord.author_name == author
    ).first()
    if not exists:
        # 尝试从员工列表中查找（允许查无记录者）
        employees = load_all_employees()
        if not any(e["name"] == author for e in employees):
            raise HTTPException(status_code=404, detail="未找到该开发者")

    # 设置默认时间范围
    if days is not None:
        days = min(days, 180)  # 最多180天
        end_date_obj = date.today() - timedelta(days=1)  # 昨天
        start_date_obj = end_date_obj - timedelta(days=days-1)
    else:
        if start_date:
            start_date_obj = datetime.fromisoformat(start_date).date()
        else:
            start_date_obj = date.today() - timedelta(days=6)  # 默认7天

        if end_date:
            end_date_obj = datetime.fromisoformat(end_date).date()
        else:
            end_date_obj = date.today() - timedelta(days=1)

    # 防止查询超过180天
    delta = (end_date_obj - start_date_obj).days
    if delta > 180:
        raise HTTPException(status_code=400, detail="时间范围不能超过180天")

    return templates.TemplateResponse(
        "detail.html",
        {
            "request": request,
            "author": author,
            "days": days,
            "start_date": start_date_obj.isoformat() if start_date_obj else None,
            "end_date": end_date_obj.isoformat() if end_date_obj else None,
        }
    )


@app.get("/api/trends")
def get_trends(
    author: str = Query(..., description="开发者姓名"),
    days: int = Query(None, ge=1, le=180),
    start_date: str = Query(None),
    end_date: str = Query(None),
    db: Session = Depends(get_db)
):
    """
    返回开发者每日提交趋势数据（JSON）
    格式: { "dates": [...], "additions": [...], "deletions": [...] }
    """
    if not author:
        raise HTTPException(status_code=400, detail="缺少 author 参数")

    # 时间范围处理
    if days is not None:
        days = min(days, 180)
        until = datetime.now().date() - timedelta(days=1)
        since = until - timedelta(days=days-1)
    else:
        since = datetime.fromisoformat(start_date).date() if start_date else None
        until = datetime.fromisoformat(end_date).date() if end_date else datetime.now().date() - timedelta(days=1)

    # 验证时间范围
    if not since or not until:
        since = until - timedelta(days=6)  # 默认7天

    if (until - since).days > 180:
        raise HTTPException(status_code=400, detail="时间范围不能超过180天")

    # 数据库查询：按日期聚合 additions 和 deletions
    result = (
        db.query(
            func.date(CommitRecord.commit_date).label("commit_date"),
            func.sum(CommitRecord.additions).label("additions"),
            func.sum(CommitRecord.deletions).label("deletions")
        )
        .filter(CommitRecord.author_name == author)
        .filter(func.date(CommitRecord.commit_date) >= since)
        .filter(func.date(CommitRecord.commit_date) <= until)
        .group_by(func.date(CommitRecord.commit_date))
        .order_by(func.date(CommitRecord.commit_date))
        .all()
    )

    # 转换为字典列表，确保日期连续（可选：补零）
    dates = []
    adds = []
    dels = []

    current = since
    print(current)
    result_dict = {
        datetime.strptime(r.commit_date, "%Y-%m-%d").date(): r
        for r in result
        if r.commit_date is not None
    }
    print(result_dict)

    while current <= until:
        if current in result_dict:
            row = result_dict[current]
            print(row)
            adds.append(int(row.additions))
            dels.append(int(row.deletions))
        else:
            adds.append(0)
            dels.append(0)
        dates.append(current.isoformat())
        current += timedelta(days=1)

    return {
        "dates": dates,
        "additions": adds,
        "deletions": dels
    }

    print(additions)


@app.post("/sync")
def trigger_sync():
    sync_yesterday_commits()
    return {"status": "success", "message": "数据同步任务已执行"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}


def start_scheduler():
    # 添加每天 8:00 执行的任务
    scheduler.add_job(
        func=sync_yesterday_commits,
        trigger="cron",
        hour=8,
        minute=0,
        timezone=timezone("Asia/Shanghai"),
        id="daily_sync",
        name="每日 GitLab 提交同步",
        replace_existing=True,
        misfire_grace_time=60,  # ✅ 容忍 60 秒延迟
        max_instances=1,  # ✅ 防止并发
        coalesce=True  # ✅ 错过多此只执行一次
    )
    scheduler.start()
    print("✅ 定时任务已启动：每天 08:00 同步昨日提交")
    atexit.register(lambda: scheduler.shutdown())


async def async_trigger_sync():
    print("⏰ [Scheduler] 正在提交 sync_yesterday_commits 到后台线程...")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, sync_yesterday_commits)
    print("⏰ [Scheduler] sync_yesterday_commits 提交完成")


# ================================
# ✅ 异步执行同步任务的包装函数
# ================================
async def run_sync_in_background():
    print("🔄 开始执行数据同步任务...")
    try:
        sync_yesterday_commits()
        print("✅ 数据同步任务完成")
    except Exception as e:
        print(f"❌ 数据同步任务失败: {e}")


# 添加读取excel的函数
EMPLOYEES_FILE = os.path.join(os.path.dirname(__file__), "employee.xlsx")


def load_all_employees() -> list:
    """
        从 Excel 文件读取所有员工：姓名 + 部门
        返回: [{"name": "张三", "department": "后端组"}, ...]
        """
    try:
        df = pd.read_excel(EMPLOYEES_FILE)
        df.columns = df.columns.str.strip()  # 清理列名空格

        if "姓名" not in df.columns or "部门" not in df.columns:
            raise ValueError("Excel 文件必须包含 '姓名' 和 '部门' 列")

        employees = []
        for _, row in df.iterrows():
            name = str(row["姓名"]).strip()
            dept = str(row["部门"]).strip()
            if name:
                employees.append({"name": name, "department": dept})

        print(f"✅ 成功加载 {len(employees)} 名员工: {employees}")
        return employees

    except Exception as e:
        print(f"❌ 读取 employees.xlsx 失败: {e}")
        return []


# ================================
# ✅ 修改点：启动时不阻塞，异步执行首次同步
# ================================
@app.on_event("startup")
async def startup_event():
    print("🚀 应用启动中...")
    print("✅ 应用已启动，Uvicorn 正在运行...")

    # 启动定时任务
    start_scheduler()

    # 提交首次同步任务到事件循环，不阻塞
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, sync_yesterday_commits)

    print("📌 首次数据同步任务已提交至后台执行...")


@app.on_event("shutdown")
async def shutdown_event():
    print("👋 应用正在关闭...")


# ================================
# 🧩 Jinja2 过滤器：用于分页时保留查询参数
def update_query_params(*args, **updates):
    """
    Jinja2 过滤器：更新查询参数。
    用法: {{ request.query_params | update_query(page=2, search="张") }}
    """
    if not args:
        return ""
    try:
        original = args[0]
        params = dict(original)
        params.update(updates)
        return "&".join(
            f"{k}={v}" for k, v in params.items()
            if v is not None and v != ""
        )
    except Exception as e:
        print(f"❌ update_query_params error: {e}")
        return ""


# 注册过滤器
templates.env.filters["update_query"] = update_query_params

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)

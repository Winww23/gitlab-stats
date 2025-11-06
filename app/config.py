# config.py
import os
from dataclasses import dataclass

@dataclass
class Config:
    # ---------- GitLab 配置 ----------
    GITLAB_URL: str = ""
    # 用于访问 GitLab API 的个人访问令牌（需具备 read_repository 权限）
    GITLAB_TOKEN: str = ""  # TODO: 实际使用时应通过环境变量注入

    # ---------- 数据源配置 ----------
    # 数据获取方式：固定为 "api"
    DATA_SOURCE: str = "api"

    # ---------- 映射文件配置 ----------
    # 作者邮箱与姓名映射表路径
    MAPPING_FILE: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mapping.xlsx")

    # ---------- 数据库配置 ----------
    # 数据库连接地址，使用 SQLite 作为本地存储
    DATABASE_URL: str = "sqlite:///D:/sqlfile/gitlab.db"

    # ---------- 提交过滤规则 ----------
    # CICD 提交识别关键词（不区分大小写）
    CICD_KEYWORDS: list = None
    # 最大 additions 阈值
    MAX_ADDITIONS: int = 2000

    def __post_init__(self):
        if self.CICD_KEYWORDS is None:
            self.CICD_KEYWORDS = ["ci", "cd", "jenkins", "gitlab-ci", "bot", "auto", "runner"]

# 实例化配置
config = Config()

# 测试路径（运行后删除）
print("Mapping file path:", config.MAPPING_FILE)
print("Database URL:", config.DATABASE_URL)
"""模板库仓储层：统一访问接口，后端可为 MySQL 或本地 JSON 文件（自动回退）"""
from .template_repo import TemplateRepository, get_template_repository, reset_template_repository

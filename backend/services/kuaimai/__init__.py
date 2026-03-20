"""快麦ERP API 集成模块"""

from services.kuaimai.client import KuaiMaiClient
from services.kuaimai.errors import KuaiMaiError

__all__ = ["KuaiMaiClient", "KuaiMaiError"]

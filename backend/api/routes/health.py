"""
健康检查路由

提供服务健康状态检查接口。
"""

from fastapi import APIRouter, Depends
from supabase import Client

from core.database import get_db

router = APIRouter(prefix="/health", tags=["健康检查"])


@router.get("")
async def health_check():
    """
    基础健康检查

    返回服务运行状态。
    """
    return {
        "status": "ok",
        "service": "EVERYDAYAI API",
        "version": "1.0.0",
    }


@router.get("/db")
async def database_health_check(db: Client = Depends(get_db)):
    """
    数据库连接健康检查

    检查 Supabase 数据库连接是否正常。
    """
    try:
        # 简单查询测试连接
        response = db.table("users").select("id").limit(1).execute()
        return {
            "status": "ok",
            "database": "connected",
        }
    except Exception as e:
        return {
            "status": "error",
            "database": "disconnected",
            "error": str(e),
        }

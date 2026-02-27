"""
日志配置模块

配置 loguru 日志输出：
- 控制台输出（所有级别）
- 应用日志文件（WARNING 及以上）
- 数据一致性专用日志文件
"""

import os
from pathlib import Path
from loguru import logger


def setup_logging():
    """配置应用日志"""

    # 创建日志目录
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)

    # 1. 应用日志（WARNING 及以上级别）
    logger.add(
        log_dir / "app_{time:YYYY-MM-DD}.log",
        rotation="00:00",  # 每天午夜滚动
        retention="30 days",  # 保留30天
        level="WARNING",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} | {message}",
        encoding="utf-8",
    )

    # 2. 数据一致性专用日志（所有级别）
    logger.add(
        log_dir / "data_consistency_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="90 days",  # 保留90天（重要数据）
        filter=lambda record: "DATA INCONSISTENCY" in record["message"]
                            or "Data consistency" in record["message"],
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
        encoding="utf-8",
    )

    logger.info(f"Logging configured | log_dir={log_dir.absolute()}")

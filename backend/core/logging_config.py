"""
日志配置模块

配置 loguru 日志输出：
- 控制台输出（所有级别）
- 应用日志文件（INFO 及以上）
- 数据一致性专用日志文件
"""

import warnings
from pathlib import Path
from loguru import logger

# 模块加载时立即抑制第三方库 websockets 的废弃警告（google-genai 内部使用旧版 API）
warnings.filterwarnings(
    "ignore",
    message="remove second argument of ws_handler",
    category=DeprecationWarning,
)
# 抑制 redis-py close() 废弃警告（已改用 aclose，但第三方依赖可能仍调用）
warnings.filterwarnings(
    "ignore",
    message="Call to deprecated close",
    category=DeprecationWarning,
)


def setup_logging():
    """配置应用日志"""

    # 创建日志目录
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)

    # 1. 应用日志（INFO 及以上级别）
    logger.add(
        log_dir / "app_{time:YYYY-MM-DD}.log",
        rotation="00:00",  # 每天午夜滚动
        retention="30 days",  # 保留30天
        level="INFO",
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

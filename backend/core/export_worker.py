"""DuckDB Export 子进程

独立进程执行 DuckDB -> Parquet 导出，内存隔离不影响 chat worker。

通信协议：
- stdin:  JSON 参数（一次性读完）
- stdout: 最终结果 JSON（一行，进程退出前写入）
- stderr: 进度行（每行一个 JSON，父进程实时读取推送 thinking）

进度行格式：
  {"phase":"connect"}
  {"phase":"export","elapsed":12.3,"size_kb":1234.5}
  {"phase":"done","row_count":15000,"size_kb":4567.8,"elapsed":45.2}
  {"phase":"error","message":"..."}
"""

import json
import sys
import threading
import time
from pathlib import Path


def _report(phase: str, **kwargs: object) -> None:
    """向 stderr 写一行进度 JSON（父进程实时读取）。"""
    json.dump({"phase": phase, **kwargs}, sys.stderr)
    sys.stderr.write("\n")
    sys.stderr.flush()


def _size_monitor(output_path: str, stop: threading.Event) -> None:
    """后台线程：每 5s 检查 parquet 文件大小，上报进度。

    DuckDB COPY TO 是原子操作无回调，但文件在写入过程中大小持续增长，
    通过监控文件大小可以间接反映导出进度。
    """
    t0 = time.monotonic()
    while not stop.wait(5.0):
        try:
            p = Path(output_path)
            if p.exists():
                size_kb = p.stat().st_size / 1024
                _report(
                    "export",
                    elapsed=round(time.monotonic() - t0, 1),
                    size_kb=round(size_kb, 1),
                )
        except OSError:
            pass


def main() -> None:
    params = json.loads(sys.stdin.read())
    t0 = time.monotonic()

    _report("connect")

    from core.duckdb_engine import DuckDBEngine

    engine = DuckDBEngine(
        pg_url=params["pg_url"],
        memory_limit=params.get("memory_limit", "256MB"),
        threads=params.get("threads", 2),
    )

    output_path = params["output_path"]
    stop = threading.Event()
    monitor = threading.Thread(
        target=_size_monitor, args=(output_path, stop), daemon=True,
    )
    monitor.start()

    try:
        result = engine.export_to_parquet(
            query=params["query"],
            output_path=output_path,
            timeout=params.get("timeout", 120.0),
        )
        stop.set()
        elapsed = round(time.monotonic() - t0, 1)
        _report(
            "done",
            row_count=result["row_count"],
            size_kb=round(result["size_kb"], 1),
            elapsed=elapsed,
        )
        json.dump(result, sys.stdout)
    except Exception as e:
        stop.set()
        _report("error", message=str(e)[:500])
        sys.exit(1)
    finally:
        stop.set()
        engine.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 顶层兜底：stdin 为空、JSON 解析失败、DuckDB 构造失败等
        # 都输出结构化错误，让父进程能拿到诊断信息
        _report("error", message=f"worker startup failed: {str(e)[:500]}")
        sys.exit(1)

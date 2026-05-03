"""
沙盒受限 os/shutil 模块

只暴露安全的文件系统操作，屏蔽系统命令/进程操作/环境变量。
由 _build_sandbox_globals 注入到沙盒执行环境。

安全原则：
  - 路径操作：所有接受路径的函数经过 _check_path 白名单校验
  - 只读操作：listdir/walk/stat/path.* 放行
  - 写操作：makedirs/rename 限制在白名单内
  - 删除操作：remove/unlink 需 confirm_delete 确认
  - 系统命令：system/popen/exec* 不定义（AttributeError）
  - 环境变量：environ=空dict，getenv=返回default
"""

import os as _real_os


def build_scoped_os(workspace_dir: str, staging_dir: str, output_dir: str):
    """构建受限 os 模块实例

    每次执行构造一份。confirm_delete 通过 set_confirmed_deletes 注入。

    Returns:
        (scoped_os_instance, _check_path_fn)
    """
    _ws = _real_os.path.realpath(workspace_dir)
    _allowed = [_ws]
    if staging_dir:
        _allowed.append(_real_os.path.realpath(staging_dir))
    if output_dir:
        _allowed.append(_real_os.path.realpath(output_dir))

    # staging 父目录黑名单：禁止列举/访问其他会话的临时文件
    _staging_parent = _real_os.path.realpath(
        _real_os.path.join(workspace_dir, "staging")
    )
    _denied = [_staging_parent]

    _confirmed_deletes: list[str] = []

    def _check_path(path_str) -> str:
        """路径安全校验 — 解析相对路径 + realpath + 白名单 + 黑名单"""
        path_str = str(path_str)
        if not _real_os.path.isabs(path_str):
            path_str = _real_os.path.join(_ws, path_str)
        resolved = _real_os.path.realpath(path_str)

        # 黑名单优先：staging 父目录下的路径，必须被更具体的白名单条目覆盖
        for d in _denied:
            if resolved == d or resolved.startswith(d + _real_os.sep):
                # 检查是否有非 workspace-root 的白名单精确覆盖
                if not any(
                    (resolved == a or resolved.startswith(a + _real_os.sep))
                    for a in _allowed if a != _ws
                ):
                    raise PermissionError(f"路径不在允许范围内: {path_str}")

        if not any(
            resolved == p or resolved.startswith(p + _real_os.sep)
            for p in _allowed
        ):
            raise PermissionError(f"路径不在允许范围内: {path_str}")
        return resolved

    def set_confirmed_deletes(paths: list[str]) -> None:
        """设置本次执行允许删除的文件路径列表"""
        _confirmed_deletes.clear()
        for p in paths:
            _confirmed_deletes.append(_check_path(p))

    class _ScopedOS:
        """受限 os 模块 — 对外行为像 os，内部所有 IO 操作经过路径校验"""

        # os.path 完整暴露（纯计算，无副作用）
        path = _real_os.path
        sep = _real_os.sep
        linesep = _real_os.linesep
        curdir = _real_os.curdir
        pardir = _real_os.pardir

        # 只读操作
        # 设计原则：
        #   listdir/stat — 返回值不含路径，传绝对路径无影响
        #   walk/scandir — 返回值的 root/entry.path 跟随传入路径格式
        #                  必须传原始路径（保持相对），否则 stdout 泄露绝对路径
        #                  安全检查和实际调用分开

        @staticmethod
        def listdir(path="."):
            return _real_os.listdir(_check_path(path))

        @staticmethod
        def scandir(path="."):
            _check_path(path)
            return _real_os.scandir(path)

        @staticmethod
        def walk(top=".", **kwargs):
            _check_path(top)
            return _real_os.walk(top, **kwargs)

        @staticmethod
        def stat(path):
            return _real_os.stat(_check_path(path))

        @staticmethod
        def getcwd():
            return _ws

        # 写操作（限制在白名单内）

        @staticmethod
        def makedirs(path, exist_ok=True):
            _real_os.makedirs(_check_path(path), exist_ok=exist_ok)

        @staticmethod
        def rename(src, dst):
            _real_os.rename(_check_path(src), _check_path(dst))

        # 删除操作（需 confirm_delete 确认）

        @staticmethod
        def remove(path):
            resolved = _check_path(path)
            if resolved in _confirmed_deletes:
                return _real_os.remove(resolved)
            name = _real_os.path.basename(path)
            raise PermissionError(
                f"删除操作需要用户确认。请先调 ask_user 告知用户要删除 {name}，"
                f"确认后在 code_execute 的 confirm_delete 参数传入文件名。"
            )

        @staticmethod
        def rmdir(path):
            raise PermissionError("删除目录被禁止。")

        unlink = remove

        # 环境变量屏蔽

        environ = {}

        @staticmethod
        def getenv(key, default=None):
            return default

        # system/popen/exec*/fork/kill → 不定义 → AttributeError

    scoped = _ScopedOS()
    scoped._set_confirmed_deletes = set_confirmed_deletes
    return scoped, _check_path


def build_scoped_shutil(check_path_fn):
    """构建受限 shutil — copy/move 放行，rmtree 禁止"""
    import shutil as _real_shutil

    class _ScopedShutil:
        @staticmethod
        def copy(src, dst):
            return _real_shutil.copy(check_path_fn(src), check_path_fn(dst))

        @staticmethod
        def copy2(src, dst):
            return _real_shutil.copy2(check_path_fn(src), check_path_fn(dst))

        @staticmethod
        def move(src, dst):
            return _real_shutil.move(check_path_fn(src), check_path_fn(dst))

        @staticmethod
        def rmtree(path):
            raise PermissionError("递归删除目录被禁止。")

    return _ScopedShutil()

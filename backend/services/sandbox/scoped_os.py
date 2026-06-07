"""
沙盒受限 os/shutil 模块

只暴露安全的文件系统操作,屏蔽系统命令/进程操作/环境变量。
由 _build_sandbox_globals 注入到沙盒执行环境。

设计原则(对齐 OpenAI Code Interpreter / Anthropic sandbox-runtime):
  - 安全边界 = nsjail (生产) / 信任 (本地开发),Python 层不做路径白名单
  - 路径操作:_check_path 只做相对路径解析(workspace cwd → 绝对 realpath)
  - 只读操作:listdir/walk/stat/path.* 放行
  - 写操作:makedirs/rename 由 nsjail bind mount ro/rw 决定
  - 删除操作:remove/unlink/rmdir 禁止 (UX 引导用 file_delete 工具)
  - 系统命令:system/popen/exec* 不定义 (AttributeError)
  - 环境变量:environ=空dict,getenv=返回 default

详见 docs/document/TECH_沙盒安全架构.md
"""

import os as _real_os


def build_scoped_os(workspace_dir: str, staging_dir: str, output_dir: str):
    """构建受限 os 模块实例

    每次执行构造一份。

    Returns:
        (scoped_os_instance, _check_path_fn)
    """
    _ws = _real_os.path.realpath(workspace_dir)

    def _check_path(path_str) -> str:
        """路径解析 — 相对路径 → workspace cwd → realpath。

        历史:曾在此做白名单/黑名单校验,但 Python 层挡不住攻击者
        (introspection 5 行逃逸),且会拦库内部资源 (matplotlib 字体等),
        反成 bug 源。现统一由 nsjail bind mount + clone_newnet + cgroup
        负责安全边界,Python 层只做路径解析。
        """
        path_str = str(path_str)
        if not _real_os.path.isabs(path_str):
            path_str = _real_os.path.join(_ws, path_str)
        return _real_os.path.realpath(path_str)

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

        # 删除操作：沙盒内禁止，引导用 file_delete 工具

        @staticmethod
        def remove(path):
            name = _real_os.path.basename(str(path))
            raise PermissionError(
                f"沙盒内禁止直接删除文件。请使用 file_delete 工具删除 {name}。"
            )

        @staticmethod
        def rmdir(path):
            raise PermissionError("沙盒内禁止删除目录。")

        unlink = remove

        # 环境变量屏蔽

        environ = {}

        @staticmethod
        def getenv(key, default=None):
            return default

        # system/popen/exec*/fork/kill → 不定义 → AttributeError

    scoped = _ScopedOS()
    return scoped, _check_path


def build_scoped_pathlib(scoped_os_instance):
    """构建受限 pathlib 模块 — Path 的破坏性方法走 scoped_os 检查

    用户代码 import pathlib 时拿到此受限版本；
    pandas/openpyxl 等库在模块加载时已拿到真实 pathlib,不受影响。

    拦截方法:
      - unlink → scoped_os.remove(UX 引导用 file_delete)
      - rmdir → scoped_os.rmdir(始终拒绝)
      - write_text/write_bytes → 走 check_path 路径解析,nsjail 负责权限
    """
    import pathlib as _real_pathlib

    _scoped_os = scoped_os_instance

    class _ScopedPath(_real_pathlib.PurePosixPath):
        """受限 Path — 路径计算完整保留，破坏性操作走 scoped_os"""

        # ------ 构造：让 Path("x") 返回 _ScopedPath 实例 ------

        def __new__(cls, *args, **kwargs):
            return super().__new__(cls, *args, **kwargs)

        # ------ 只读方法：委托真实 Path ------

        def _real(self):
            """转为真实 Path 执行 IO"""
            return _real_pathlib.Path(str(self))

        def exists(self):
            return self._real().exists()

        def is_file(self):
            return self._real().is_file()

        def is_dir(self):
            return self._real().is_dir()

        def stat(self):
            return self._real().stat()

        def read_text(self, encoding=None, errors=None):
            return self._real().read_text(encoding=encoding, errors=errors)

        def read_bytes(self):
            return self._real().read_bytes()

        def iterdir(self):
            for p in self._real().iterdir():
                yield _ScopedPath(p)

        def glob(self, pattern):
            for p in self._real().glob(pattern):
                yield _ScopedPath(p)

        def rglob(self, pattern):
            for p in self._real().rglob(pattern):
                yield _ScopedPath(p)

        def open(self, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
            return self._real().open(mode, buffering, encoding, errors, newline)

        def mkdir(self, mode=0o777, parents=False, exist_ok=False):
            _scoped_os.makedirs(str(self), exist_ok=exist_ok)

        def rename(self, target):
            _scoped_os.rename(str(self), str(target))
            return _ScopedPath(target)

        def replace(self, target):
            _scoped_os.rename(str(self), str(target))
            return _ScopedPath(target)

        # ------ 破坏性方法：走 scoped_os ------

        def unlink(self, missing_ok=False):
            try:
                _scoped_os.remove(str(self))
            except FileNotFoundError:
                if not missing_ok:
                    raise

        def rmdir(self):
            _scoped_os.rmdir(str(self))

        # ------ 写方法：路径安全由 io.open → scoped_open 保证 ------

        def write_text(self, data, encoding=None, errors=None, newline=None):
            return self._real().write_text(
                data, encoding=encoding, errors=errors, newline=newline,
            )

        def write_bytes(self, data):
            return self._real().write_bytes(data)

        # ------ / 运算符返回 _ScopedPath ------

        def __truediv__(self, other):
            return _ScopedPath(super().__truediv__(other))

        def __rtruediv__(self, other):
            return _ScopedPath(super().__rtruediv__(other))

        @property
        def parent(self):
            return _ScopedPath(super().parent)

        def with_name(self, name):
            return _ScopedPath(super().with_name(name))

        def with_suffix(self, suffix):
            return _ScopedPath(super().with_suffix(suffix))

        def with_stem(self, stem):
            return _ScopedPath(super().with_stem(stem))

        def resolve(self, strict=False):
            return _ScopedPath(self._real().resolve(strict=strict))

        def absolute(self):
            return _ScopedPath(self._real().absolute())

    class _ScopedPathlib:
        """受限 pathlib 模块 — Path 指向 _ScopedPath"""
        Path = _ScopedPath
        PosixPath = _ScopedPath
        PurePath = _real_pathlib.PurePath
        PurePosixPath = _real_pathlib.PurePosixPath
        PureWindowsPath = _real_pathlib.PureWindowsPath

    return _ScopedPathlib()


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
            raise PermissionError("沙盒内禁止递归删除目录。")

    return _ScopedShutil()

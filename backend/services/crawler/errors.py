"""爬虫服务异常类型"""


class CrawlerError(Exception):
    """爬虫服务基础异常"""


class CrawlerNotInstalledError(CrawlerError):
    """MediaCrawler 未安装或 venv 不存在"""


class CrawlerTimeoutError(CrawlerError):
    """爬取超时"""

    def __init__(self, timeout: int, platform: str = ""):
        self.timeout = timeout
        self.platform = platform
        super().__init__(f"爬取超时（{timeout}秒），平台：{platform}")


class CrawlerLoginRequiredError(CrawlerError):
    """平台需要登录"""

    def __init__(self, platform: str):
        self.platform = platform
        super().__init__(f"平台 {platform} 需要登录，请在 .env 中配置对应 Cookie")


class CrawlerProcessError(CrawlerError):
    """子进程执行失败"""

    def __init__(self, returncode: int, stderr: str = ""):
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"爬虫进程异常退出（code={returncode}）")

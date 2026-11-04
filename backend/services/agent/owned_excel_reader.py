"""Own Excel readers independently of exception tracebacks and cached frames."""
from contextlib import contextmanager


class ExcelReaderLease:
    # fastexcel has no public close(): dropping its last reference releases the
    # Rust file. Tracebacks may retain this lease, but never the open reader.
    def __init__(self, path):
        import fastexcel
        self._reader = fastexcel.read_excel(path)

    def __getattr__(self, name):
        if self._reader is None:
            raise RuntimeError("Excel reader is closed")
        return getattr(self._reader, name)

    def close(self):
        self._reader = None


@contextmanager
def excel_reader(path):
    reader = ExcelReaderLease(path)
    try:
        yield reader
    finally:
        reader.close()

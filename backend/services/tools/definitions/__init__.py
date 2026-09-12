"""Definition factories only; never store request contexts or handler instances."""

def build_specs():
    from .erp import build_specs as erp
    from .file_sandbox import build_specs as file_sandbox
    from .media import build_specs as media
    from .task import build_specs as task
    from .general import build_specs as general
    families = (erp(), file_sandbox(), media(), task(), general())
    return tuple(sorted(
        (spec for family in families for spec in family),
        key=lambda spec: spec.catalog_order,
    ))

"""One explicit role vocabulary for planner evidence, canvas and design checks."""

_PRODUCT = frozenset({
    'product', 'product_image', 'product_detail', 'detail', '商品', '商品图',
    '商品参考', '商品参考图', '产品', '产品图', '产品参考图', '产品摄影参考图',
    '商品细节', '商品细节图', 'product/商品图', 'detail/商品细节',
})
_STYLE = frozenset({'style', 'style_reference', '风格', '风格图', '风格参考',
                    '风格参考图', 'style_reference/风格参考'})


def canonical_role(role: str) -> str | None:
    if not isinstance(role, str):
        return None
    value = role.strip().lower()
    if value in _PRODUCT:
        return 'product'
    if value in _STYLE:
        return 'style_reference'
    return None


def is_product(role: str) -> bool:
    return canonical_role(role) == 'product'

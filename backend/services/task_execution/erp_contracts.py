"""Describe verified completion protocols; discovery never grants finality."""

def contract_for(method):
    from .native_writes import COMPLETION_METHODS
    from .erp_postconditions import METHOD,READ_METHOD,FIELD_LIMITS
    base={'version':1,'method':method,'unknown_effect_can_complete':False,'can_resend':False}
    if method in COMPLETION_METHODS:
        return {**base,'mode':'verified_sync_receipt','requires':'original persisted synchronous receipt without partial/async/failure markers'}
    if method==METHOD:
        return {**base,'mode':'acknowledged_exact_product_readback','read_method':READ_METHOD,
            'eligible_fields':sorted(FIELD_LIMITS),'requires':'host-bound exact original parameters, original ACK and fresh same-source native read; unsupported edits stay unresolved'}
    if method=='sale.purchase.strategy.calculate':
        return {**base,'mode':'provider_instance_contract_required',
            'requires':'instance identity and terminal evidence bound to this original request; global purchase.progress.query is insufficient'}
    return {**base,'mode':'provider_method_contract_required',
        'requires':'verified final receipt or instance terminal protocol bound to this original request; generic success is not completion'}


def inventory():
    from services.kuaimai.registry import (BASIC_REGISTRY,PRODUCT_REGISTRY,TRADE_REGISTRY,AFTERSALES_REGISTRY,
        WAREHOUSE_REGISTRY,PURCHASE_REGISTRY,DISTRIBUTION_REGISTRY)
    categories=dict(basic=BASIC_REGISTRY,product=PRODUCT_REGISTRY,trade=TRADE_REGISTRY,aftersales=AFTERSALES_REGISTRY,
        warehouse=WAREHOUSE_REGISTRY,purchase=PURCHASE_REGISTRY,distribution=DISTRIBUTION_REGISTRY)
    return [{'category':category,'action':action,'method':entry.method,'completion':contract_for(entry.method)}
        for category,registry in categories.items() for action,entry in sorted(registry.items()) if entry.is_write]

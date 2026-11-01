import json
from pathlib import Path
from services.task_execution.erp_contracts import contract_for,inventory
from services.task_execution.native_writes import verified_phase


def test_every_registered_write_has_an_explicit_completion_boundary():
    rows=inventory()
    assert rows and len(rows)==len({(r['category'],r['action']) for r in rows})
    for r in rows:
        assert r['completion']['method']==r['method']
        assert r['completion']['unknown_effect_can_complete'] is False and r['completion']['can_resend'] is False
        if r['completion']['mode'].startswith('provider_'):
            assert verified_phase({'phase':'returned','dispatch':{'method':r['method']},
                'response':{'method':r['method'],'body':{'success':True}}})=='accepted'


def test_unknown_new_method_and_global_progress_never_get_completion_authority():
    assert contract_for('future.provider.write')['mode']=='provider_method_contract_required'
    assert contract_for('sale.purchase.strategy.calculate')['mode']=='provider_instance_contract_required'
    assert contract_for(None)['unknown_effect_can_complete'] is False


def test_persisted_method_inventory_matches_current_registry():
    path=Path(__file__).resolve().parents[2]/'docs/document/ERP_METHOD_FINALITY_INVENTORY.json'
    assert json.loads(path.read_text())['methods']==inventory()

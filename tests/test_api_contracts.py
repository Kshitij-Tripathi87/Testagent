import pytest

pytest.importorskip("pydantic_settings")

from dhqa_api.contracts import contract_from_dict, contract_from_metadata, contract_to_dict


def sample_metadata():
    return {
        "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)",
        "name": "fact_orders",
        "columns": [
            {"name": "id", "type": "string", "nullable": False},
            {"name": "customer_id", "type": "string", "nullable": False},
            {"name": "order_ts", "type": "timestamp", "nullable": False},
        ],
        "owners": ["urn:li:corpuser:data-team"],
        "upstream_urns": [],
        "glossary_terms": [],
    }


def test_contract_round_trip_preserves_inferred_constraints():
    contract = contract_from_metadata(sample_metadata())
    restored = contract_from_dict(contract_to_dict(contract))
    assert restored.urn == contract.urn
    assert {(c.kind, c.column) for c in restored.constraints} == {
        (c.kind, c.column) for c in contract.constraints
    }


def test_contract_override_preserves_explicit_constraints():
    payload = contract_to_dict(contract_from_metadata(sample_metadata()))
    payload["constraints"] = [{"kind": "not_null", "column": "customer_id"}]
    restored = contract_from_dict(payload)
    assert len(restored.constraints) == 1
    assert restored.constraints[0].kind == "not_null"
    assert restored.constraints[0].column == "customer_id"


def test_empty_contract_is_not_replaced_by_inferred_constraints():
    payload = contract_to_dict(contract_from_metadata(sample_metadata()))
    payload["constraints"] = []
    restored = contract_from_dict(payload)
    assert restored.constraints == []

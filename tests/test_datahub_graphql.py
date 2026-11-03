import json

import pytest

pytest.importorskip("httpx")

import httpx

from dhqa_api.integrations.datahub import DataHubGraphQL


def test_search_and_dataset_metadata_are_normalized():
    calls = []

    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        if "SearchDatasets" in payload["query"]:
            response = {
                "data": {
                    "search": {
                        "total": 1,
                        "searchResults": [
                            {"entity": {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.raw_orders,PROD)", "type": "DATASET"}}
                        ],
                    }
                }
            }
        else:
            response = {
                "data": {
                    "dataset": {
                        "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.raw_orders,PROD)",
                        "name": "raw_orders",
                        "schemaMetadata": {"fields": [
                            {"fieldPath": "id", "type": "STRING", "nullable": False, "description": "Primary key"}
                        ]},
                        "ownership": {"owners": [{"owner": {"urn": "urn:li:corpuser:data-team"}}]},
                        "upstreamLineage": {"edges": []},
                        "glossaryTerms": {"terms": []},
                    }
                }
            }
        return httpx.Response(200, json=response)

    client = DataHubGraphQL("https://datahub.example", token="test-token")
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        results = client.list_datasets()
        assert len(results) == 1
        assert results[0]["name"] == "raw_orders"
        assert results[0]["columns"][0]["name"] == "id"
        assert results[0]["owners"] == ["urn:li:corpuser:data-team"]
        assert len(calls) == 2
        assert calls[0].headers["Authorization"] == "Bearer test-token"
    finally:
        client.close()


def test_graphql_errors_are_not_silently_ignored():
    client = DataHubGraphQL("https://datahub.example")
    client.client.close()
    client.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"errors": [{"message": "permission denied"}]})
        )
    )
    try:
        with pytest.raises(RuntimeError, match="permission denied"):
            client.test_connection()
    finally:
        client.close()

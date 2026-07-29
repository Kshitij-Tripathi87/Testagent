"""Thin wrapper around the DataHub MCP Server / Agent Context Kit with a
GraphQL fallback for environments where the MCP server isn't running.

Two modes, same interface — clients instantiate ``DataHubMCPClient`` and
call ``get_dataset``, ``get_lineage``, ``write_assertion``, or
``write_incident`` without caring which transport is active:

  - **MCP Server** (``DataHubMCPClient(..., transport="mcp")``):
    Calls the self-hosted ``mcp-server-datahub`` process via stdio.  This
    is the recommended path for hackathon submissions because it mirrors
    the actual Agent workflow (the agent *is* the MCP client).

  - **GraphQL** (``DataHubMCPClient(..., transport="graphql")``, default):
    Talks directly to the GMS ``/api/graphql`` endpoint using ``aiohttp``.
    Stable, well-documented, and works against any DataHub instance.

Both paths return the same ``DatasetSnapshot`` / typed results — the
public API of this module does not change.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ColumnSpec:
    name: str
    type: str
    nullable: bool = True
    description: str | None = None


@dataclass
class DatasetSnapshot:
    urn: str
    name: str
    columns: list[ColumnSpec]
    owners: list[str] = field(default_factory=list)
    upstream_urns: list[str] = field(default_factory=list)
    glossary_terms: list[str] = field(default_factory=list)
    existing_assertions: list[dict] = field(default_factory=list)


# ── GraphQL queries / mutations ───────────────────────────────────────

_GET_DATASET = """
query GetDataset($urn: String!) {
  dataset(urn: $urn) {
    urn
    name
    schemaMetadata {
      fields {
        fieldPath
        type
        nullable
        description
      }
    }
    ownership {
      owners {
        owner { urn }
      }
    }
    upstreamLineage(maxDegrees: 5) {
      edges {
        destination { urn }
      }
    }
    downstreamLineage(maxDegrees: 1) {
      edges {
        destination { urn }
      }
    }
    glossaryTerms {
      terms {
        term { urn name }
      }
    }
    assertions {
      assertions {
        urn
        info { type description }
        ... on DatasetAssertion {
          assertionUrn
          result {
            type
            nativeResults { value assertionUrn }
          }
        }
      }
    }
  }
}
"""

_GET_LINEAGE = """
query($urn: String!, $direction: LineageDirection!, $maxHops: Int!) {
  entity(urn: $urn) {
    urn
    ... on Dataset {
      __typename
      upstreamLineage(maxDegrees: $maxHops) {
        edges {
          destination { urn }
        }
        ... on DownstreamLineage {
          edges {
            destination { urn }
          }
        }
      }
    }
  }
}
"""

_GET_ENTITIES = """
query($urns: [String!]!) {
  entities(urns: $urns) {
    urn
    type
  }
}
"""

_UPSERT_ASSERTION = """
mutation($input: UpsertAssertionInput!) {
  upsertAssertion(input: $input)
}
"""

_CREATE_INCIDENT = """
mutation($input: CreateIncidentInput!) {
  createIncident(input: $input)
}
"""


class DataHubMCPClient:
    """Wraps calls to a DataHub MCP Server or GraphQL API.

    Constructed as sync factory; the ``connect`` method initialises the
    underlying aiohttp session / MCP session and should be called once
    before first use.  Context-manager support::

        async with DataHubMCPClient(...) as client:
            snap = await client.get_dataset(urn)

    If no ``gms_url`` or ``token`` is passed, falls back to the
    ``DATAHUB_GMS_URL`` / ``DATAHUB_TOKEN`` environment variables.
    """

    def __init__(
        self,
        gms_url: str | None = None,
        token: str | None = None,
        transport: str = "graphql",
    ):
        self.gms_url = gms_url or os.environ.get("DATAHUB_GMS_URL")
        self.token = token or os.environ.get("DATAHUB_TOKEN")
        self.transport = transport.lower()  # "mcp" or "graphql"
        self._session_ctx: Any | None = None
        self._connected = False

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def connect(self) -> None:
        if self._connected:
            return
        if self.transport == "graphql":
            await self._connect_graphql()
        elif self.transport == "mcp":
            await self._connect_mcp()
        else:
            raise ValueError(f"Unknown transport: {self.transport}")
        self._connected = True

    async def close(self) -> None:
        if self.transport == "graphql" and getattr(self, "_http", None):
            await self._http.close()
        elif self.transport == "mcp" and getattr(self, "_mcp_session", None):
            try:
                await self._mcp_session.__aexit__(None, None, None)
            except Exception:
                pass
        self._connected = False

    # ── transport initialisation ───────────────────────────────────

    async def _connect_graphql(self) -> None:
        try:
            import aiohttp
        except ImportError:
            raise ImportError(
                "aiohttp is required for the GraphQL transport. "
                "Install it with: pip install aiohttp>=3.9"
            )
        self._http = aiohttp.ClientSession(
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}" if self.token else "",
            },
            timeout=aiohttp.ClientTimeout(total=30),
        )

    async def _connect_mcp(self) -> None:
        try:
            from mcp import ClientSession
            from mcp.client.stdio import stdio_client
            from mcp.client.stdio import StdioServerParameters
        except ImportError:
            raise ImportError(
                "mcp is required for the MCP transport. "
                "Install with: pip install mcp"
            )
        server = stdio_client.StdioServerParameters(
            command="uvx",
            args=["mcp-server-datahub@latest"],
        )
        self._mcp_read, self._mcp_write, self._mcp_session = stdio_client(server)
        self._mcp_session = ClientSession(self._mcp_read, self._mcp_write)
        await self._mcp_session.initialize()

    # ── public methods ─────────────────────────────────────────────

    async def get_dataset(self, urn: str) -> DatasetSnapshot:
        if self.transport == "graphql":
            return await self._get_dataset_graphql(urn)
        return await self._get_dataset_mcp(urn)

    async def get_lineage(
        self, urn: str, direction: str = "upstream", max_hops: int = 5
    ) -> list[str]:
        if self.transport == "graphql":
            return await self._get_lineage_graphql(urn, direction, max_hops)
        return await self._get_lineage_mcp(urn, direction, max_hops)

    async def write_assertion(
        self, urn: str, assertion_id: str, passed: bool, details: dict
    ) -> None:
        if self.transport == "graphql":
            await self._write_assertion_graphql(urn, assertion_id, passed, details)
        else:
            await self._write_assertion_mcp(urn, assertion_id, passed, details)

    async def write_incident(
        self, urn: str, title: str, root_cause: dict
    ) -> None:
        if self.transport == "graphql":
            await self._write_incident_graphql(urn, title, root_cause)
        else:
            await self._write_incident_mcp(urn, title, root_cause)

    # ── GraphQL backing methods ────────────────────────────────────

    async def _execute_graphql(self, query: str, variables: dict | None = None) -> dict:
        if not self.gms_url:
            raise RuntimeError(
                "No GMS URL configured. Set DATAHUB_GMS_URL environment variable "
                "or pass gms_url= to the constructor."
            )
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        async with self._http.post(
            f"{self.gms_url.rstrip('/')}/api/graphql",
            json=payload,
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"DataHub GraphQL returned {resp.status}: {body}"
                )
            return await resp.json()

    async def _get_dataset_graphql(self, urn: str) -> DatasetSnapshot:
        result = await self._execute_graphql(_GET_DATASET, {"urn": urn})
        ds = result.get("data", {}).get("dataset") or {}

        columns = [
            ColumnSpec(
                name=f.get("fieldPath", ""),
                type=f.get("type", "string"),
                nullable=f.get("nullable", True),
                description=f.get("description"),
            )
            for f in ds.get("schemaMetadata", {}).get("fields", [])
        ]

        owners = [
            o.get("owner", {}).get("urn", "")
            for o in ds.get("ownership", {}).get("owners", [])
        ]

        upstream_urns = [
            e.get("destination", {}).get("urn", "")
            for e in ds.get("upstreamLineage", {}).get("edges", [])
        ]

        glossary_terms = [
            t.get("term", {}).get("name", "")
            for t in ds.get("glossaryTerms", {}).get("terms", [])
        ]

        assertion_urns = [
            a.get("assertionUrn", a.get("urn", ""))
            for a in ds.get("assertions", {}).get("assertions", [])
        ]

        return DatasetSnapshot(
            urn=ds.get("urn", urn),
            name=ds.get("name", urn.rsplit(",", 2)[0].rsplit(".", 1)[-1] if "." in urn else urn),
            columns=columns,
            owners=owners,
            upstream_urns=upstream_urns,
            glossary_terms=glossary_terms,
            existing_assertions=[{"urn": u} for u in assertion_urns],
        )

    async def _get_lineage_graphql(
        self, urn: str, direction: str = "upstream", max_hops: int = 5
    ) -> list[str]:
        result = await self._execute_graphql(_GET_LINEAGE, {"urn": urn, "direction": direction.upper(), "maxHops": max_hops})
        entity = result.get("data", {}).get("entity")
        if not entity:
            return []
            
        lineage_field = "upstreamLineage" if direction == "upstream" else "downstreamLineage"
        edges = entity.get(lineage_field, {}).get("edges", [])
        return [e.get("destination", {}).get("urn", "") for e in edges if e.get("destination", {}).get("urn")]

    async def _write_assertion_graphql(
        self, urn: str, assertion_id: str, passed: bool, details: dict
    ) -> None:
        await self._graphql(
            _UPSERT_ASSERTION,
            {
                "input": {
                    "assertionUrn": f"{urn}/assertions/{assertion_id}",
                    "entityUrn": urn,
                    "result": {
                        "type": "PASSED" if passed else "FAILED",
                        "nativeResults": details,
                    },
                }
            },
        )

    async def _write_incident_graphql(
        self, urn: str, title: str, root_cause: dict
    ) -> None:
        import json
        await self._graphql(
            _CREATE_INCIDENT,
            {
                "input": {
                    "resourceUrn": urn,
                    "title": title,
                    "description": json.dumps(root_cause),
                }
            },
        )

    # ── MCP backing methods (self-hosted mcp-server-datahub) ───────

    async def _get_dataset_mcp(self, urn: str) -> DatasetSnapshot:
        result = await self._mcp_session.call_tool("get_entities", {"urns": [urn]})
        entity = result.get("content", [{}])[0]
        return self._parse_mcp_entity(entity)

    async def _get_lineage_mcp(
        self, urn: str, direction: str = "upstream", max_hops: int = 5
    ) -> list[str]:
        result = await self._mcp_session.call_tool(
            "get_lineage",
            {"urn": urn, "direction": direction, "maxHops": max_hops},
        )
        edges = result.get("edges", [])
        return [e.get("destination", {}).get("urn", "") for e in edges]

    @staticmethod
    def _parse_mcp_entity(entity: dict) -> DatasetSnapshot:
        fields = entity.get("schemaMetadata", {}).get("fields", [])
        columns = [
            ColumnSpec(
                name=f.get("fieldPath", ""),
                type=f.get("type", ""),
                nullable=f.get("nullable", True),
                description=f.get("description"),
            )
            for f in fields
        ]
        owners = [o.get("owner", {}).get("urn", "") for o in entity.get("ownership", {}).get("owners", [])]
        upstreams = [
            e.get("destination", {}).get("urn", "")
            for e in entity.get("upstreamLineage", {}).get("edges", [])
        ]
        return DatasetSnapshot(
            urn=entity.get("urn", ""),
            name=entity.get("name", ""),
            columns=columns,
            owners=owners,
            upstream_urns=upstreams,
        )

    async def _write_assertion_mcp(
        self, urn: str, assertion_id: str, passed: bool, details: dict
    ) -> None:
        await self._mcp_session.call_tool(
            "upsert_assertion",
            {"entityUrn": urn, "assertion_id": assertion_id, "result": "PASSED" if passed else "FAILED", "details": details},
        )

    async def _write_incident_mcp(
        self, urn: str, title: str, root_cause: dict
    ) -> None:
        import json
        await self._mcp_session.call_tool(
            "create_incident",
            {"urn": urn, "title": title, "description": json.dumps(root_cause)},
        )
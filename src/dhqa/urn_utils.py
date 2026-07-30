"""Shared utilities for parsing DataHub URNs.

Six different modules previously reimplemented URN-to-name extraction with
fragile string splits (``u.split(",")[1].strip(")")``). This module is the
single canonical place for that logic, with edge cases handled and unit-tested.

The DataHub dataset URN shape is::

    urn:li:dataset:(urn:li:dataPlatform:<platform>,<name>,<env>)

e.g. ``urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)``

Owner URNs are ``urn:li:corpuser:<name>``.
"""

from __future__ import annotations


def short_name(urn: str) -> str:
    """Return the human-readable dataset name from a URN.

    ``urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)``
    -> ``fact_orders``.

    For ``orders.fact_orders`` (with a dot) we keep the final segment
    (``fact_orders``). For already-short names we return them unchanged.
    Handles missing/empty URNs gracefully (returns the input or empty string).
    """
    if not urn:
        return urn
    if "," not in urn:
        return urn
    parts = urn.split(",")
    # Prefer the second segment (the dataset name) when present.
    if len(parts) >= 2:
        name = parts[1].strip().strip(")")
        if "." in name:
            return name.split(".")[-1]
        return name
    return urn


def safe_name(urn_or_name: str) -> str:
    """Sanitise any string for use as a filename / identifier.

    Replaces every character that is not alphanumeric or ``_`` with ``_``.
    Used for generated artifact filenames and dbt model names.
    """
    if not urn_or_name:
        return ""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in urn_or_name)


def extract_platform(urn: str) -> str:
    """Pull the platform token out of a DataHub URN, lowercased.

    ``urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)``
    -> ``snowflake``. Returns ``"unknown"`` if the URN has no platform segment.
    """
    needle = "dataPlatform:"
    if needle in urn:
        return urn.split(needle, 1)[1].split(",", 1)[0].lower()
    return "unknown"


def upstream_model_name(urn: str) -> str | None:
    """Pull a dbt-friendly model name out of an upstream URN.

    ``urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)``
    -> ``fact_orders``. Returns ``None`` for malformed/empty URNs.
    """
    if not urn:
        return None
    if "," not in urn:
        return None
    parts = urn.split(",")
    if len(parts) < 2:
        return None
    name = parts[1].strip().strip(")")
    if "." in name:
        return name.split(".")[-1]
    return name


def owner_short_name(owner_urn: str) -> str:
    """Strip the ``urn:li:corpuser:`` prefix from an owner URN.

    Handles both ``urn:li:corpuser:`` and the legacy ``urn:oli:corpuser:``
    typo so historical fixtures still render cleanly.
    """
    if not owner_urn:
        return owner_urn
    for prefix in ("urn:li:corpuser:", "urn:oli:corpuser:"):
        if owner_urn.startswith(prefix):
            return owner_urn[len(prefix):]
    return owner_urn

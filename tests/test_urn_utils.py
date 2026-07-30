"""Tests for the shared URN utilities (extracted from 6+ modules)."""

from __future__ import annotations

from dhqa.urn_utils import (
    extract_platform,
    owner_short_name,
    safe_name,
    short_name,
    upstream_model_name,
)


FACT_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)"
STG_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.stg_orders,PROD)"
RAW_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.raw_orders,PROD)"


# short_name

def test_short_name_extracts_dataset_tail():
    assert short_name(FACT_URN) == "fact_orders"


def test_short_name_handles_no_dot():
    assert short_name("urn:li:dataset:(urn:li:dataPlatform:snowflake,simple,PROD)") == "simple"


def test_short_name_empty_and_no_comma():
    assert short_name("") == ""
    assert short_name("just-a-string") == "just-a-string"


def test_short_name_handles_legacy_urn_oli_typo():
    # The historical typo (urn:oli:corpuser) must not break extraction.
    assert short_name("urn:li:dataset:(urn:li:dataPlatform:snowflake,x.y,PROD)") == "y"


# safe_name

def test_safe_name_keeps_alnum_and_underscore():
    assert safe_name("fact_orders") == "fact_orders"


def test_safe_name_replaces_special_chars():
    assert safe_name("a.b/c:(d)") == "a_b_c__d_"


def test_safe_name_empty():
    assert safe_name("") == ""


# extract_platform

def test_extract_platform_snowflake():
    assert extract_platform(FACT_URN) == "snowflake"


def test_extract_platform_missing_returns_unknown():
    assert extract_platform("urn:li:corpuser:foo") == "unknown"


def test_extract_platform_case_insensitive():
    assert extract_platform("urn:li:dataset:(urn:li:dataPlatform:BigQuery,x,PROD)") == "bigquery"


# upstream_model_name

def test_upstream_model_name_extracts_tail():
    assert upstream_model_name(STG_URN) == "stg_orders"


def test_upstream_model_name_none_for_empty():
    assert upstream_model_name("") is None
    assert upstream_model_name("no-comma") is None


def test_upstream_model_name_no_dot():
    assert upstream_model_name("urn:li:dataset:(urn:li:dataPlatform:snowflake,plain,PROD)") == "plain"


# owner_short_name

def test_owner_short_name_strips_li_prefix():
    assert owner_short_name("urn:li:corpuser:data-team") == "data-team"


def test_owner_short_name_strips_legacy_oli_prefix():
    # The historical urn:oli:corpuser: typo must still render cleanly.
    assert owner_short_name("urn:oli:corpuser:data-team") == "data-team"


def test_owner_short_name_passthrough_for_unknown():
    assert owner_short_name("plain-owner-string") == "plain-owner-string"
    assert owner_short_name("") == ""

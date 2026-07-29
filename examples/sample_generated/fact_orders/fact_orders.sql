-- Auto-generated from DataHub contract for urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)
-- Owners: urn:oli:corpuser:analytics-team
-- upstream: urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.stg_orders,PROD)

with source as (
    select
    id,
    customer_id,
    amount,
    order_ts,
    tax
    from {{ source('raw', 'fact_orders') }}
)

select * from source

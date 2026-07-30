-- Auto-generated from DataHub contract for urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.stg_orders,PROD)
-- Owners: urn:li:corpuser:data-team
-- upstream: urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.raw_orders,PROD)

with source as (
    select
    id,
    customer_id,
    amount,
    order_ts,
    cleaned_amount
    from {{ source('raw', 'stg_orders') }}
)

select * from source

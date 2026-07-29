{# Auto-generated from DataHub contract by `dhqa generate`
   URN:   urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)
   Owners: analytics-team
   Glossary terms: 
   Generated columns: ['id', 'customer_id', 'amount', 'order_ts', 'tax']
   Materialised as table, contract-enforced, freshness-checked
#}
-- upstream: urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.stg_orders,PROD)
{{ config(
    materialized='incremental',
    schema='staging' if 'fact_orders'.startswith('stg_') else 'marts',
    contract={'enforced': True},
    on_schema_change='append_new_columns',
    incremental_strategy='merge',
    unique_key='id',
    tags=['dhqa-generated', 'snowflake'],
) }}

with source as (
    select
        id,
customer_id,
amount,
order_ts,
tax
    from {{ source('raw', 'fact_orders') }}
    where order_ts >= dateadd('hour', -24, current_timestamp())
),

renamed as (
    select * from source
)

select * from renamed

-- dbt schema.yml:
version: 2

models:
  - name: fact_orders
    description: >-
      Auto-generated from DataHub contract for urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD).
    config:
      contract:
        enforced: true
      on_schema_change: append_new_columns
      tags:
        - dhqa-generated
        - snowflake
    columns:
      - name: id
        description: id column
        data_tests:
          - not_null
          - unique
          - relationships(to=ref('stg_orders'), field='id')
      - name: customer_id
        description: customer_id column
        data_tests:
          - not_null
      - name: amount
        description: amount column
      - name: order_ts
        description: order_ts column
        data_tests:
          - not_null
      - name: tax
        description: tax column

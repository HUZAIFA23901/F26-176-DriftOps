{{ config(materialized='view') }}

SELECT
    id AS customer_id,
    customer_phone,
    email,
    created_at
FROM raw.raw_customers

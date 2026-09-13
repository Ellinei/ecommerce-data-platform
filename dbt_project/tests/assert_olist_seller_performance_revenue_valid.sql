-- Business invariant: a seller's total_revenue, total_freight, and
-- reviewed_orders must match aggregates computed directly from order items
-- and reviews, with no inflation from joining item-grain rows to
-- review-grain rows before aggregating. Returns offending rows — the test
-- fails if this query returns any.

with item_totals as (

    select
        seller_id,
        sum(price)          as expected_revenue,
        sum(freight_value)  as expected_freight

    from {{ ref('stg_olist_order_items') }}
    group by seller_id

),

seller_reviewed_orders as (

    select distinct
        oi.seller_id,
        r.order_id

    from {{ ref('stg_olist_order_items') }} oi
    inner join {{ ref('stg_olist_order_reviews') }} r using (order_id)
    where r.review_score is not null

),

expected_reviewed_orders as (

    select
        seller_id,
        count(order_id) as expected_reviewed_orders

    from seller_reviewed_orders
    group by seller_id

)

select
    m.seller_id,
    m.total_revenue,
    it.expected_revenue,
    m.total_freight,
    it.expected_freight,
    m.reviewed_orders,
    coalesce(ero.expected_reviewed_orders, 0) as expected_reviewed_orders

from {{ ref('mart_olist_seller_performance') }} m
left join item_totals it using (seller_id)
left join expected_reviewed_orders ero using (seller_id)
where m.total_revenue <> coalesce(it.expected_revenue, 0.00)
   or m.total_freight <> coalesce(it.expected_freight, 0.00)
   or m.reviewed_orders <> coalesce(ero.expected_reviewed_orders, 0)

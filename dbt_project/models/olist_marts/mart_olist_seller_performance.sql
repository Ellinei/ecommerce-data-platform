-- mart_olist_seller_performance: one row per seller with revenue, freight,
-- and review-score aggregates across all their order items.

with sellers as (

    select * from {{ ref('stg_olist_sellers') }}

),

order_items as (

    select * from {{ ref('stg_olist_order_items') }}

),

reviews as (

    select * from {{ ref('stg_olist_order_reviews') }}

),

-- Aggregated at the seller/order-item grain only — no review join here, so
-- an order with multiple reviews can never inflate these sums.
item_totals as (

    select
        seller_id,
        count(distinct order_id) as total_orders,
        sum(price)               as total_revenue,
        sum(freight_value)       as total_freight

    from order_items
    group by seller_id

),

-- One row per (seller, order, review): a seller's item(s) on an order link
-- it to every review left on that order, deduplicated so a seller with
-- several items on the same order doesn't multiply that order's review(s).
seller_order_reviews as (

    select distinct
        oi.seller_id,
        r.order_id,
        r.review_id,
        r.review_score

    from order_items oi
    inner join reviews r using (order_id)
    where r.review_score is not null

),

-- avg_review_score is a true per-review average (one review = one data
-- point, regardless of how many items the seller had on that order), and
-- reviewed_orders counts distinct reviewed orders, not item x review pairs.
review_totals as (

    select
        seller_id,
        avg(review_score)            as avg_review_score,
        count(distinct order_id)     as reviewed_orders

    from seller_order_reviews
    group by seller_id

),

seller_summary as (

    select
        it.seller_id,
        it.total_orders,
        it.total_revenue,
        it.total_freight,
        rt.avg_review_score,
        rt.reviewed_orders

    from item_totals it
    left join review_totals rt using (seller_id)

)

select
    s.seller_id,
    s.seller_city,
    s.seller_state,
    coalesce(ss.total_orders, 0)      as total_orders,
    coalesce(ss.total_revenue, 0.00)  as total_revenue,
    coalesce(ss.total_freight, 0.00)  as total_freight,
    ss.avg_review_score,
    coalesce(ss.reviewed_orders, 0)   as reviewed_orders

from sellers s
left join seller_summary ss using (seller_id)

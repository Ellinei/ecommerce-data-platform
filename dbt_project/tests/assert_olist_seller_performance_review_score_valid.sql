-- Business invariant: avg_review_score must be a true per-review average —
-- one review contributes once per seller, not once per item on the order it
-- was left on (an order with N items and 1 review must not weight that
-- review N times relative to a 1-item order). Returns offending rows — the
-- test fails if this query returns any (allowing a small numeric tolerance
-- for floating-point division).

with seller_reviews as (

    select distinct
        oi.seller_id,
        r.order_id,
        r.review_id,
        r.review_score

    from {{ ref('stg_olist_order_items') }} oi
    inner join {{ ref('stg_olist_order_reviews') }} r using (order_id)
    where r.review_score is not null

),

expected as (

    select
        seller_id,
        avg(review_score) as expected_avg_review_score

    from seller_reviews
    group by seller_id

)

select
    m.seller_id,
    m.avg_review_score,
    e.expected_avg_review_score

from {{ ref('mart_olist_seller_performance') }} m
inner join expected e using (seller_id)
where abs(m.avg_review_score - e.expected_avg_review_score) > 0.001

# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from app.tools import business
from app.tools.builtin.orders import query_order, query_product

USER = "u-mock"
MINE = business.list_user_orders(USER)[0]["order_id"]   # 这个用户名下真实存在的一单



async def test_query_order_deterministic_and_shaped():
    r1 = await query_order.ainvoke({"order_id": MINE, "user_id": USER})
    r2 = await query_order.ainvoke({"order_id": MINE, "user_id": USER})
    assert r1 == r2                                  # 同种子可复现
    assert r1["order_id"] == MINE
    assert r1["status"] in {"待付款", "已付款", "已发货", "已签收"}
    assert r1["tracking_no"].startswith("SF")        


async def test_query_product_names_and_determinism():
    assert query_order.name == "query_order"
    assert query_product.name == "query_product"
    p1 = await query_product.ainvoke({"product_name": "猫粮"})
    p2 = await query_product.ainvoke({"product_name": "猫粮"})
    assert p1 == p2                                   # 同种子可复现
    assert p1["product_name"] == "猫粮" and "price" in p1

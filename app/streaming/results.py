"""Business presentation data derived only from actual tool return values."""
import json
from app.streaming.security import redact

LABELS = {'query_order': '查询订单', 'list_user_orders': '查询订单', 'query_logistics': '查询物流', 'query_product': '查询商品', 'apply_refund': '申请退款', 'search_knowledge': '检索知识库', 'load_skill': '准备服务流程', 'recall_user_memory': '读取咨询记录'}


def decode_result(raw):
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {'message': str(raw)}


def business_result(name, raw):
    data = decode_result(raw)
    if not isinstance(data, dict):
        data = {'message': data}
    success = data.get('success') is not False and not data.get('error')
    block = None
    label = LABELS.get(name, name)
    summary = '处理完成' if success else '查询失败'
    if success and (data.get('orders') is not None or data.get('order')):
        orders = data.get('orders', [data['order']] if data.get('order') else [])
        fields = ('order_id', 'status', 'items_summary', 'items', 'total', 'created_at', 'carrier', 'tracking_number', 'estimated_delivery', 'refund_status', 'refund_requested_at', 'refund_reason')
        states = {'shipped': '已发货', '已发货': '已发货', 'pending': '待发货', '待发货': '待发货', 'delivered': '已签收', '已签收': '已签收', 'refund_processing': '退款中', '退款中': '退款中', '退款处理中': '退款中'}
        statuses = {states.get(row.get('status'), '') for row in orders}
        state = next(iter(statuses)) if len(statuses) == 1 else ''
        label = f'查询{state}订单' if state else '查询订单'
        title = f'查询{state}订单信息' if state else '订单信息'
        block = {'kind': 'orders', 'title': title, 'data': [{k: row[k] for k in fields if k in row} for row in orders]}
        summary = f'已查询到 {len(orders)} 笔{state}订单'
    elif success and data.get('logistics'):
        block = {'kind': 'logistics', 'title': '物流进度', 'data': data['logistics']}
        summary = '已查询到物流轨迹'
    elif success and data.get('products') is not None:
        block = {'kind': 'products', 'title': '商品信息', 'data': data['products']}
        summary = f"已查询到 {len(data['products'])} 件商品"
    elif success and name == 'search_knowledge':
        hits = data.get('results', [])
        block = {'kind': 'sources', 'title': '知识来源', 'data': hits}
        summary = f'检索到 {len(hits)} 条知识来源'
    elif name == 'apply_refund':
        block = {'kind': 'refund', 'title': '退款处理结果', 'data': data}
        summary = data.get('message', '退款申请已处理') if success else '退款申请未完成'
    elif success and data.get('message'):
        summary = str(data['message'])[:160]
    return {'success': success, 'label': label, 'summary': redact(summary), 'error': redact(str(data.get('error', ''))) if not success else None, 'block': redact(block)}

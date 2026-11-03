const names: Record<string, string> = {
  shipped: '已发货', '已发货': '已发货', pending: '待发货', '待发货': '待发货',
  delivered: '已签收', '已签收': '已签收', refund_processing: '退款中', '退款中': '退款中', '退款处理中': '退款中',
}

export function orderPresentation(orders: Array<{ status?: unknown }>) {
  const statuses = new Set(orders.map(order => names[String(order.status)] || ''))
  const state = statuses.size === 1 ? [...statuses][0] : ''
  return {
    label: state ? '查询' + state + '订单' : '查询订单',
    title: state ? '查询' + state + '订单信息' : '订单信息',
  }
}

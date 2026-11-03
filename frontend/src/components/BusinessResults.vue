<script setup lang="ts">
import type { BusinessBlock } from '../api/chat'
import { formatDateTime } from '../utils/datetime'
import { orderPresentation } from '../api/orderPresentation'
defineProps<{ blocks: BusinessBlock[] }>()
const statuses: Record<string, string> = { pending: '待发货', shipped: '已发货', delivered: '已签收', refund_processing: '退款处理中', in_transit: '运输中' }
function status(value: unknown) { return statuses[String(value)] || String(value || '暂无状态') }
function money(value: unknown) { return value != null && Number.isFinite(Number(value)) ? Number(value).toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' }) : '未提供' }
function productNames(order: any) { return order.items_summary || order.items?.map((item: any) => item.name + (item.quantity ? ' × ' + item.quantity : '')).join('、') || '未提供商品信息' }
</script>
<template>
  <div v-if="blocks.length" class="business-results">
    <section v-for="block in blocks" :key="block.id" class="business-block" :class="'business-' + block.kind">
      <header><strong>{{ block.kind === 'orders' ? orderPresentation(block.data).title : block.title }}</strong><span v-if="Array.isArray(block.data) && block.kind !== 'sources'">{{ block.data.length }} 项</span></header>
      <div v-if="block.kind === 'orders'" class="table-scroll">
        <table class="order-table"><thead><tr><th>订单 / 商品</th><th>金额</th><th>状态</th><th>下单时间</th></tr></thead>
          <tbody><tr v-for="order in block.data" :key="order.order_id"><td><strong>{{ order.order_id }}</strong><span class="order-products">{{ productNames(order) }}</span><span v-if="order.carrier" class="order-extra">{{ order.carrier }} · {{ order.tracking_number || '暂无运单号' }}</span><span v-if="order.refund_status" class="order-extra">退款审核：{{ order.refund_status }}<template v-if="order.refund_requested_at"> · {{ formatDateTime(order.refund_requested_at) }}</template></span></td><td class="amount">{{ money(order.total) }}</td><td><span class="business-status">{{ status(order.status) }}</span></td><td class="data-time" :title="formatDateTime(order.created_at)">{{ formatDateTime(order.created_at) }}</td></tr></tbody>
        </table>
      </div>
      <div v-else-if="block.kind === 'logistics'" class="logistics-card">
        <div class="logistics-heading"><strong>{{ block.data.carrier || '物流信息' }}</strong><span class="business-status">{{ status(block.data.status) }}</span></div>
        <p class="tracking-number">运单号 {{ block.data.tracking_number || '未提供' }}</p>
        <ol class="logistics-timeline"><li v-for="(event, i) in [...(block.data.events || [])].reverse()" :key="i"><time :title="formatDateTime(event.time)">{{ formatDateTime(event.time) }}</time><div><strong>{{ event.description }}</strong><span>{{ event.location }}</span></div></li></ol>
      </div>
      <div v-else-if="block.kind === 'products'" class="product-grid"><article v-for="product in block.data" :key="product.product_id" class="product-card"><span class="product-category">{{ product.category }}</span><h3>{{ product.name }}</h3><p>{{ product.description }}</p><div class="product-specs"><span v-for="(value, key) in product.specs" :key="key">{{ key }}：{{ value }}</span></div><footer><strong>{{ money(product.price) }}</strong><span v-if="product.stock != null">库存 {{ product.stock }}</span></footer><small v-if="String(product.product_id).startsWith('MOCK-')">工具返回的模拟商品</small></article></div>
      <div v-else-if="block.kind === 'refund'" class="refund-card"><p>{{ block.data.message || block.data.error || '暂无处理详情' }}</p><dl><template v-if="block.data.amount != null"><dt>退款金额</dt><dd>{{ money(block.data.amount) }}</dd></template><template v-if="block.data.status || block.data.refund_status"><dt>审核状态</dt><dd>{{ status(block.data.refund_status || block.data.status) }}</dd></template></dl></div>
      <div v-else-if="block.kind === 'sources'" class="source-list"><p v-if="!block.data.length" class="source-empty">本次检索未返回可引用的知识来源。</p><details v-for="(hit, i) in block.data" :key="i"><summary>{{ hit.doc || hit.source || '知识文档' }}<span v-if="hit.section"> · {{ hit.section }}</span></summary><p>{{ hit.text }}</p></details></div>
      <pre v-else>{{ JSON.stringify(block.data, null, 2) }}</pre>
    </section>
  </div>
</template>

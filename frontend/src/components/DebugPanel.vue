<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { X, RefreshCw, Pin } from 'lucide-vue-next'
import { chatApi, friendlyError } from '../api/chat'
import MarkdownMessage from './MarkdownMessage.vue'
const props = defineProps<{ requestId?: string }>()
defineEmits<{ close: [] }>()
const trace = ref<any>(null)
const error = ref('')
const baseline = ref<any>(null)
let generation = 0
let timer: ReturnType<typeof setTimeout> | undefined
const models = computed(() => trace.value?.events.filter((event: any) => event.type === 'model' && event.state === 'succeeded') || [])
const calls = computed(() => trace.value?.events.filter((event: any) => event.type === 'tool' && event.state === 'running') || [])
const rounds = computed(() => Math.max(0, ...models.value.filter((m: any) => m.phase === 'react').map((m: any) => m.round)))
const tokens = computed(() => models.value.some((m: any) => m.usage) ? models.value.reduce((sum: number, m: any) => sum + (m.usage?.total_tokens || 0), 0) : null)
const memory = computed(() => trace.value?.events.filter((event: any) => event.type === 'memory').at(-1))
const prompts = computed(() => trace.value?.events.filter((event: any) => event.type === 'model' && event.messages) || [])
const stateNames: Record<string, string> = { queued: '等待处理', running: '执行中', succeeded: '已完成', failed: '执行失败', waiting: '准备调用' }
async function load() {
  if (!props.requestId) { trace.value = null; return }
  const current = ++generation
  clearTimeout(timer)
  try {
    const result = await chatApi.debug(props.requestId)
    if (current !== generation) return
    trace.value = result
    error.value = ''
    if (['queued', 'running'].includes(result.status)) timer = setTimeout(load, 1000)
  } catch (e) { if (current === generation) error.value = friendlyError(e) }
}
watch(() => props.requestId, () => { trace.value = null; error.value = ''; void load() }, { immediate: true })
onUnmounted(() => { generation++; clearTimeout(timer) })
function snapshot() { baseline.value = JSON.parse(JSON.stringify({ request_id: trace.value.request_id, elapsed_ms: trace.value.elapsed_ms, tools: calls.value.length, rounds: rounds.value, tokens: tokens.value, status: trace.value.status })) }
</script>
<template>
  <aside class="debug-panel" aria-label="执行调试面板">
    <header class="debug-header"><div><strong>执行调试</strong><span>仅本机可见 · 敏感字段已脱敏</span></div><button class="icon-button" aria-label="关闭调试面板" @click="$emit('close')"><X :size="18" /></button></header>
    <div class="debug-scroll">
      <p v-if="error" class="debug-error">{{ error }}</p>
      <p v-if="!requestId" class="debug-empty">选择一条新回复的“执行详情”查看记录。历史记录未采集的指标不会补造。</p>
      <template v-if="trace">
        <section><div class="debug-section-title"><h2>执行概览</h2><button aria-label="刷新调试记录" @click="load"><RefreshCw :size="13" />刷新</button></div><code class="request-id">{{ trace.request_id }}</code><dl class="debug-metrics"><div><dt>状态</dt><dd>{{ stateNames[trace.status] || trace.status }}</dd></div><div><dt>总耗时</dt><dd>{{ trace.elapsed_ms != null ? (trace.elapsed_ms / 1000).toFixed(2) + 's' : '执行中' }}</dd></div><div><dt>模型 / 路由轮数</dt><dd>{{ rounds }}</dd></div><div><dt>工具调用</dt><dd>{{ calls.length }}</dd></div></dl></section>
        <section><h2>意图识别</h2><p>{{ trace.user_message }}</p><code>{{ trace.output?.reply?.intent || '尚未返回' }}</code></section>
        <section><h2>执行顺序</h2><ol class="debug-timeline"><li v-for="(event, i) in trace.events.filter((e: any) => ['model', 'tool', 'status'].includes(e.type))" :key="i"><time>{{ ((event.at_ms || 0) / 1000).toFixed(2) }}s</time><div><strong>{{ event.type === 'model' ? (event.phase === 'answer' ? '最终回复生成' : '模型 / 路由调用') : event.label || '处理状态' }}</strong><span>{{ stateNames[event.state] || event.text || event.summary }}</span><details v-if="event.type === 'tool'"><summary>{{ event.name }} · {{ event.source || '来源未返回' }}</summary><pre v-if="event.args">{{ JSON.stringify(event.args, null, 2) }}</pre><pre v-if="event.result">{{ event.result }}</pre><p v-if="event.error" class="debug-error">{{ event.error }}</p></details></div></li></ol></section>
        <section><h2>模型统计</h2><p class="debug-muted">Token 仅在模型服务返回 usage 时显示。</p><dl><dt>模型</dt><dd>{{ [...new Set(models.map((m: any) => m.model))].join('、') || '尚未返回' }}</dd><dt>Token</dt><dd>{{ tokens ?? '服务未返回' }}</dd></dl><div v-for="model in models" :key="model.id" class="debug-model-row"><span>{{ model.phase === 'answer' ? '回复' : '决策' }} #{{ model.round }}</span><span>{{ (model.elapsed_ms / 1000).toFixed(2) }}s</span></div></section>
        <section><h2>Memory</h2><dl v-if="memory"><dt>历史消息数量</dt><dd>{{ memory.history_count }}</dd><dt>本轮摘要更新</dt><dd>{{ memory.summary_changed ? '是' : '否' }}</dd><dt>摘要长度</dt><dd>{{ memory.summary_length }} 字符</dd></dl><p v-else class="debug-muted">本轮尚未返回记忆统计。</p></section>
        <section><h2>最终输出</h2><MarkdownMessage v-if="trace.output" :content="trace.output.assistant_message.content" /><p v-else class="debug-muted">尚未完成。</p></section>
        <section><h2>提示词检查</h2><p class="debug-muted">展示已采集的请求消息，不展示隐藏推理内容。</p><details v-for="(model, i) in prompts" :key="model.id"><summary>请求 {{ i + 1 }} · {{ model.phase }}</summary><pre>{{ JSON.stringify(model.messages, null, 2) }}</pre></details></section>
        <section v-if="trace.error"><h2>请求排障</h2><code>{{ trace.error.code }}</code><p>{{ trace.error.message }}</p><p class="debug-muted">详细异常按上方请求 ID 查询本机服务日志。</p></section>
        <section><div class="debug-section-title"><h2>运行对比</h2><button :disabled="trace.status !== 'succeeded'" @click="snapshot"><Pin :size="13" />设为对比基线</button></div><p v-if="!baseline" class="debug-muted">固定本轮结果，再打开另一条回复进行比较。</p><table v-else class="debug-compare"><thead><tr><th>指标</th><th>基线</th><th>当前</th></tr></thead><tbody><tr><td>耗时</td><td>{{ baseline.elapsed_ms }}ms</td><td>{{ trace.elapsed_ms ?? '执行中' }}ms</td></tr><tr><td>工具调用</td><td>{{ baseline.tools }}</td><td>{{ calls.length }}</td></tr><tr><td>模型轮数</td><td>{{ baseline.rounds }}</td><td>{{ rounds }}</td></tr><tr><td>Token</td><td>{{ baseline.tokens ?? '未返回' }}</td><td>{{ tokens ?? '未返回' }}</td></tr></tbody></table></section>
      </template>
    </div>
  </aside>
</template>

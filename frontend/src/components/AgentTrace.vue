<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  Brain,
  Check,
  CheckCircle2,
  ChevronDown,
  Copy,
  GitBranch,
  Loader2,
  Sparkles,
  Terminal,
  Wrench,
} from 'lucide-vue-next'
import type { AgentStep } from '../api/chat'

const props = withDefaults(
  defineProps<{
    steps?: AgentStep[]
    loading?: boolean
    defaultOpen?: boolean
  }>(),
  {
    steps: () => [],
    loading: false,
    defaultOpen: false,
  },
)

// 工具名称中文映射
const toolLabels: Record<string, string> = {
  query_order: '查询订单详情',
  query_product: '查询商品信息',
  query_logistics: '查询物流轨迹',
  search_knowledge: '检索政策知识库',
  list_user_orders: '查询用户订单列表',
  apply_refund: '申请退款',
  recall_user_memory: '检索用户记忆',
  load_skill: '加载标准技能',
}

// 展开/收起状态（执行中默认展开，否则按 defaultOpen）
const isOpen = ref(props.loading || props.defaultOpen)

// 当进入 loading 状态时，自动展开查看思考过程
watch(
  () => props.loading,
  (val) => {
    if (val) isOpen.value = true
  },
)

// 提取当前使用过的独立工具名称
const usedTools = computed(() => {
  const tools = new Set<string>()
  for (const s of props.steps || []) {
    if (s.tool_name) tools.add(s.tool_name)
  }
  return Array.from(tools)
})

// 复制状态提示
const copiedMap = ref<Record<string, boolean>>({})

async function copyText(key: string, text: string) {
  try {
    await navigator.clipboard.writeText(text)
    copiedMap.value[key] = true
    setTimeout(() => {
      copiedMap.value[key] = false
    }, 1800)
  } catch {
    // 降级复制
    const ta = document.createElement('textarea')
    ta.value = text
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    copiedMap.value[key] = true
    setTimeout(() => {
      copiedMap.value[key] = false
    }, 1800)
  }
}

// 格式化展示参数或结果
function formatPayload(raw: any): string {
  if (raw === undefined || raw === null) return ''
  if (typeof raw === 'object') {
    return JSON.stringify(raw, null, 2)
  }
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw)
      return JSON.stringify(parsed, null, 2)
    } catch {
      return raw
    }
  }
  return String(raw)
}

function formatTime(isoString?: string): string {
  if (!isoString) return ''
  try {
    const d = new Date(isoString)
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return ''
  }
}
</script>

<template>
  <div v-if="(steps && steps.length > 0) || loading" class="codex-trace-card" :class="{ 'is-loading': loading }">
    <!-- Header -->
    <div class="trace-header" role="button" tabindex="0" @click="isOpen = !isOpen" @keydown.enter="isOpen = !isOpen">
      <div class="header-left">
        <div class="trace-icon-badge" :class="{ spinning: loading }">
          <Loader2 v-if="loading" :size="15" />
          <Brain v-else :size="15" />
        </div>
        <div class="header-titles">
          <span class="trace-title">思考与工具执行链</span>
          <span v-if="steps && steps.length" class="trace-count-pill">{{ steps.length }} 步执行</span>
        </div>

        <!-- 工具标签摘要 -->
        <div v-if="usedTools.length" class="tool-tags-summary">
          <span v-for="t in usedTools" :key="t" class="tool-summary-tag">
            <Wrench :size="11" />
            <span>{{ toolLabels[t] || t }}</span>
          </span>
        </div>
      </div>

      <div class="header-right">
        <span v-if="loading" class="trace-status-pill running">
          <span class="pulse-dot"></span>
          <span>执行中...</span>
        </span>
        <span v-else class="trace-status-pill finished">
          <CheckCircle2 :size="13" />
          <span>已完成</span>
        </span>

        <button class="toggle-button" :aria-label="isOpen ? '收起详情' : '展开详情'" @click.stop="isOpen = !isOpen">
          <span class="toggle-text">{{ isOpen ? '收起' : '展开' }}</span>
          <ChevronDown class="chevron" :class="{ rotated: isOpen }" :size="15" />
        </button>
      </div>
    </div>

    <!-- Timeline Body (Codex style) -->
    <div v-show="isOpen" class="trace-timeline-body">
      <div class="trace-timeline-track">
        <div
          v-for="(step, index) in steps"
          :key="step.id || index"
          class="timeline-node"
          :class="`node-${step.type}`"
        >
          <!-- Node Dot -->
          <div class="node-marker">
            <Brain v-if="step.type === 'thought'" :size="13" />
            <Wrench v-else-if="step.type === 'action'" :size="13" />
            <Terminal v-else-if="step.type === 'observation'" :size="13" />
            <GitBranch v-else-if="step.type === 'route'" :size="13" />
            <Sparkles v-else :size="13" />
          </div>

          <!-- Step Content Container -->
          <div class="node-content">
            <!-- Step Header -->
            <div class="node-header">
              <div class="node-title-group">
                <span class="node-type-label">
                  {{
                    step.type === 'thought'
                      ? '思考过程'
                      : step.type === 'action'
                        ? '调用工具'
                        : step.type === 'observation'
                          ? '工具观测'
                          : step.type === 'route'
                            ? '路由分发'
                            : '执行步骤'
                  }}
                </span>
                <span v-if="step.tool_name" class="node-tool-name">
                  {{ toolLabels[step.tool_name] || step.tool_name }}
                  <code>{{ step.tool_name }}</code>
                </span>
                <span v-if="step.agent_name" class="node-agent-name">
                  {{ step.agent_name }}
                </span>
              </div>
              <span v-if="step.created_at" class="node-timestamp">{{ formatTime(step.created_at) }}</span>
            </div>

            <!-- Step Detail: Thought -->
            <div v-if="step.type === 'thought'" class="thought-box">
              <div class="thought-bubble">
                {{ step.content }}
              </div>
            </div>

            <!-- Step Detail: Action -->
            <div v-else-if="step.type === 'action'" class="action-box">
              <div class="code-header">
                <span class="code-title">输入参数 (Arguments)</span>
                <button
                  class="copy-btn"
                  :title="copiedMap[`action_${step.id}`] ? '已复制' : '复制代码'"
                  @click="copyText(`action_${step.id}`, formatPayload(step.tool_args))"
                >
                  <Check v-if="copiedMap[`action_${step.id}`]" :size="13" />
                  <Copy v-else :size="13" />
                  <span>{{ copiedMap[`action_${step.id}`] ? '已复制' : '复制' }}</span>
                </button>
              </div>
              <pre class="code-block"><code>{{ formatPayload(step.tool_args) }}</code></pre>
            </div>

            <!-- Step Detail: Observation -->
            <div v-else-if="step.type === 'observation'" class="observation-box">
              <div class="code-header observation-header">
                <span class="code-title">返回数据 (Observation)</span>
                <button
                  class="copy-btn"
                  :title="copiedMap[`obs_${step.id}`] ? '已复制' : '复制结果'"
                  @click="copyText(`obs_${step.id}`, formatPayload(step.tool_result || step.content))"
                >
                  <Check v-if="copiedMap[`obs_${step.id}`]" :size="13" />
                  <Copy v-else :size="13" />
                  <span>{{ copiedMap[`obs_${step.id}`] ? '已复制' : '复制' }}</span>
                </button>
              </div>
              <pre class="code-block scrollable-block"><code>{{ formatPayload(step.tool_result || step.content) }}</code></pre>
            </div>

            <!-- Step Detail: Route -->
            <div v-else-if="step.type === 'route'" class="route-box">
              <div class="route-badge">
                <GitBranch :size="14" />
                <span>{{ step.content }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Live Step Indicator when loading -->
        <div v-if="loading" class="timeline-node node-loading">
          <div class="node-marker loading-spinner">
            <Loader2 :size="13" class="spin" />
          </div>
          <div class="node-content">
            <div class="live-pulse-text">
              <span>Agent 正在深度推理并执行工具...</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.codex-trace-card {
  margin: 6px 0 14px;
  border-radius: 11px;
  border: 1px solid #e5e9f2;
  background: #fbfcfe;
  overflow: hidden;
  transition: all 0.2s ease;
  box-shadow: 0 1px 3px rgba(30, 41, 59, 0.03);
}

.codex-trace-card:hover {
  border-color: #d7deec;
}

.codex-trace-card.is-loading {
  border-color: #c7d2fe;
  background: #fdfefe;
}

/* Header */
.trace-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
  gap: 12px;
  background: #f8fafc;
  border-bottom: 1px solid transparent;
}

.codex-trace-card:not(:has(.trace-timeline-body[style*="display: none"])) .trace-header {
  border-bottom-color: #edf1f7;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  min-width: 0;
}

.trace-icon-badge {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: #eef2ff;
  color: #4f46e5;
  display: grid;
  place-items: center;
  flex-shrink: 0;
}

.trace-icon-badge.spinning {
  color: #4338ca;
  background: #e0e7ff;
}

.trace-icon-badge.spinning svg {
  animation: spin 1s linear infinite;
}

.header-titles {
  display: flex;
  align-items: center;
  gap: 8px;
}

.trace-title {
  font-size: 12px;
  font-weight: 700;
  color: #1e293b;
  letter-spacing: 0.2px;
}

.trace-count-pill {
  font-size: 10px;
  font-weight: 600;
  color: #475569;
  background: #e2e8f0;
  padding: 2px 7px;
  border-radius: 100px;
}

.tool-tags-summary {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.tool-summary-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  font-weight: 500;
  color: #4338ca;
  background: #eef2ff;
  border: 1px solid #e0e7ff;
  padding: 2px 8px;
  border-radius: 6px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.trace-status-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 100px;
}

.trace-status-pill.running {
  color: #2563eb;
  background: #eff6ff;
}

.pulse-dot {
  width: 6px;
  height: 6px;
  background: #3b82f6;
  border-radius: 50%;
  animation: pulse 1s infinite alternate;
}

.trace-status-pill.finished {
  color: #059669;
  background: #ecfdf5;
}

.toggle-button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 0;
  background: transparent;
  color: #64748b;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 6px;
  border-radius: 6px;
  transition: all 0.15s;
}

.toggle-button:hover {
  background: #e2e8f0;
  color: #1e293b;
}

.chevron {
  transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

.chevron.rotated {
  transform: rotate(180deg);
}

/* Timeline Body */
.trace-timeline-body {
  padding: 14px 16px 14px 14px;
  background: #ffffff;
}

.trace-timeline-track {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* Vertical line connecting nodes */
.trace-timeline-track::before {
  content: '';
  position: absolute;
  top: 10px;
  bottom: 12px;
  left: 13px;
  width: 2px;
  background: #e5e9f2;
  z-index: 1;
}

.timeline-node {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  z-index: 2;
}

.node-marker {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  flex-shrink: 0;
  box-shadow: 0 0 0 3px #ffffff;
}

/* Marker color variants */
.node-thought .node-marker {
  background: #f0fdf4;
  color: #16a34a;
  border: 1px solid #bbf7d0;
}

.node-action .node-marker {
  background: #eff6ff;
  color: #2563eb;
  border: 1px solid #bfdbfe;
}

.node-observation .node-marker {
  background: #faf5ff;
  color: #9333ea;
  border: 1px solid #e9d5ff;
}

.node-route .node-marker {
  background: #fffbeb;
  color: #d97706;
  border: 1px solid #fde68a;
}

.node-loading .node-marker {
  background: #eff6ff;
  color: #3b82f6;
  border: 1px solid #bfdbfe;
}

.node-content {
  flex: 1;
  min-width: 0;
}

.node-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  gap: 8px;
}

.node-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.node-type-label {
  font-size: 11px;
  font-weight: 700;
  color: #334155;
}

.node-tool-name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 600;
  color: #1e293b;
}

.node-tool-name code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 10px;
  font-weight: 500;
  color: #475569;
  background: #f1f5f9;
  padding: 1px 6px;
  border-radius: 4px;
}

.node-agent-name {
  font-size: 10px;
  color: #b45309;
  background: #fef3c7;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 600;
}

.node-timestamp {
  font-size: 10px;
  color: #94a3b8;
  font-variant-numeric: tabular-nums;
}

/* Detail Boxes */
.thought-box {
  margin-top: 4px;
}

.thought-bubble {
  background: #f8fafc;
  border-left: 3px solid #6366f1;
  border-radius: 4px 8px 8px 4px;
  padding: 9px 13px;
  font-size: 12px;
  line-height: 1.65;
  color: #475569;
  white-space: pre-wrap;
  word-break: break-word;
}

.action-box,
.observation-box {
  margin-top: 5px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #0f172a;
  color: #f8fafc;
  overflow: hidden;
}

.code-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 5px 10px;
  background: #1e293b;
  border-bottom: 1px solid #334155;
}

.code-title {
  font-size: 10px;
  font-weight: 600;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 0;
  background: #334155;
  color: #cbd5e1;
  font-size: 10px;
  font-weight: 500;
  padding: 2px 7px;
  border-radius: 4px;
  transition: all 0.15s;
}

.copy-btn:hover {
  background: #475569;
  color: #ffffff;
}

.code-block {
  margin: 0;
  padding: 10px 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  line-height: 1.6;
  color: #e2e8f0;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.scrollable-block {
  max-height: 190px;
  overflow-y: auto;
}

.route-box {
  margin-top: 4px;
}

.route-badge {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 6px 11px;
  border-radius: 7px;
  background: #fffbeb;
  border: 1px solid #fef3c7;
  color: #b45309;
  font-size: 11.5px;
  font-weight: 600;
}

.live-pulse-text {
  font-size: 11.5px;
  color: #3b82f6;
  font-weight: 500;
  display: flex;
  align-items: center;
  padding: 4px 0;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.spin {
  animation: spin 1s linear infinite;
}
</style>

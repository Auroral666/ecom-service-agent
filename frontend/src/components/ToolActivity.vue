<script setup lang="ts">
import { Check, ChevronDown, CircleAlert, Loader2, Clock3 } from 'lucide-vue-next'
import type { ToolEvent } from '../api/chat'
defineProps<{ tools: ToolEvent[] }>()
</script>
<template>
  <div v-if="tools.length" class="tool-activity" aria-label="业务处理过程">
    <details v-for="tool in tools" :key="tool.id" class="tool-row" :class="tool.state">
      <summary>
        <Check v-if="tool.state === 'succeeded'" :size="15" />
        <CircleAlert v-else-if="tool.state === 'failed'" :size="15" />
        <Clock3 v-else-if="tool.state === 'waiting'" :size="15" />
        <Loader2 v-else :size="15" class="spin" />
        <span class="tool-label">{{ tool.label }}</span>
        <span class="tool-summary">{{ tool.summary }}</span>
        <span v-if="tool.elapsed_ms != null" class="tool-duration">{{ (tool.elapsed_ms / 1000).toFixed(2) }}s</span>
        <span v-if="tool.state === 'failed'" class="tool-reason">查看原因 <ChevronDown :size="12" /></span>
      </summary>
      <p v-if="tool.error">{{ tool.error }}</p>
      <p v-else>{{ tool.state === 'running' || tool.state === 'waiting' ? '正在处理，请稍候。' : tool.summary }}</p>
    </details>
  </div>
</template>

import type { BusinessBlock, ChatMessage, ToolEvent, Presentation } from './chat'
import { orderPresentation } from './orderPresentation'

// Older conversations reuse actual tool observations; text is never guessed into cards.
export function presentation(message: ChatMessage): Presentation {
  if (message.presentation) {
    const view = message.presentation
    return { ...view, tools: view.tools.map(tool => {
      const block = view.blocks.find(item => item.id === tool.id && item.kind === 'orders')
      return block ? { ...tool, label: orderPresentation(block.data).label } : tool
    }) }
  }
  const blocks: BusinessBlock[] = []
  for (const step of message.steps || []) {
    if (step.type !== 'observation' || !step.tool_result) continue
    try {
      const result = JSON.parse(step.tool_result)
      if (result.success === false || result.error) continue
      if (result.orders || result.order) blocks.push({ id: step.id, kind: 'orders', title: '订单信息', data: result.orders || [result.order] })
      else if (result.logistics) blocks.push({ id: step.id, kind: 'logistics', title: '物流进度', data: result.logistics })
      else if (result.products) blocks.push({ id: step.id, kind: 'products', title: '商品信息', data: result.products })
      else if (step.tool_name === 'search_knowledge' && result.results) blocks.push({ id: step.id, kind: 'sources', title: '知识来源', data: result.results })
      else if (step.tool_name === 'apply_refund') blocks.push({ id: step.id, kind: 'refund', title: '退款处理结果', data: result })
    } catch { /* Legacy observations may contain plain text. */ }
  }
  return { tools: [], blocks }
}

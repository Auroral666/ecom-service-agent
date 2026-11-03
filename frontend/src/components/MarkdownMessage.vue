<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = defineProps<{ content: string; businessKinds?: string[] }>()
const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true })
const rendered = computed(() => {
  const safe = DOMPurify.sanitize(markdown.render(props.content), {
    FORBID_TAGS: ['img', 'svg', 'math', 'style', 'form', 'iframe'],
    FORBID_ATTR: ['style', 'id', 'name'],
  })
  const document = new DOMParser().parseFromString(safe, 'text/html')
  // Dedicated cards replace only tables with a matching business header.
  for (const table of document.querySelectorAll('table')) {
    const heading = table.querySelector('thead')?.textContent || ''
    if (props.businessKinds?.includes('orders') && heading.includes('订单号')) table.remove()
    else {
      const wrapper = document.createElement('div')
      wrapper.className = 'table-scroll'
      table.replaceWith(wrapper)
      wrapper.append(table)
    }
  }
  for (const link of document.querySelectorAll('a')) link.setAttribute('rel', 'noreferrer noopener')
  return document.body.innerHTML
})
</script>

<template><div class="markdown-body" v-html="rendered"></div></template>

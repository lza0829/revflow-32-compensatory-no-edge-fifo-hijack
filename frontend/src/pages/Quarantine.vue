<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const rows = ref([])
const err = ref('')

onMounted(async () => {
  try {
    rows.value = await getJson('/api/quarantine')
  } catch (e) {
    err.value = String(e.message || e)
  }
})

function yuan(cents) {
  return (Number(cents || 0) / 100).toFixed(2)
}
</script>

<template>
  <div>
    <h1>未平隔离</h1>
    <p class="lead">冲正无原单、账户不一致或超额等未能配平的流水。</p>
    <p v-if="err" class="msg">{{ err }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr><th>流水键</th><th>账户</th><th>原因</th><th>金额（元）</th><th>归并#</th><th>时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="q in rows" :key="q.id">
            <td>{{ q.entry_key }}</td>
            <td>{{ q.account_code }}</td>
            <td><span class="tag warn">{{ q.reason }}</span></td>
            <td>{{ yuan(q.amount_cents) }}</td>
            <td>{{ q.settle_run_id }}</td>
            <td>{{ q.created_at }}</td>
          </tr>
          <tr v-if="!rows.length"><td colspan="6">暂无隔离项（先跑归并）</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

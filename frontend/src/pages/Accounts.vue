<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const rows = ref([])
const err = ref('')

onMounted(async () => {
  try {
    rows.value = await getJson('/api/accounts')
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
    <h1>账户</h1>
    <p class="lead">结算账户与归并后余额（分字段存库，页面显示元）。</p>
    <p v-if="err" class="msg">{{ err }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr><th>编码</th><th>名称</th><th>币种</th><th>余额（元）</th><th>最近归并</th></tr>
        </thead>
        <tbody>
          <tr v-for="a in rows" :key="a.id">
            <td>{{ a.code }}</td>
            <td>{{ a.name }}</td>
            <td>{{ a.currency }}</td>
            <td>{{ yuan(a.balance_cents) }}</td>
            <td>{{ a.last_settle_run_id || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

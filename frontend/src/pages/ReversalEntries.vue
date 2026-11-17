<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const rows = ref([])
const err = ref('')

onMounted(async () => {
  try {
    rows.value = await getJson('/api/entries?kind=冲正')
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
    <h1>冲正流水</h1>
    <p class="lead">冲正须关联原正向流水键；无原单或超额会在归并时进未平隔离。已被补正向对消绑定的冲正不可再被其他冲正 FIFO 抢占。</p>
    <p v-if="err" class="msg">{{ err }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr><th>流水键</th><th>账户</th><th>金额（元）</th><th>原单键</th><th>业务单</th><th>状态</th><th>对消补记</th></tr>
        </thead>
        <tbody>
          <tr v-for="e in rows" :key="e.id">
            <td>{{ e.entry_key }}</td>
            <td>{{ e.account_code }}</td>
            <td>{{ yuan(e.amount_cents) }}</td>
            <td>{{ e.target_entry_key || '—' }}</td>
            <td>{{ e.biz_ref }}</td>
            <td>
              <span
                class="tag"
                :class="{ on: e.status === '已归并', bad: e.status === '已隔离' }"
              >{{ e.status }}</span>
            </td>
            <td>
              <router-link v-if="e.offset_forward_keys" to="/forward" class="link">
                补正向 {{ e.offset_forward_keys }} · 对消合计 {{ yuan(e.offset_cents) }}
              </router-link>
              <span v-else>—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

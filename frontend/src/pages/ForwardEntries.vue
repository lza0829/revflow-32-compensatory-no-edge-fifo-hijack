<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const rows = ref([])
const err = ref('')
const msg = ref('')
const busy = ref(false)
const showForm = ref(false)

const form = ref({
  reverse_entry_key: '',
  amount_yuan: '',
  offset_yuan: '',
  biz_ref: '',
  note: '',
})

const mergedReversals = ref([])

async function load() {
  rows.value = await getJson('/api/entries?kind=正向')
  mergedReversals.value = (await getJson('/api/entries?kind=冲正')).filter(
    (r) => r.status === '已归并'
  )
}

onMounted(async () => {
  try {
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

const selectedReverse = computed(() =>
  mergedReversals.value.find((r) => r.entry_key === form.value.reverse_entry_key)
)
const reverseQuota = computed(() => {
  const r = selectedReverse.value
  if (!r) return null
  return r.amount_cents - Number(r.offset_cents || 0)
})

function toCents(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return NaN
  return Math.round(n * 100)
}

async function submit() {
  err.value = ''
  msg.value = ''
  const amount = toCents(form.value.amount_yuan)
  if (!Number.isInteger(amount) || amount <= 0) {
    err.value = '补正向金额必须大于 0 元'
    return
  }
  let offset = null
  if (String(form.value.offset_yuan).trim() !== '') {
    offset = toCents(form.value.offset_yuan)
    if (!Number.isInteger(offset) || offset <= 0) {
      err.value = '对消分必须大于 0 元'
      return
    }
  }
  if (!form.value.reverse_entry_key) {
    err.value = '必须选择一笔已归并冲正'
    return
  }
  busy.value = true
  try {
    const r = await postJson('/api/compensatory-forwards', {
      reverse_entry_key: form.value.reverse_entry_key,
      amount_cents: amount,
      offset_cents: offset,
      biz_ref: form.value.biz_ref,
      note: form.value.note,
    })
    msg.value = `补正向 ${r.补正向.entry_key} 已登记并绑定冲正 ${r.补记说明.reverse_entry_key}（对消 ${yuan(r.对消边.offset_cents)}）`
    form.value = { reverse_entry_key: '', amount_yuan: '', offset_yuan: '', biz_ref: '', note: '' }
    showForm.value = false
    await load()
  } catch (e) {
    let text = String(e.message || e)
    try {
      const j = JSON.parse(text)
      text = j.detail || text
    } catch (_) {}
    err.value = text
  } finally {
    busy.value = false
  }
}

function yuan(cents) {
  return (Number(cents || 0) / 100).toFixed(2)
}
</script>

<template>
  <div>
    <h1>正向流水</h1>
    <p class="lead">
      已装载的正向入账；归并后状态变为「已归并」。补正向须引用一笔已归并冲正，登记后即参与净额并形成不可变对消边。
    </p>
    <div class="row-actions">
      <button class="btn" @click="showForm = !showForm">
        {{ showForm ? '收起补正向登记' : '登记补正向' }}
      </button>
    </div>
    <p v-if="msg" class="ok">{{ msg }}</p>
    <p v-if="err" class="msg">{{ err }}</p>

    <div v-if="showForm" class="panel form-panel">
      <h2>补正向登记</h2>
      <p class="note-strip">
        只能补记「原正向少计」。无引用的普通正向不能冒充补正向；补额不得超过被引用冲正未对消额度；任一校验失败整笔回滚。
      </p>
      <div class="form-grid">
        <label>
          <span>引用已归并冲正 *</span>
          <select v-model="form.reverse_entry_key">
            <option value="">请选择</option>
            <option v-for="r in mergedReversals" :key="r.id" :value="r.entry_key">
              {{ r.entry_key }} · {{ r.account_code }} · 冲 {{ yuan(r.amount_cents) }} · 原单 {{ r.target_entry_key || '—' }}
            </option>
          </select>
        </label>
        <label>
          <span>补正向金额（元）*</span>
          <input v-model="form.amount_yuan" inputmode="decimal" placeholder="0.00" />
        </label>
        <label>
          <span>对消分（元，留空等于补额）</span>
          <input v-model="form.offset_yuan" inputmode="decimal" placeholder="0.00" />
        </label>
        <label>
          <span>业务单</span>
          <input v-model="form.biz_ref" placeholder="可选" />
        </label>
        <label class="span-2">
          <span>备注</span>
          <input v-model="form.note" placeholder="可选" />
        </label>
      </div>
      <p v-if="selectedReverse" class="hint-line">
        被引用冲正 {{ selectedReverse.entry_key }}：金额 {{ yuan(selectedReverse.amount_cents) }}，
        已对消 {{ yuan(selectedReverse.offset_cents || 0) }}，
        剩余额度 <strong>{{ yuan(reverseQuota) }}</strong>
      </p>
      <div class="row-actions">
        <button class="btn" :disabled="busy" @click="submit">登记并绑定</button>
        <button class="btn secondary" :disabled="busy" @click="showForm = false">取消</button>
      </div>
    </div>

    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>流水键</th><th>类型</th><th>账户</th><th>金额（元）</th><th>业务单</th>
            <th>引用冲正</th><th>对消分</th><th>状态</th><th>入账时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in rows" :key="e.id">
            <td>{{ e.entry_key }}</td>
            <td>
              <span class="tag" :class="e.kind === '补正向' ? 'warn' : ''">{{ e.kind }}</span>
            </td>
            <td>{{ e.account_code }}</td>
            <td>{{ yuan(e.amount_cents) }}</td>
            <td>{{ e.biz_ref }}</td>
            <td>{{ e.offset_reverse_key || '—' }}</td>
            <td>{{ e.offset_cents != null ? yuan(e.offset_cents) : '—' }}</td>
            <td><span class="tag" :class="{ on: e.status === '已归并' }">{{ e.status }}</span></td>
            <td>{{ e.booked_at }}</td>
          </tr>
          <tr v-if="!rows.length"><td colspan="9">尚无正向流水</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

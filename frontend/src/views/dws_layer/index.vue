<template>
  <section class="page" data-module="dws_layer">
    <header class="page-head">
      <div>
        <h2>汇总层管理管理</h2>
        <p class="page-desc">维护汇总层DWS表的输入、执行约束与结果记录，为汇总层管理提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记汇总表</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出汇总层管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" :data-testid="`pit-run-${row.id}`" @click="runPitAggregate(row)">时点汇总</button>
            <button class="link" type="button" :data-testid="`pit-result-${row.id}`" @click="openPitResult(row)">汇总结果</button>
            <button class="link" type="button" :data-testid="`pit-verify-${row.id}`" @click="runVerify(row)">逐项核对</button>
            <button class="link" type="button" :data-testid="`late-dim-${row.id}`" @click="openLateDim(row)">迟到更正</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无汇总层管理数据，可先登记汇总表</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记汇总表</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="pitPanel" class="modal-backdrop" data-testid="pit-panel">
      <div class="modal">
        <h3>DWS时点汇总结果 · {{ pitPanel.汇总表 }}</h3>
        <p class="page-desc">
          汇总窗口：{{ pitPanel.汇总窗口 }} ｜ 维度模型：{{ pitPanel.维度模型 }} ｜ 生成时间：{{ pitPanel.生成时间 }}
          <template v-if="pitPanel.迟到接管">｜ 迟到接管维度键：{{ pitPanel.接管维度键 }}</template>
          <template v-if="pitPanel.保留原窗口">｜ 保留原窗口，受影响维度键：{{ (pitPanel.受影响维度键 || []).join('、') || '无' }}</template>
        </p>
        <table class="data-table">
          <thead>
            <tr>
              <th>生效维度键</th><th>自然键</th><th>维度版本</th><th>生效区间</th>
              <th>汇总窗口</th><th>聚合函数</th><th>度量值</th><th>明细条数</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in pitPanel.rows" :key="item.维度键" data-testid="pit-row">
              <td>{{ item.维度键 }}</td>
              <td>{{ item.自然键 }}</td>
              <td>{{ item.维度版本 }}</td>
              <td>{{ item.生效区间 }}</td>
              <td>{{ item.汇总窗口 }}</td>
              <td>{{ item.聚合函数 }}({{ item.度量字段 }})</td>
              <td>{{ item.度量值 }}</td>
              <td>{{ item.明细条数 }}</td>
            </tr>
            <tr v-if="!pitPanel.rows.length">
              <td colspan="8" class="empty-state">该窗口暂无时点汇总数据</td>
            </tr>
          </tbody>
        </table>
        <div class="page-actions">
          <button class="btn" type="button" data-testid="goto-dim-model" @click="gotoDimModel">前往维度建模核对</button>
          <button class="btn" type="button" @click="pitPanel = null">关闭</button>
        </div>
      </div>
    </div>

    <div v-if="verifyPanel" class="modal-backdrop" data-testid="verify-panel">
      <div class="modal">
        <h3>逐项核对 · {{ verifyPanel.汇总表 }}</h3>
        <p class="page-desc">{{ verifyPanel.message }}</p>
        <table class="data-table">
          <thead>
            <tr>
              <th>明细键</th><th>业务时点</th><th>维度键</th><th>生效区间</th>
              <th>唯一命中</th><th>时点在窗口内</th><th>时点在生效区间内</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in verifyPanel.核对项" :key="item.明细键" data-testid="verify-row">
              <td>{{ item.明细键 }}</td>
              <td>{{ item.业务时点 }}</td>
              <td>{{ item.维度键 }}</td>
              <td>{{ item.生效区间 }}</td>
              <td>{{ item.唯一命中 ? '是' : '否' }}</td>
              <td>{{ item.时点在窗口内 ? '是' : '否' }}</td>
              <td>{{ item.时点在生效区间内 ? '是' : '否' }}</td>
            </tr>
            <tr v-if="!verifyPanel.核对项.length">
              <td colspan="7" class="empty-state">暂无可核对的明细映射</td>
            </tr>
          </tbody>
        </table>
        <div class="page-actions">
          <button class="btn" type="button" @click="verifyPanel = null">关闭</button>
        </div>
      </div>
    </div>

    <div v-if="lateDimRow" class="modal-backdrop" data-testid="late-dim-modal">
      <form class="modal" @submit.prevent="submitLateDim">
        <h3>迟到维度更正 · {{ lateDimRow.表编号 }}</h3>
        <label class="create-field">
          <span>自然键</span>
          <input v-model="lateDimForm.自然键" data-testid="late-dim-自然键" required />
        </label>
        <label class="create-field">
          <span>生效日期</span>
          <input v-model="lateDimForm.生效日期" data-testid="late-dim-生效日期" placeholder="2026-11-01" required />
        </label>
        <label class="create-field">
          <span>失效日期</span>
          <input v-model="lateDimForm.失效日期" data-testid="late-dim-失效日期" placeholder="2099-12-31" required />
        </label>
        <label class="create-field">
          <span>处理策略</span>
          <select v-model="lateDimForm.策略" data-testid="late-dim-策略">
            <option value="迟到接管">迟到接管（生成新汇总窗口）</option>
            <option value="保留原窗口">保留原窗口（附受影响维度键清单）</option>
          </select>
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="lateDimRow = null">取消</button>
          <button class="btn primary" data-testid="late-dim-submit" type="submit">提交更正</button>
        </div>
        <div v-if="handlingCompare.length" class="late-compare" data-testid="late-compare">
          <h4>处理先后（当前生效：{{ currentHandling || '无' }}）</h4>
          <p class="page-desc">迟到接管与保留原窗口谁优先尚无业务结论；当前按处理时间后者优先。</p>
          <ol>
            <li v-for="item in handlingCompare" :key="item.序号">
              {{ item.处理时间 }} · {{ item.策略 }} · 自然键 {{ item.自然键 }} → {{ item.维度键 }}
            </li>
          </ol>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条汇总层管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/dws_layer'
const columns = ["表编号", "统计粒度", "汇总维度", "度量字段", "聚合函数", "时间范围", "刷新周期", "创建批次"]
const actions = ["提交校验", "确认上线", "标记下线"]
const statuses = ["草稿", "待校验", "已上线", "已下线"]
const metricNames = ["汇总表总数", "待校验表", "已上线表"]

const router = useRouter()
const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const pitPanel = ref<Record<string, any> | null>(null)
const verifyPanel = ref<Record<string, any> | null>(null)
const lateDimRow = ref<Row | null>(null)
const lateDimForm = ref<Record<string, string>>({ 自然键: '', 生效日期: '', 失效日期: '', 策略: '迟到接管' })
const handlingCompare = ref<Record<string, any>[]>([])
const currentHandling = ref('')
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'dws_layer.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('汇总层管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '汇总层管理操作失败'
  }
}

async function runPitAggregate(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/pit_aggregate`, { method: 'POST' })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message || '时点汇总生成失败')
    showPitResult(payload.result)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '时点汇总生成失败'
  }
}

async function openPitResult(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/pit_result`)
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message || '时点汇总结果读取失败')
    const windows = payload.窗口结果 || {}
    const mergedRows = Object.values(windows).flatMap((item: any) => item.rows || [])
    const latest: any = Object.values(windows).at(-1) ?? {}
    showPitResult({
      汇总表: payload.汇总表,
      维度模型: latest.维度模型 ?? row.维度模型 ?? '',
      汇总窗口: Object.keys(windows).join('、') || '尚未生成',
      生成时间: latest.生成时间 ?? '—',
      迟到接管: latest.迟到接管,
      接管维度键: latest.接管维度键,
      保留原窗口: latest.保留原窗口,
      受影响维度键: latest.受影响维度键,
      rows: mergedRows,
    })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '时点汇总结果读取失败'
  }
}

function showPitResult(result: Record<string, any>) {
  pitPanel.value = { rows: [], ...result }
}

async function runVerify(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/pit_verify`)
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message || '逐项核对失败')
    verifyPanel.value = { 汇总表: row.表编号, ...payload }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '逐项核对失败'
  }
}

async function openLateDim(row: Row) {
  errorMessage.value = ''
  lateDimForm.value = { 自然键: '', 生效日期: '', 失效日期: '', 策略: '迟到接管' }
  lateDimRow.value = row
  await refreshHandlings(row)
}

async function refreshHandlings(row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}/late_dim_handlings`)
    const payload = await response.json()
    handlingCompare.value = payload.处理先后 ?? []
    currentHandling.value = payload.当前生效 ?? ''
  } catch {
    handlingCompare.value = []
    currentHandling.value = ''
  }
}

async function submitLateDim() {
  if (!lateDimRow.value) return
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${lateDimRow.value.id}/late_dim_correction`, {
      method: 'POST',
      body: JSON.stringify(lateDimForm.value),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message || '迟到维度更正失败')
    await refreshHandlings(lateDimRow.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '迟到维度更正失败'
  }
}

function gotoDimModel() {
  const dimModel = pitPanel.value?.维度模型
  void router.push({ path: '/dim_model', query: dimModel ? { focus: String(dimModel) } : {} })
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('汇总表列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '汇总层管理列表读取失败'
  }
}

onMounted(reload)
</script>

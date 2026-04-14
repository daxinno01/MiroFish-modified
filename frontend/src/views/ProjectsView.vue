<template>
  <div class="projects-page">
    <!-- Header -->
    <header class="app-header">
      <div class="header-left">
        <div class="brand" @click="goHome">MIROFISH</div>
      </div>

      <div class="header-center">
        <div class="page-title">历史项目</div>
      </div>

      <div class="header-right">
        <button class="icon-btn" @click="refresh" :disabled="loading" title="刷新">
          <svg :class="{ spinning: loading }" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M23 4v6h-6"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
          </svg>
        </button>
        <button class="btn-new" @click="goHome">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
          </svg>
          新建项目
        </button>
      </div>
    </header>

    <!-- Toolbar -->
    <div class="toolbar">
      <div class="toolbar-left">
        <div class="search-wrap">
          <svg class="search-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input
            v-model="query"
            class="search-input"
            placeholder="搜索项目名称、ID、模拟目标..."
            :disabled="loading"
          />
          <button v-if="query" class="search-clear" @click="clearQuery">×</button>
        </div>

        <!-- Filter Tabs -->
        <div class="filter-tabs">
          <button
            v-for="tab in filterTabs"
            :key="tab.value"
            class="filter-tab"
            :class="{ active: filterMode === tab.value }"
            @click="filterMode = tab.value"
          >
            {{ tab.label }}
          </button>
        </div>
      </div>

      <div class="toolbar-right">
        <transition name="fade">
          <button
            v-if="selectedCount > 0"
            class="btn-delete"
            @click="deleteSelected"
            :disabled="deleting"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/>
            </svg>
            删除所选 ({{ selectedCount }})
          </button>
        </transition>
        <div class="result-count">
          <span v-if="loading" class="loading-dots">
            <span></span><span></span><span></span>
          </span>
          <span v-else>{{ filteredProjects.length }} 个项目</span>
        </div>
      </div>
    </div>

    <!-- Main Content -->
    <main class="content">

      <!-- Error State -->
      <div v-if="error" class="state-panel error-panel">
        <div class="state-icon">⚠️</div>
        <div class="state-title">加载失败</div>
        <div class="state-msg">{{ error }}</div>
        <button class="btn-primary" @click="refresh">重新加载</button>
      </div>

      <!-- Loading Skeleton -->
      <div v-else-if="loading && projects.length === 0" class="card-grid">
        <div v-for="i in 6" :key="i" class="card skeleton">
          <div class="sk-header"></div>
          <div class="sk-line wide"></div>
          <div class="sk-line narrow"></div>
          <div class="sk-footer"></div>
        </div>
      </div>

      <!-- Empty State -->
      <div v-else-if="!loading && filteredProjects.length === 0" class="state-panel">
        <div class="state-icon">🗂</div>
        <div class="state-title">{{ query ? '没有匹配的项目' : '暂无历史项目' }}</div>
        <div class="state-msg">{{ query ? '试试其他关键词' : '去首页上传材料，开始一个新的仿真项目。' }}</div>
        <button v-if="!query" class="btn-primary" @click="goHome">开始新项目</button>
        <button v-else class="btn-secondary" @click="clearQuery">清空搜索</button>
      </div>

      <!-- Project Cards -->
      <div v-else class="card-grid">
        <div
          v-for="p in filteredProjects"
          :key="p.project_id"
          class="card"
          :class="{ selected: isSelected(p.project_id), interactive: !!p.latest_completed_report?.report_id }"
          @click="handleRowClick(p)"
        >
          <!-- Selection Checkbox -->
          <div class="card-select" @click.stop>
            <label class="checkbox-wrap">
              <input
                type="checkbox"
                :checked="isSelected(p.project_id)"
                @change="toggleSelected(p.project_id)"
              />
              <span class="checkmark"></span>
            </label>
          </div>

          <!-- Status Badge -->
          <div class="card-status-badge" :class="reportStatusClass(p.latest_completed_report?.report_status || p.latest_report?.report_status)">
            <span class="badge-dot"></span>
            {{ reportStatusText(p.latest_completed_report?.report_status || p.latest_report?.report_status) }}
          </div>

          <!-- Card Body -->
          <div class="card-body">
            <div class="card-name">{{ p.name || 'Unnamed Project' }}</div>
            <div class="card-req" v-if="p.simulation_requirement">{{ p.simulation_requirement }}</div>
          </div>

          <!-- Metrics Row -->
          <div class="card-metrics">
            <div class="metric" v-if="p.token_usage?.total_tokens">
              <span class="metric-label">Token</span>
              <span class="metric-value mono">{{ formatTokens(p.token_usage.total_tokens) }}</span>
            </div>
            <div class="metric" v-if="p.token_usage?.call_count">
              <span class="metric-label">调用</span>
              <span class="metric-value mono">{{ p.token_usage.call_count }}</span>
            </div>
            <div class="metric">
              <span class="metric-label">更新</span>
              <span class="metric-value">{{ formatTimeShort(p.latest_completed_report?.updated_at || p.latest_report?.updated_at || p.updated_at || p.created_at) }}</span>
            </div>
          </div>

          <!-- IDs Row -->
          <div class="card-ids">
            <span class="id-chip">{{ p.project_id?.slice(0, 12) }}…</span>
            <span class="id-chip muted" v-if="p.latest_ready_simulation?.simulation_id">sim</span>
            <span class="id-chip muted" v-if="p.latest_completed_report?.report_id || p.latest_report?.report_id">rpt</span>
          </div>

          <!-- Action Footer -->
          <div class="card-footer" @click.stop>
            <button
              v-if="p.latest_completed_report?.report_id"
              class="action-btn highlight"
              @click="activateInterview(p)"
              title="激活采访并进入交互"
            >激活采访</button>
            <button
              v-if="p.latest_completed_report?.report_id"
              class="action-btn primary"
              @click="openInteraction(p.latest_completed_report.report_id)"
            >交互</button>
            <button
              v-if="p.latest_completed_report?.report_id || p.latest_report?.report_id"
              class="action-btn"
              @click="openReport(p.latest_completed_report?.report_id || p.latest_report?.report_id)"
            >报告</button>
            <button
              v-if="p.latest_ready_simulation?.simulation_id"
              class="action-btn"
              @click="openEnv(p.latest_ready_simulation.simulation_id)"
            >环境</button>
            <button
              class="action-btn"
              @click="openProject(p.project_id)"
            >项目</button>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { deleteGraph, deleteProject, listProjects } from '../api/graph'
import { listSimulations } from '../api/simulation'
import { checkReportStatus } from '../api/report'

const router = useRouter()

const loading = ref(false)
const deleting = ref(false)
const error = ref('')
const projects = ref([])
const query = ref('')
const filterMode = ref('interactive')
const selectedIds = ref(new Set())

const ENV_READY_STATUSES = new Set(['ready', 'running', 'paused', 'stopped', 'completed'])

const filterTabs = [
  { value: 'interactive', label: '可交互' },
  { value: 'env', label: '环境就绪' },
  { value: 'all', label: '全部' },
]

const goHome = () => router.push({ name: 'Home' })

const refresh = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await listProjects(200)
    const baseProjects = res?.data || []

    let allSimulations = []
    try {
      const simsRes = await listSimulations()
      allSimulations = Array.isArray(simsRes?.data) ? simsRes.data : []
    } catch {
      allSimulations = []
    }

    const latestReadyByProjectId = new Map()
    for (const sim of allSimulations) {
      if (!ENV_READY_STATUSES.has(sim?.status)) continue
      const projectId = sim?.project_id
      if (!projectId) continue
      const ts = Date.parse(sim?.updated_at || sim?.created_at || '') || 0
      const existing = latestReadyByProjectId.get(projectId)
      const existingTs = Date.parse(existing?.updated_at || existing?.created_at || '') || 0
      if (!existing || ts >= existingTs) {
        latestReadyByProjectId.set(projectId, sim)
      }
    }

    const simsByProjectId = new Map()
    for (const sim of allSimulations) {
      if (!ENV_READY_STATUSES.has(sim?.status)) continue
      if (!sim?.project_id || !sim?.simulation_id) continue
      if (!simsByProjectId.has(sim.project_id)) simsByProjectId.set(sim.project_id, [])
      simsByProjectId.get(sim.project_id).push(sim)
    }

    for (const [projectId, sims] of simsByProjectId.entries()) {
      sims.sort((a, b) => {
        const at = Date.parse(a?.updated_at || a?.created_at || '') || 0
        const bt = Date.parse(b?.updated_at || b?.created_at || '') || 0
        return bt - at
      })
      simsByProjectId.set(projectId, sims)
    }

    const latestReportByProjectId = new Map()
    const latestCompletedReportByProjectId = new Map()
    await Promise.all(
      baseProjects.map(async p => {
        const sims = simsByProjectId.get(p.project_id) || []
        let latestAnyReport = null
        for (const sim of sims) {
          try {
            const r = await checkReportStatus(sim.simulation_id)
            if (r?.success && r?.data?.has_report && r?.data?.report_id && !latestAnyReport) {
              latestAnyReport = {
                report_id: r.data.report_id,
                simulation_id: sim.simulation_id,
                report_status: r.data.report_status,
                interview_unlocked: !!r.data.interview_unlocked,
                updated_at: sim.updated_at || sim.created_at || null
              }
            }
            if (r?.success && r?.data?.interview_unlocked && r?.data?.report_id) {
              const completed = {
                report_id: r.data.report_id,
                simulation_id: sim.simulation_id,
                report_status: r.data.report_status,
                interview_unlocked: true,
                updated_at: sim.updated_at || sim.created_at || null
              }
              latestCompletedReportByProjectId.set(p.project_id, completed)
              if (!latestAnyReport) latestAnyReport = completed
              break
            }
          } catch {
            // ignore
          }
        }
        if (latestAnyReport) {
          latestReportByProjectId.set(p.project_id, latestAnyReport)
        }
      })
    )

    projects.value = baseProjects.map(p => {
      const latestReady = latestReadyByProjectId.get(p.project_id) || null
      const latestReport = latestCompletedReportByProjectId.get(p.project_id) || null
      const latestAny = latestReportByProjectId.get(p.project_id) || null
      return {
        ...p,
        latest_ready_simulation: latestReady
          ? {
              simulation_id: latestReady.simulation_id,
              status: latestReady.status,
              updated_at: latestReady.updated_at || latestReady.created_at || null
            }
          : null,
        latest_report: latestAny,
        latest_completed_report: latestReport
      }
    })

    if (selectedIds.value.size) {
      const availableIds = new Set(projects.value.map(p => p.project_id))
      const next = new Set()
      for (const id of selectedIds.value) {
        if (availableIds.has(id)) next.add(id)
      }
      selectedIds.value = next
    }
  } catch (e) {
    error.value = e?.message || 'Unknown error'
  } finally {
    loading.value = false
  }
}

const clearQuery = () => { query.value = '' }
const selectedCount = computed(() => selectedIds.value.size)
const isSelected = (projectId) => selectedIds.value.has(projectId)

const toggleSelected = (projectId) => {
  const next = new Set(selectedIds.value)
  if (next.has(projectId)) next.delete(projectId)
  else next.add(projectId)
  selectedIds.value = next
}

const clearSelection = () => { selectedIds.value = new Set() }

const filteredProjects = computed(() => {
  const q = query.value.trim().toLowerCase()
  let base = projects.value

  if (filterMode.value === 'env') base = base.filter(p => p?.latest_ready_simulation?.simulation_id)
  if (filterMode.value === 'interactive') base = base.filter(p => p?.latest_completed_report?.report_id)

  if (!q) return base
  return base.filter(p => {
    const haystack = [p?.name, p?.project_id, p?.graph_id, p?.simulation_requirement, p?.latest_ready_simulation?.simulation_id, p?.latest_completed_report?.report_id]
      .filter(Boolean).join(' ').toLowerCase()
    return haystack.includes(q)
  })
})

const handleRowClick = (project) => {
  if (!project) return
  if (project.latest_completed_report?.report_id) { openInteraction(project.latest_completed_report.report_id); return }
  if (project.latest_report?.report_id) { openReport(project.latest_report.report_id); return }
  if (project.latest_ready_simulation?.simulation_id) { openEnv(project.latest_ready_simulation.simulation_id); return }
  openProject(project.project_id)
}

const openProject = (projectId) => { if (projectId) router.push({ name: 'Process', params: { projectId } }) }
const openEnv = (simulationId) => { if (simulationId) router.push({ name: 'Simulation', params: { simulationId } }) }
const openReport = (reportId) => { if (reportId) router.push({ name: 'Report', params: { reportId } }) }
const openInteraction = (reportId) => { if (reportId) router.push({ name: 'Interaction', params: { reportId } }) }

const activateInterview = (project) => {
  const reportId = project.latest_completed_report?.report_id
  if (!reportId) return
  const simulationId = project.latest_completed_report?.simulation_id || project.latest_ready_simulation?.simulation_id
  router.push({ name: 'Interaction', params: { reportId }, query: { activate_env: 'true', sim_id: simulationId } })
}

const deleteSelected = async () => {
  if (selectedIds.value.size === 0) return
  if (!window.confirm(`确定删除选中的 ${selectedIds.value.size} 个项目？删除后无法恢复。`)) return

  deleting.value = true
  const failed = []
  try {
    const ids = Array.from(selectedIds.value)
    for (const projectId of ids) {
      const project = projects.value.find(p => p.project_id === projectId)
      try {
        if (project?.graph_id) await deleteGraph(project.graph_id)
      } catch (e) {
        failed.push({ projectId, error: e?.message || '图谱删除失败' })
        continue
      }
      try {
        await deleteProject(projectId)
      } catch (e) {
        failed.push({ projectId, error: e?.message || '项目删除失败' })
      }
    }
    await refresh()
    clearSelection()
  } finally {
    deleting.value = false
  }
  if (failed.length) {
    window.alert(`部分项目删除失败:\n${failed.map(i => `${i.projectId}: ${i.error}`).join('\n')}`)
  }
}

const reportStatusText = (status) => {
  if (!status) return '未生成'
  if (status === 'completed') return '已解锁'
  if (status === 'generating') return '生成中'
  if (status === 'failed') return '失败'
  return status
}

const reportStatusClass = (status) => {
  if (status === 'completed') return 'ok'
  if (status === 'generating') return 'running'
  if (status === 'failed') return 'bad'
  return 'neutral'
}

const formatTimeShort = (isoString) => {
  if (!isoString) return '-'
  const dt = new Date(isoString)
  if (Number.isNaN(dt.getTime())) return '-'
  const now = new Date()
  const diff = now - dt
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
  if (diff < 604800000) return `${Math.floor(diff / 86400000)} 天前`
  return dt.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

const formatTokens = (total) => {
  if (!total) return '0'
  if (total >= 1000000) return (total / 1000000).toFixed(1) + 'M'
  if (total >= 1000) return (total / 1000).toFixed(1) + 'K'
  return total.toLocaleString()
}

onMounted(() => { refresh() })
</script>

<style scoped>
/* ── Reset & Base ─────────────────────────────── */
.projects-page {
  min-height: 100vh;
  background: #F8F8F8;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  display: flex;
  flex-direction: column;
}

/* ── Header ───────────────────────────────────── */
.app-header {
  height: 60px;
  background: #FFF;
  border-bottom: 1px solid #EAEAEA;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 28px;
  position: sticky;
  top: 0;
  z-index: 100;
}

.brand {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 800;
  font-size: 17px;
  letter-spacing: 1px;
  cursor: pointer;
  color: #000;
  transition: opacity 0.2s;
}
.brand:hover { opacity: 0.65; }

.page-title {
  font-weight: 700;
  font-size: 15px;
  color: #000;
  letter-spacing: 0.3px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.icon-btn {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  border: 1px solid #E8E8E8;
  background: transparent;
  color: #555;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, color 0.15s;
}
.icon-btn:hover { background: #F3F3F3; color: #000; }
.icon-btn:disabled { opacity: 0.45; cursor: not-allowed; }

.spinning { animation: spin 0.8s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

.btn-new {
  height: 36px;
  padding: 0 16px;
  border-radius: 8px;
  border: none;
  background: #000;
  color: #FFF;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: background 0.15s, transform 0.1s;
}
.btn-new:hover { background: #222; transform: translateY(-1px); }
.btn-new:active { transform: translateY(0); }

/* ── Toolbar ──────────────────────────────────── */
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 28px;
  background: #FFF;
  border-bottom: 1px solid #EAEAEA;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.search-wrap {
  position: relative;
  display: flex;
  align-items: center;
  flex: 1;
  max-width: 380px;
}

.search-icon {
  position: absolute;
  left: 11px;
  color: #AAA;
  pointer-events: none;
  flex-shrink: 0;
}

.search-input {
  width: 100%;
  height: 36px;
  border: 1px solid #E5E5E5;
  border-radius: 9px;
  padding: 0 34px 0 34px;
  font-size: 13px;
  font-family: inherit;
  background: #FAFAFA;
  outline: none;
  transition: border-color 0.15s, background 0.15s;
  color: #000;
}
.search-input::placeholder { color: #BBB; }
.search-input:focus {
  border-color: #C0C0C0;
  background: #FFF;
}

.search-clear {
  position: absolute;
  right: 10px;
  background: none;
  border: none;
  color: #AAA;
  font-size: 16px;
  cursor: pointer;
  line-height: 1;
  padding: 0 2px;
  transition: color 0.15s;
}
.search-clear:hover { color: #000; }

/* Filter Tabs */
.filter-tabs {
  display: flex;
  background: #F3F3F3;
  border-radius: 8px;
  padding: 3px;
  gap: 2px;
  flex-shrink: 0;
}

.filter-tab {
  height: 30px;
  padding: 0 14px;
  border: none;
  background: transparent;
  border-radius: 6px;
  font-size: 12.5px;
  font-weight: 600;
  color: #777;
  cursor: pointer;
  transition: all 0.15s;
  font-family: inherit;
}
.filter-tab:hover { color: #333; }
.filter-tab.active {
  background: #FFF;
  color: #000;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

/* Delete Button */
.btn-delete {
  height: 34px;
  padding: 0 14px;
  border-radius: 8px;
  border: 1px solid #FFCCC9;
  background: #FFF5F5;
  color: #D4380D;
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: background 0.15s, border-color 0.15s;
  font-family: inherit;
}
.btn-delete:hover { background: #FFEBE8; border-color: #FFA39E; }
.btn-delete:disabled { opacity: 0.5; cursor: not-allowed; }

.result-count {
  font-size: 12.5px;
  color: #999;
  font-weight: 500;
  white-space: nowrap;
  min-width: 60px;
  text-align: right;
}

/* Loading dots */
.loading-dots {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}
.loading-dots span {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #CCC;
  animation: blink 1.2s infinite;
}
.loading-dots span:nth-child(2) { animation-delay: 0.2s; }
.loading-dots span:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink { 0%, 80%, 100% { opacity: 0.2; } 40% { opacity: 1; } }

/* ── Content Area ─────────────────────────────── */
.content {
  flex: 1;
  padding: 28px;
  max-width: 1440px;
  width: 100%;
  margin: 0 auto;
  box-sizing: border-box;
}

/* ── State Panels ─────────────────────────────── */
.state-panel {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 80px 40px;
  gap: 12px;
}

.state-icon { font-size: 48px; line-height: 1; margin-bottom: 4px; }
.state-title { font-size: 17px; font-weight: 700; color: #111; }
.state-msg { font-size: 14px; color: #888; max-width: 320px; line-height: 1.6; margin-bottom: 8px; }

.btn-primary {
  height: 38px;
  padding: 0 22px;
  border-radius: 9px;
  border: none;
  background: #000;
  color: #FFF;
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.15s;
}
.btn-primary:hover { background: #222; }

.btn-secondary {
  height: 38px;
  padding: 0 22px;
  border-radius: 9px;
  border: 1px solid #E0E0E0;
  background: #FFF;
  color: #333;
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.15s;
}
.btn-secondary:hover { background: #F5F5F5; }

/* ── Card Grid ────────────────────────────────── */
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

/* ── Card ─────────────────────────────────────── */
.card {
  background: #FFF;
  border: 1px solid #EBEBEB;
  border-radius: 14px;
  padding: 18px 18px 14px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 10px;
  position: relative;
  transition: box-shadow 0.2s, border-color 0.2s, transform 0.15s;
  overflow: hidden;
}
.card:hover {
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.08);
  border-color: #D8D8D8;
  transform: translateY(-2px);
}
.card.selected {
  border-color: #000;
  box-shadow: 0 0 0 2px rgba(0,0,0,0.08);
}
.card.interactive::after {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, #FF4500, #FF6A00);
  border-radius: 14px 14px 0 0;
}

/* Card internals */
.card-select {
  position: absolute;
  top: 14px;
  right: 14px;
}

/* Custom Checkbox */
.checkbox-wrap {
  position: relative;
  display: block;
  width: 18px;
  height: 18px;
  cursor: pointer;
}
.checkbox-wrap input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}
.checkmark {
  position: absolute;
  inset: 0;
  border: 1.5px solid #D0D0D0;
  border-radius: 5px;
  background: #FFF;
  transition: all 0.15s;
}
.checkbox-wrap input:checked + .checkmark {
  background: #000;
  border-color: #000;
}
.checkbox-wrap input:checked + .checkmark::after {
  content: '';
  position: absolute;
  left: 5px;
  top: 2px;
  width: 5px;
  height: 9px;
  border: 2px solid #FFF;
  border-top: none;
  border-left: none;
  transform: rotate(45deg);
}

/* Status Badge */
.card-status-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: 11.5px;
  font-weight: 600;
  width: fit-content;
  border: 1px solid transparent;
}

.badge-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.card-status-badge.ok {
  background: rgba(0, 168, 94, 0.1);
  border-color: rgba(0, 168, 94, 0.25);
  color: #006D3B;
}
.card-status-badge.ok .badge-dot { background: #00A85E; }

.card-status-badge.running {
  background: rgba(255, 69, 0, 0.08);
  border-color: rgba(255, 69, 0, 0.25);
  color: #CC3700;
}
.card-status-badge.running .badge-dot {
  background: #FF4500;
  animation: pulse 1.2s infinite;
}

.card-status-badge.bad {
  background: rgba(220, 30, 30, 0.08);
  border-color: rgba(220, 30, 30, 0.2);
  color: #B91C1C;
}
.card-status-badge.bad .badge-dot { background: #DC1E1E; }

.card-status-badge.neutral {
  background: #F5F5F5;
  border-color: #E8E8E8;
  color: #777;
}
.card-status-badge.neutral .badge-dot { background: #CCC; }

@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }

/* Card Body */
.card-body { padding-right: 22px; }

.card-name {
  font-size: 14.5px;
  font-weight: 700;
  color: #0A0A0A;
  line-height: 1.4;
  word-break: break-word;
}

.card-req {
  margin-top: 5px;
  font-size: 12.5px;
  color: #888;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* Metrics */
.card-metrics {
  display: flex;
  gap: 16px;
  padding-top: 2px;
}

.metric {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.metric-label {
  font-size: 10.5px;
  color: #BBB;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.metric-value {
  font-size: 12.5px;
  font-weight: 700;
  color: #333;
}

.metric-value.mono {
  font-family: 'JetBrains Mono', monospace;
}

/* IDs */
.card-ids {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
}

.id-chip {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  background: #F3F3F3;
  font-size: 10.5px;
  font-family: 'JetBrains Mono', monospace;
  color: #666;
  display: inline-flex;
  align-items: center;
  border: 1px solid #EBEBEB;
}
.id-chip.muted {
  background: #FAFAFA;
  color: #AAA;
  border-color: #F0F0F0;
}

/* Card Footer */
.card-footer {
  display: flex;
  gap: 6px;
  align-items: center;
  padding-top: 4px;
  border-top: 1px solid #F0F0F0;
  margin-top: 2px;
  flex-wrap: wrap;
}

.action-btn {
  height: 28px;
  padding: 0 11px;
  border-radius: 7px;
  border: 1px solid #E8E8E8;
  background: #FFF;
  color: #444;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
  font-family: inherit;
  white-space: nowrap;
}
.action-btn:hover {
  background: #F5F5F5;
  border-color: #D5D5D5;
  color: #000;
}

.action-btn.primary {
  background: #000;
  border-color: #000;
  color: #FFF;
}
.action-btn.primary:hover { background: #222; }

.action-btn.highlight {
  background: rgba(255, 69, 0, 0.06);
  border-color: rgba(255, 69, 0, 0.3);
  color: #CC3700;
}
.action-btn.highlight:hover {
  background: rgba(255, 69, 0, 0.12);
  border-color: rgba(255, 69, 0, 0.5);
}

/* ── Skeleton ─────────────────────────────────── */
.card.skeleton { cursor: default; pointer-events: none; }
.card.skeleton:hover { transform: none; box-shadow: none; border-color: #EBEBEB; }

.sk-header, .sk-line, .sk-footer {
  background: linear-gradient(90deg, #F0F0F0 25%, #E8E8E8 50%, #F0F0F0 75%);
  background-size: 200% 100%;
  animation: shimmer 1.4s infinite;
  border-radius: 6px;
}
.sk-header { height: 22px; width: 40%; }
.sk-line { height: 14px; }
.sk-line.wide { width: 90%; margin-top: 10px; }
.sk-line.narrow { width: 65%; margin-top: 8px; }
.sk-footer { height: 30px; margin-top: 10px; width: 100%; }
@keyframes shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }

/* ── Transition ───────────────────────────────── */
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s, transform 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; transform: scale(0.95); }

/* ── Responsive ───────────────────────────────── */
@media (max-width: 768px) {
  .app-header { padding: 0 16px; }
  .toolbar { padding: 12px 16px; flex-wrap: wrap; }
  .toolbar-left { flex-wrap: wrap; }
  .content { padding: 16px; }
  .card-grid { grid-template-columns: 1fr; gap: 12px; }
  .search-wrap { max-width: 100%; }
  .page-title { display: none; }
}
</style>

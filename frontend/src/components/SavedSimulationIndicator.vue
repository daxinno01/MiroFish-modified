<template>
  <aside v-if="isSaved" class="saved-indicator" aria-label="Saved simulation controls">
    <strong>Saved simulation</strong>
    <span v-if="session">{{ session.current_stage }} · {{ Math.round(session.playback_position * 100) }}%</span>
    <button v-if="session && !session.completed" @click="toggle" :disabled="busy">{{ paused ? 'Resume' : 'Stop' }}</button>
    <button v-if="session" @click="restart" :disabled="busy">Start over</button>
    <span v-if="error" role="alert">{{ error }}</span>
  </aside>
</template>
<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createReplay, resumeReplay, stopReplay } from '../api/recordings'
import { replayState } from '../store/replay'
import { subscribeReplay } from '../store/replayStatus'
const route = useRoute()
const router = useRouter()
const session = ref(null)
const busy = ref(false)
const error = ref('')
const isSaved = computed(() => Object.values(route.params).some(value => /^(rproj_|rsim_|rreport_)/.test(String(value))))
const paused = computed(() => session.value?.stages[session.value.current_stage]?.paused)
let unsubscribe
watch([isSaved, () => replayState.replayId], () => {
  unsubscribe?.()
  session.value = null
  error.value = ''
  if (isSaved.value && replayState.replayId) {
    unsubscribe = subscribeReplay(replayState.replayId, data => {
      session.value = data
      error.value = ''
    }, err => { error.value = err.message })
  }
}, { immediate: true })
async function toggle() {
  busy.value = true
  error.value = ''
  try {
    session.value = (await (paused.value ? resumeReplay : stopReplay)(session.value.replay_id)).data
  } catch (err) { error.value = err.message }
  finally { busy.value = false }
}
async function restart() {
  busy.value = true
  error.value = ''
  try {
    const response = await createReplay(session.value.recording_id)
    // Remount the workflow so it resumes ontology polling for the new session.
    router.push({ name: 'Process', params: { projectId: response.data.project_id } })
  } catch (err) { error.value = err.message }
  finally { busy.value = false }
}
onUnmounted(() => unsubscribe?.())
</script>
<style scoped>
.saved-indicator { position: fixed; bottom: 12px; left: 12px; z-index: 100; display: flex; flex-wrap: wrap; align-items: center; gap: 12px; padding: 10px 14px; background: #fff8e5; border: 1px solid #dca843; border-radius: 6px; font-size: 12px; max-width: calc(100vw - 24px); }
button { padding: 4px 8px; cursor: pointer; }
[role=alert] { color: #b42318; }
</style>

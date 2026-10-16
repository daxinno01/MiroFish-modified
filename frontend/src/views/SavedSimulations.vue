<template>
  <main class="saved-page">
    <button @click="router.push('/')">← Home</button>
    <h1>Saved simulations</h1>
    <p>Replay a recorded analysis, then ask new questions after the report completes.</p>
    <button @click="refresh" :disabled="loading">Refresh</button>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="loading">Loading recordings…</p>
    <p v-else-if="!recordings.length">No saved simulations yet. Select “Record for saved simulation” before starting a live run.</p>
    <ul>
      <li v-for="recording in recordings" :key="recording.recording_id">
        <div>
          <strong>{{ recording.name || recording.recording_id }}</strong>
          <p>{{ recording.ready ? 'Ready to replay' : recording.error }}</p>
          <small v-if="recording.missing_stages?.length">Missing: {{ recording.missing_stages.join(', ') }}</small>
        </div>
        <button :disabled="!recording.ready || starting" @click="start(recording.recording_id)">Start saved simulation →</button>
      </li>
    </ul>
    <p v-for="(failure, index) in failures" :key="index" role="alert">Recording failed: {{ failure.error }}</p>
  </main>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { createReplay, listRecordings } from '../api/recordings'
const router = useRouter()
const recordings = ref([])
const failures = ref([])
const loading = ref(false)
const starting = ref(false)
const error = ref('')
async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const response = await listRecordings()
    recordings.value = response.data.recordings
    failures.value = response.data.errors
  } catch (err) { error.value = err.message }
  finally { loading.value = false }
}
async function start(id) {
  starting.value = true
  error.value = ''
  try {
    const response = await createReplay(id)
    router.push({ name: 'Process', params: { projectId: response.data.project_id } })
  } catch (err) { error.value = err.message }
  finally { starting.value = false }
}
onMounted(refresh)
</script>

<style scoped>
.saved-page { max-width: 960px; margin: 60px auto; padding: 24px; }
h1 { margin: 24px 0 12px; }
p { margin: 12px 0; line-height: 1.5; }
ul { list-style: none; margin-top: 24px; }
li { display: flex; align-items: center; justify-content: space-between; gap: 20px; border-bottom: 1px solid #ddd; padding: 24px 0; }
button { padding: 10px 16px; cursor: pointer; }
button:disabled { cursor: default; opacity: .5; }
[role=alert] { color: #b42318; }
</style>

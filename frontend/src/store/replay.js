import { reactive } from 'vue'

export const replayState = reactive({ replayId: null })

export function observeReplayResponse(response) {
  const id = response?.replay_id || response?.data?.replay_id
  if (id) replayState.replayId = id
}

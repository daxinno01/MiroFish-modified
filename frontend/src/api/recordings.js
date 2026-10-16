import service from './index'

export const listRecordings = () => service.get('/api/recordings')
export const createReplay = (recordingId) => service.post('/api/replays', { recording_id: recordingId })
export const getReplay = (replayId, config = {}) => service.get(`/api/replays/${replayId}`, config)
export const stopReplay = (replayId) => service.post(`/api/replays/${replayId}/stop`)
export const resumeReplay = (replayId) => service.post(`/api/replays/${replayId}/resume`)

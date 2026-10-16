export const BACKEND_UNAVAILABLE = 'Backend unavailable; reconnecting'

export function isConnectionFailure(error) {
  return error?.backendUnavailable || error?.code === 'ERR_NETWORK' ||
    error?.code === 'ECONNABORTED' || error?.response?.status === 503
}

// Schedule from completion so a slow request can never overlap its successor.
export function completionPoll(request, { interval = 1000, onError = () => {} } = {}) {
  const controller = new AbortController()
  let timer
  let failures = 0
  async function tick() {
    let delay = interval
    try {
      await request(controller.signal)
      failures = 0
    } catch (error) {
      if (controller.signal.aborted) return
      if (isConnectionFailure(error)) delay = Math.min(10000, 1000 * 2 ** failures++)
      onError(error)
    }
    if (!controller.signal.aborted) timer = setTimeout(tick, delay)
  }
  tick()
  return () => {
    controller.abort()
    clearTimeout(timer)
  }
}

export function sharedPoller(load) {
  const channels = new Map()
  return function subscribe(id, onData, onError = () => {}) {
    let channel = channels.get(id)
    if (!channel) {
      channel = { listeners: new Set(), cancel: null }
      channels.set(id, channel)
    }
    const listener = { onData, onError }
    channel.listeners.add(listener)
    if (!channel.cancel) {
      channel.cancel = completionPoll(async signal => {
        const data = await load(id, signal)
        if (signal.aborted) return
        await Promise.all([...channel.listeners].map(async entry => {
          if (channel.listeners.has(entry)) await entry.onData(data)
        }))
      }, { onError: error => {
        for (const entry of channel.listeners) entry.onError(error)
      } })
    }
    return () => {
      channel.listeners.delete(listener)
      if (!channel.listeners.size) {
        channel.cancel()
        channels.delete(id)
      }
    }
  }
}

import assert from 'node:assert/strict'
import { completionPoll, sharedPoller } from '../src/utils/polling.js'

const originalTimeout = globalThis.setTimeout
const originalClear = globalThis.clearTimeout
const timers = new Map()
let nextId = 0
globalThis.setTimeout = (callback, delay) => {
  const id = ++nextId
  timers.set(id, { callback, delay })
  return id
}
globalThis.clearTimeout = id => timers.delete(id)
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }
async function next(expectedDelay) {
  assert.equal(timers.size, 1)
  const [id, timer] = timers.entries().next().value
  assert.equal(timer.delay, expectedDelay)
  timers.delete(id)
  timer.callback()
  await flush()
}

try {
  let finish
  let active = 0
  let requests = 0
  let requestSignal
  const stop = completionPoll(async signal => {
    requestSignal = signal
    requests++
    active++
    assert.equal(active, 1)
    await new Promise(resolve => { finish = resolve })
    active--
  })
  await flush()
  assert.equal(requests, 1)
  assert.equal(timers.size, 0, 'A slow request must finish before scheduling another')
  finish()
  await flush()
  await next(1000)
  assert.equal(requests, 2)
  stop()
  assert.equal(requestSignal.aborted, true)
  finish()
  await flush()
  assert.equal(timers.size, 0, 'Navigation must cancel pending and future polling')

  let failing = true
  let successes = 0
  const unsubscribe = completionPoll(async () => {
    if (failing) throw { backendUnavailable: true }
    successes++
  })
  await flush()
  for (const delay of [1000, 2000, 4000, 8000, 10000, 10000]) await next(delay)
  failing = false
  await next(10000)
  assert.equal(successes, 1)
  await next(1000)
  assert.equal(successes, 2, 'Polling resumes after recovery at the normal rate')
  unsubscribe()

  let resolveStatus
  const ids = []
  let sharedSignal
  const subscribe = sharedPoller(async (id, signal) => {
    ids.push(id)
    sharedSignal = signal
    return await new Promise(resolve => { resolveStatus = resolve })
  })
  const ontology = []
  const indicator = []
  const stopOntology = subscribe('existing_replay', data => ontology.push(data))
  const stopIndicator = subscribe('existing_replay', data => indicator.push(data))
  assert.deepEqual(ids, ['existing_replay'], 'Both consumers share one status request')
  resolveStatus({ position: 0.5 })
  await flush()
  assert.equal(ontology.length, 1)
  assert.deepEqual(indicator, ontology)
  stopOntology()
  assert.equal(sharedSignal.aborted, false)
  await next(1000)
  stopIndicator()
  assert.equal(sharedSignal.aborted, true)
  resolveStatus({ position: 1 })
  await flush()
  assert.equal(indicator.length, 1, 'Canceled requests cannot publish stale results')
  assert.deepEqual(ids, ['existing_replay', 'existing_replay'])
  assert.equal(timers.size, 0)

  console.log('Replay polling checks passed: slow requests, shared status, bounded backoff, recovery, preserved replay identity, navigation cancellation.')
} finally {
  globalThis.setTimeout = originalTimeout
  globalThis.clearTimeout = originalClear
}

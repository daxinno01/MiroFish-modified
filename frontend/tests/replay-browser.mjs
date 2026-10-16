// Manual Windows browser check using the installed Chromium cache and Node's CDP WebSocket.
// Run after backend/tests/replay_runtime_check.py start in WSL.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../../', import.meta.url))
const folders = await fs.readdir(path.join(root, 'backend/uploads/replays'))
const candidates = await Promise.all(folders.filter(name => name.startsWith('replay_')).map(async name => {
  const filename = path.join(root, 'backend/uploads/replays', name, 'session.json')
  return { filename, text: await fs.readFile(filename, 'utf8') }
}))
const selected = candidates.sort((a, b) => JSON.parse(b.text).created_at - JSON.parse(a.text).created_at)[0]
const session = JSON.parse(selected.text)
const url = `http://localhost:3000/process/${session.project_id}`
const browserRoot = path.join(process.env.LOCALAPPDATA, 'ms-playwright')
const chromium = (await fs.readdir(browserRoot)).filter(name => /^chromium-\d+$/.test(name)).sort().at(-1)
const executable = path.join(browserRoot, chromium, 'chrome-win64/chrome.exe')
const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'mirofish-browser-'))
const browser = spawn(executable, ['--headless=new', '--no-first-run', '--remote-debugging-port=9337',
  `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
async function until(predicate, timeout = 20000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (await predicate()) return
    await sleep(100)
  }
  throw new Error('Browser condition timed out')
}
let ws
try {
  await until(async () => { try { return (await fetch('http://127.0.0.1:9337/json')).ok } catch { return false } })
  const targets = await (await fetch('http://127.0.0.1:9337/json')).json()
  ws = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl)
  await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }))
  let id = 0
  const pending = new Map()
  let onEvent = () => {}
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data)
    if (message.id) {
      const request = pending.get(message.id)
      pending.delete(message.id)
      if (message.error) request?.reject(new Error(JSON.stringify(message.error)))
      else request?.resolve(message.result)
    } else onEvent(message)
  })
  function send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const requestId = ++id
      pending.set(requestId, { resolve, reject })
      ws.send(JSON.stringify({ id: requestId, method, params }))
    })
  }
  const evaluate = async expression => (await send('Runtime.evaluate', { expression, returnByValue: true })).result.value
  let outage = false
  let slow = true
  let statusRequests = 0
  let active = 0
  let maxActive = 0
  let created = 0
  const errors = []
  onEvent = message => {
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text)
    if (message.method === 'Network.requestWillBeSent' && message.params.request.method === 'POST' &&
        message.params.request.url.endsWith('/api/replays')) created++
    if (message.method !== 'Fetch.requestPaused') return
    const { request, requestId } = message.params
    if (!request.url.includes(`/api/replays/${session.replay_id}`)) {
      send('Fetch.continueRequest', { requestId }).catch(() => {})
      return
    }
    statusRequests++
    active++
    maxActive = Math.max(maxActive, active)
    ;(async () => {
      if (slow) await sleep(1600)
      active--
      if (outage) await send('Fetch.fulfillRequest', {
        requestId, responseCode: 503,
        responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
        body: Buffer.from(JSON.stringify({ success: false, code: 'BACKEND_UNAVAILABLE',
          error: 'Backend unavailable; reconnecting' })).toString('base64'),
      })
      else await send('Fetch.continueRequest', { requestId })
    })().catch(() => {})
  }
  // Hold ontology so both actual UI consumers remain mounted throughout the check.
  session.stages.ontology = { started_at: Date.now() / 1000, elapsed: 0, paused: true }
  await fs.writeFile(selected.filename, JSON.stringify(session))
  await send('Page.enable')
  await send('Runtime.enable')
  await send('Network.enable')
  await send('Fetch.enable', { patterns: [{ urlPattern: '*api/replays/*', requestStage: 'Request' }] })
  await send('Page.navigate', { url })
  await until(() => evaluate("document.body.innerText.includes('Saved simulation')"))
  await until(() => statusRequests >= 3)
  assert.equal(maxActive, 1, 'Ontology and indicator must share slow status requests')
  slow = false
  outage = true
  await until(() => evaluate("document.body.innerText.includes('Backend unavailable; reconnecting')"))
  const before = statusRequests
  await sleep(12000)
  assert.ok(statusRequests - before <= 4, 'Outage requests must back off')
  outage = false
  await until(async () => !(await evaluate("document.body.innerText.includes('Backend unavailable; reconnecting')")), 15000)
  assert.equal(created, 0, 'Recovery must retain the existing replay')
  assert.equal(await evaluate('location.pathname'), `/process/${session.project_id}`)
  await evaluate("document.querySelector('.brand').click()")
  await until(() => evaluate("location.pathname === '/'"))
  await sleep(2000)
  const afterNavigation = statusRequests
  await sleep(2500)
  assert.equal(statusRequests, afterNavigation, 'Navigation must stop replay polling')
  assert.deepEqual(errors, [])
  console.log('Browser checks passed: shared slow polling, visible outage message, backoff, recovery without a new replay, navigation cancellation.')
} finally {
  await fs.writeFile(selected.filename, selected.text)
  ws?.close()
  browser.kill()
  await sleep(500)
  assert.equal(path.dirname(path.resolve(profile)), path.resolve(os.tmpdir()))
  assert.ok(path.basename(profile).startsWith('mirofish-browser-'))
  await fs.rm(profile, { recursive: true, force: true }).catch(() => {})
}

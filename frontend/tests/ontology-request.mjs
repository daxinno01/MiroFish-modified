// Run with: node frontend/tests/ontology-request.mjs
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'

// Bundle the actual API modules so Vite's environment expression and extensionless
// imports work in Node. Use Axios's real interceptors with an offline adapter.
async function loadApi(dev, override, origin = 'http://localhost:3000') {
  const bundle = await build({
    stdin: {
      contents: `
        export { API_BASE_URL } from '../src/api/baseUrl.js'
        export { default as service } from '../src/api/index.js'
        export { generateOntology } from '../src/api/graph.js'
        export { getReportDownloadUrl, getTranslatedExportUrl, getExportUrl } from '../src/api/report.js'
        export { setPendingUpload, getPendingUpload, clearPendingUpload } from '../src/store/pendingUpload.js'
      `,
      resolveDir: fileURLToPath(new URL('.', import.meta.url)),
    },
    bundle: true,
    write: false,
    platform: 'node',
    format: 'cjs',
    packages: 'external',
    define: {
      'import.meta.env.VITE_API_BASE_URL': JSON.stringify(override) ?? 'undefined',
      'import.meta.env.DEV': JSON.stringify(dev),
    },
  })
  const module = { exports: {} }
  new Function('module', 'exports', 'require', 'window', bundle.outputFiles[0].text)(
    module, module.exports, createRequire(import.meta.url),
    dev ? { location: { origin } } : undefined,
  )
  return module.exports
}

for (const dev of [true, false]) {
  for (const [override, expectedOverride] of [
    [undefined, null],
    ['', null],
    ['   ', null],
    ['https://api.example.test', 'https://api.example.test'],
    [' https://api.example.test/ ', 'https://api.example.test'],
    ['https://api.example.test/backend/', 'https://api.example.test/backend'],
  ]) {
    const {
      API_BASE_URL, service, generateOntology,
      getReportDownloadUrl, getTranslatedExportUrl, getExportUrl,
      setPendingUpload, getPendingUpload, clearPendingUpload,
    } = await loadApi(dev, override)
    const expectedBase = expectedOverride || (dev ? 'http://localhost:3000' : 'http://localhost:5001')
    assert.equal(API_BASE_URL, expectedBase)
    assert.equal(service.defaults.baseURL, expectedBase)
    assert.equal(service.defaults.timeout, 300000)
    assert.equal(getReportDownloadUrl('report_test'), `${expectedBase}/api/report/report_test/download`)
    assert.equal(getTranslatedExportUrl('report_test'), `${expectedBase}/api/report/report_test/translated-export`)
    assert.equal(getExportUrl('report_test'), `${expectedBase}/api/report/report_test/pdf-export`)

    const previousError = console.error
    console.error = () => {}
    try {
      for (const code of ['ECONNABORTED', 'ETIMEDOUT']) {
        let posts = 0
        service.defaults.adapter = async config => {
          posts++
          assert.equal(config.method, 'post')
          assert.equal(config.url, '/api/graph/ontology/generate')
          assert.equal(config.baseURL, expectedBase)
          throw Object.assign(new Error('timeout of 300000ms exceeded'), { code, config })
        }
        await assert.rejects(generateOntology(new FormData()), /backend may still be processing.*Check your projects/)
        assert.equal(posts, 1, 'A timeout must not resubmit the upload')
      }

      for (const status of [502, 504]) {
        let posts = 0
        service.defaults.adapter = async config => {
          posts++
          throw Object.assign(new Error('Request failed'), {
            config,
            response: { status, data: { success: false, data: { project_id: 'proj_test' }, error: 'Useful backend error' } },
          })
        }
        await assert.rejects(generateOntology(new FormData()), new RegExp(`HTTP ${status}: Useful backend error`))
        assert.equal(posts, 1)
      }

      for (const recordRun of [undefined, true]) {
        setPendingUpload([], 'Test simulation', recordRun)
        assert.equal(getPendingUpload().recordRun, recordRun === true, 'Recording requires opt-in')
        const form = new FormData()
        form.append('record_run', String(getPendingUpload().recordRun))
        let posts = 0
        service.defaults.adapter = async config => {
          posts++
          assert.equal(config.baseURL, expectedBase)
          assert.equal(config.method, 'post')
          assert.equal(config.url, '/api/graph/ontology/generate')
          assert.equal(config.data.get('record_run'), String(recordRun === true))
          return { status: 200, config, data: { success: true, data: { project_id: 'proj_test' } } }
        }
        assert.deepEqual(await generateOntology(form), { success: true, data: { project_id: 'proj_test' } })
        assert.equal(posts, 1)
        clearPendingUpload()
        assert.equal(getPendingUpload().recordRun, false)
      }
    } finally {
      console.error = previousError
    }
  }
}
// The development origin must follow the page, including a custom host and port.
const customOrigin = 'https://dev.example.test:3443'
const customApi = await loadApi(true, undefined, customOrigin)
assert.equal(customApi.service.defaults.baseURL, customOrigin)
assert.equal(customApi.getReportDownloadUrl('report_test'), `${customOrigin}/api/report/report_test/download`)
assert.equal(customApi.getTranslatedExportUrl('report_test'), `${customOrigin}/api/report/report_test/translated-export`)
assert.equal(customApi.getExportUrl('report_test'), `${customOrigin}/api/report/report_test/pdf-export`)
console.log('Frontend checks passed: development proxy, production default, explicit overrides, report links, one POST on timeout/error, backend messages, recording opt-in and successful responses.')

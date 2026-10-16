// Endpoint paths already include /api. In development, the browser origin
// routes them through Vite's proxy to the backend running alongside it in WSL.
const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim()

export const API_BASE_URL = (
  configuredBaseUrl || (import.meta.env.DEV ? window.location.origin : 'http://localhost:5001')
).replace(/\/+$/, '')

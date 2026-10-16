import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import { API_BASE_URL } from './api/baseUrl'

const app = createApp(App)

app.use(router)

app.mount('#app')

console.log('API Base URL:', API_BASE_URL);

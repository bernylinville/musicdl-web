import { createApp } from 'vue'

import App from './App.vue'
import { apiKey, createHttpApi } from './services/api'
import './styles/base.css'

const app = createApp(App)
app.provide(apiKey, createHttpApi())
app.mount('#app')

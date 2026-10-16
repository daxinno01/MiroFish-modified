<template>
  <router-view :key="workflowKey" />
  <SavedSimulationIndicator />
</template>

<script setup>
import SavedSimulationIndicator from "./components/SavedSimulationIndicator.vue"
import { computed } from 'vue'
import { useRoute } from 'vue-router'
const route = useRoute()
const workflowKey = computed(() => Object.values(route.params).some(value => /^(rproj_|rsim_|rreport_)/.test(String(value))) ? route.path : undefined)
// 使用 Vue Router 来管理页面
</script>

<style>
/* 全局样式重置 */
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

#app {
  font-family: 'JetBrains Mono', 'Space Grotesk', 'Noto Sans SC', monospace;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  color: #000000;
  background-color: #ffffff;
}

/* 滚动条样式 */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

::-webkit-scrollbar-track {
  background: #f1f1f1;
}

::-webkit-scrollbar-thumb {
  background: #000000;
}

::-webkit-scrollbar-thumb:hover {
  background: #333333;
}

/* 全局按钮样式 */
button {
  font-family: inherit;
}
</style>

/**
 * 临时存储待上传的文件和需求
 * 
 * files 数组结构：
 * Array<{
 *   raw: File,           // 原始文件对象
 *   name: string,        // 文件名
 *   isImage: boolean,    // 是否为图片
 *   description: string  // 用户输入的图片描述
 * }>
 */
import { reactive } from 'vue'

const state = reactive({
  files: [],
  simulationRequirement: '',
  recordRun: false,
  isPending: false
})

export function setPendingUpload(fileObjects, requirement, recordRun = false) {
  // 这里的 fileObjects 是我们在 Home.vue 中构建的包含 description 的对象数组
  state.files = fileObjects
  state.simulationRequirement = requirement
  state.recordRun = recordRun
  state.isPending = true
}

export function getPendingUpload() {
  // 使用解构确保返回当前状态的快照
  return {
    files: [...state.files], 
    simulationRequirement: state.simulationRequirement,
    recordRun: state.recordRun,
    isPending: state.isPending
  }
}

export function clearPendingUpload() {
  state.files = []
  state.simulationRequirement = ''
  state.recordRun = false
  state.isPending = false
}

export default state

import service, { requestWithRetry } from './index'

/**
 * 生成本体（上传文档和模拟需求）
 * @param {Object} data - 包含files, simulation_requirement, project_name等
 * @returns {Promise}
 */
export function generateOntology(formData) {
  return service({
    url: '/api/graph/ontology/generate',
    method: 'post',
    data: formData,
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
    .catch(error => {
      if (['ECONNABORTED', 'ETIMEDOUT'].includes(error.code)) {
        throw new Error('Ontology request timed out in the browser. The backend may still be processing. Check your projects before submitting again.')
      }
      throw error
    })
}

/**
 * 构建图谱
 * @param {Object} data - 包含project_id, graph_name等
 * @returns {Promise}
 */
export function buildGraph(data, config = {}) {
  return requestWithRetry(() =>
    service({
      ...config,
      url: '/api/graph/build',
      method: 'post',
      data
    })
  )
}

/**
 * 查询任务状态
 * @param {String} taskId - 任务ID
 * @returns {Promise}
 */
export function getTaskStatus(taskId, config = {}) {
  return service({
    ...config,
    url: `/api/graph/task/${taskId}`,
    method: 'get'
  })
}

/**
 * 获取图谱数据
 * @param {String} graphId - 图谱ID
 * @returns {Promise}
 */
export function getGraphData(graphId, config = {}) {
  return service({
    ...config,
    url: `/api/graph/data/${graphId}`,
    method: 'get'
  })
}

/**
 * 获取项目信息
 * @param {String} projectId - 项目ID
 * @returns {Promise}
 */
export function getProject(projectId, config = {}) {
  return service({
    ...config,
    url: `/api/graph/project/${projectId}`,
    method: 'get'
  })
}

/**
 * 获取项目 Token 消耗统计 (Step 1)
 * @param {String} projectId
 */
export const getProjectUsage = (projectId, step = null, config = {}) => {
  const params = step ? { step } : {}
  return service.get(`/api/graph/project/${projectId}/usage`, { ...config, params })
}

/**
 * 列出历史项目
 * @param {number} limit
 */
export function listProjects(limit = 50) {
  return requestWithRetry(() =>
    service({
      url: '/api/graph/project/list',
      method: 'get',
      params: { limit }
    })
  )
}

/**
 * 删除项目
 * @param {String} projectId - 项目ID
 * @returns {Promise}
 */
export function deleteProject(projectId) {
  return service({
    url: `/api/graph/project/${projectId}`,
    method: 'delete'
  })
}

/**
 * 删除图谱
 * @param {String} graphId - 图谱ID
 * @returns {Promise}
 */
export function deleteGraph(graphId) {
  return service({
    url: `/api/graph/delete/${graphId}`,
    method: 'delete'
  })
}

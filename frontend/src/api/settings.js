import service, { requestWithRetry } from './index'

/**
 * @param {string} stage
 */
export const getStagePrompts = (stage) => {
    return service.get(`/api/settings/prompts/${stage}`)
}

export const getPromptStages = (stage) => {
    return service.get(`/api/settings/prompts/stages`)
}

export const saveStagePrompt = (data) => {
    return requestWithRetry(() => service.post("/api/settings/prompts/save", data), 3, 1000)
}

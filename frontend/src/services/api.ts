import axios, { type AxiosResponse } from 'axios'

// ---------------------------------------------------------------------------
// API Base URL - 根据环境自动切换
// ---------------------------------------------------------------------------
let _backendURL: string | null = null

async function getBaseURL(): Promise<string> {
  // 如果已经缓存，直接返回
  if (_backendURL) {
    return _backendURL
  }

  // Electron 环境中，从主进程获取后端 URL
  const electronAPI = (window as any).electronAPI
  if (electronAPI) {
    try {
      const url = await electronAPI.getBackendURL()
      _backendURL = `${url}/api`
      return _backendURL
    } catch (err) {
      console.error('Failed to get backend URL from Electron:', err)
    }
  }

  // 浏览器开发环境，使用相对路径（由 Vite proxy 处理）
  if (window.location.protocol === 'file:') {
    // file:// 协议下，使用默认端口
    _backendURL = 'http://localhost:8765/api'
  } else {
    _backendURL = '/api'
  }
  return _backendURL
}

// 创建 axios 实例，使用默认值（会在请求时动态更新）
const api = axios.create({ baseURL: '/api' })

// 请求拦截器：动态更新 baseURL
api.interceptors.request.use(async (config) => {
  const baseURL = await getBaseURL()
  config.baseURL = baseURL
  return config
})

// ---------------------------------------------------------------------------
// Common types
// ---------------------------------------------------------------------------
export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface PaginationParams {
  page?: number
  page_size?: number
}

// ---------------------------------------------------------------------------
// Settings / LLM Config types (matches backend LlmConfigResponse)
// ---------------------------------------------------------------------------
export interface LlmConfig {
  base_url: string
  api_key: string
  ranking_model: string
  enrichment_model: string
  embedding_model: string
  temperature: number
  max_tokens: number
  timeout: number
  langsmith_api_key: string
  langsmith_project: string
  langsmith_enabled: boolean
}

export interface TestConnectionResponse {
  success: boolean
  models: string[]
  error?: string | null
}

export interface ModelsResponse {
  models: string[]
  updated_at?: string | null
}

// ---------------------------------------------------------------------------
// Product types (matches backend ProductRawOut / ProductEnrichedOut)
// ---------------------------------------------------------------------------
export interface ProductRaw {
  id: number
  row_num: number
  sku: string
  product_name: string
  old_sku: string
  status: string
  created_at: string
  updated_at: string
}

export interface ProductEnriched {
  id: number
  product_id: number
  sku: string
  name: string
  brand: string
  category_l1: string
  category_l2: string
  product_type: string
  usage_scenario: string
  keywords: string[]
  consumables: any[]
  related_accessories: any[]
  typical_purchase_cycle_days?: number | null
  unit_hint: string
  embedding_vector_id: string
  enriched_at: string
  enrichment_confidence: number
  llm_model: string
}

// ---------------------------------------------------------------------------
// Purchase types (matches backend PurchaseOut)
// ---------------------------------------------------------------------------
export interface Purchase {
  id: number
  user_id: string
  sku: string
  product_name: string
  quantity: number
  purchase_date: string
  original_sku: string
  imported_at: string
}

// ---------------------------------------------------------------------------
// User Profile types (matches backend ProfileOut)
// ---------------------------------------------------------------------------
export interface UserProfile {
  id: number
  user_id: string
  profile_json: Record<string, unknown>
  updated_at?: string | null
}

// ---------------------------------------------------------------------------
// Recommendation types (matches backend recommendation items)
// ---------------------------------------------------------------------------
export interface Recommendation {
  id: number
  user_id: string
  recommended_sku: string
  product_name: string
  rank: number
  reason: string
  confidence: number
  source: string
  status: string
  feedback_at?: string | null
  generated_at?: string | null
}

// ---------------------------------------------------------------------------
// Chat types
// ---------------------------------------------------------------------------
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatResponse {
  response: string
  thread_id: string
  action?: {
    type: string        // "confirm_tool_call"
    tool: string        // tool name: "add_purchase", "delete_purchase", etc.
    args: Record<string, unknown>  // tool arguments
    message: string     // human-readable description
  } | null
}

// ===========================================================================
// Settings / LLM Config API
// ===========================================================================

export function getLlmConfig(): Promise<AxiosResponse<LlmConfig>> {
  return api.get('/settings/llm-config')
}

export function saveLlmConfig(data: LlmConfig): Promise<AxiosResponse<LlmConfig>> {
  return api.post('/settings/llm-config', data)
}

export function testConnection(
  base_url: string,
  api_key: string,
): Promise<AxiosResponse<TestConnectionResponse>> {
  return api.post('/settings/test-connection', { base_url, api_key })
}

export function getModels(): Promise<AxiosResponse<ModelsResponse>> {
  return api.get('/settings/models')
}

// ===========================================================================
// Products API (raw)
// ===========================================================================

export function getProducts(params?: {
  page?: number
  page_size?: number
  search?: string
  status?: string
}): Promise<AxiosResponse<PaginatedResponse<ProductRaw>>> {
  return api.get('/products', { params })
}

export function getProduct(id: number): Promise<AxiosResponse<ProductRaw>> {
  return api.get(`/products/${id}`)
}

export function createProduct(data: {
  row_num?: number
  sku: string
  product_name: string
  old_sku?: string
  status?: string
}): Promise<AxiosResponse<ProductRaw>> {
  return api.post('/products', data)
}

export function updateProduct(
  id: number,
  data: {
    row_num?: number
    sku?: string
    product_name?: string
    old_sku?: string
    status?: string
  },
): Promise<AxiosResponse<ProductRaw>> {
  return api.put(`/products/${id}`, data)
}

export function deleteProduct(id: number): Promise<AxiosResponse<{ ok: boolean }>> {
  return api.delete(`/products/${id}`)
}

export function exportProducts(status?: string): Promise<AxiosResponse<Blob>> {
  return api.get('/products/export', {
    params: status ? { status } : {},
    responseType: 'blob',
  })
}

export function batchUpdateProductStatus(
  productIds: number[],
  status: string,
): Promise<AxiosResponse<{ ok: boolean }>> {
  return api.post('/products/batch-status', { product_ids: productIds, status })
}

export function importProducts(
  file: File,
): Promise<AxiosResponse<{ imported: number; skipped: number; conflicts?: { old_sku: string; new_sku: string; existing_sku: string }[] }>> {
  const formData = new FormData()
  formData.append('file', file)
  return api.post('/products/import', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

// ===========================================================================
// Products API (enriched)
// ===========================================================================

export function getEnrichedProducts(params?: {
  page?: number
  page_size?: number
}): Promise<AxiosResponse<PaginatedResponse<ProductEnriched>>> {
  return api.get('/products/enriched', { params })
}

export function getEnrichedProduct(id: number): Promise<AxiosResponse<ProductEnriched>> {
  return api.get(`/products/enriched/${id}`)
}

export function enrichProducts(data?: {
  product_ids?: number[]
  batch_size?: number
}): Promise<AxiosResponse<{ total: number; enriched: number; failed: number }>> {
  return api.post('/products/enrich', data || {})
}

export interface EnrichProgressEvent {
  type: 'start' | 'progress' | 'done' | 'concurrency_change'
  total?: number
  current?: number
  sku?: string
  concurrency?: number
  product_name?: string
  success?: boolean
  error?: string
  enriched?: number
  failed?: number
  already_enriched?: number
}

export function enrichProductsStream(
  data: { product_ids?: number[]; batch_size?: number },
  onEvent: (event: EnrichProgressEvent) => void,
  onError?: (error: Error) => void,
): () => void {
  const controller = new AbortController()

  // 获取后端 URL（异步）
  getBaseURL().then(baseURL => {
    const streamURL = baseURL.replace('/api', '') + '/api/products/enrich-stream'

    fetch(streamURL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }
        const reader = response.body?.getReader()
        if (!reader) return
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''
          for (const line of lines) {
            const trimmed = line.trim()
            if (trimmed) {
              try {
                onEvent(JSON.parse(trimmed))
              } catch { /* skip malformed lines */ }
            }
          }
        }
      })
      .catch((err) => {
        if (err.name !== 'AbortError') {
          onError?.(err)
        }
      })
  })

  return () => controller.abort()
}

export function buildProductRelations(clearExisting?: boolean): Promise<AxiosResponse<{
  consumable: number; accessory: number; same_category: number; complementary: number; same_series: number; co_purchased: number; total: number
}>> {
  return api.post(`/products/build-relations?clear_existing=${clearExisting ? 'true' : 'false'}`)
}

// Graph visualization types
export interface GraphNode {
  id: string
  name: string
  category: string
  value: number
}

export interface GraphLink {
  source: string
  target: string
  relation_type: string
  weight: number
  description: string
}

export interface GraphData {
  nodes: GraphNode[]
  links: GraphLink[]
  categories: string[]
}

export interface RelationType {
  value: string
  label: string
  count: number
}

export function getProductGraph(params?: {
  relation_type?: string
  limit?: number
}): Promise<AxiosResponse<GraphData>> {
  return api.get('/products/graph', { params })
}

export function getRelationTypes(): Promise<AxiosResponse<{
  types: RelationType[]
  total: number
}>> {
  return api.get('/products/relation-types')
}

// ===========================================================================
// Purchases API
// ===========================================================================

export function getPurchases(params?: {
  page?: number
  page_size?: number
  user_id?: string
  date_from?: string
  date_to?: string
}): Promise<AxiosResponse<PaginatedResponse<Purchase>>> {
  return api.get('/purchases', { params })
}

export function createPurchase(data: {
  user_id: string
  sku: string
  product_name: string
  quantity?: number
  purchase_date: string
  original_sku?: string
}): Promise<AxiosResponse<Purchase>> {
  return api.post('/purchases', data)
}

export function updatePurchase(
  id: number,
  data: {
    user_id?: string
    sku?: string
    product_name?: string
    quantity?: number
    purchase_date?: string
    original_sku?: string
  },
): Promise<AxiosResponse<Purchase>> {
  return api.put(`/purchases/${id}`, data)
}

export function deletePurchase(id: number): Promise<AxiosResponse<{ ok: boolean }>> {
  return api.delete(`/purchases/${id}`)
}

export function importPurchases(
  file: File,
): Promise<AxiosResponse<{ imported: number; skipped: number; note?: string }>> {
  const formData = new FormData()
  formData.append('file', file)
  return api.post('/purchases/import', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

// ===========================================================================
// User Profiles API
// ===========================================================================

export function getProfiles(params?: {
  page?: number
  page_size?: number
}): Promise<AxiosResponse<PaginatedResponse<UserProfile>>> {
  return api.get('/profiles', { params })
}

export function getProfile(userId: string): Promise<AxiosResponse<UserProfile>> {
  return api.get(`/profiles/${userId}`)
}

export function computeProfile(
  userId: string,
): Promise<AxiosResponse<{ message: string; profile: Record<string, unknown> }>> {
  return api.post(`/profiles/compute/${userId}`)
}

export function updateProfile(
  userId: string,
  data: Partial<{
    basic_info: Record<string, unknown>
    category_preference: string[]
    brand_preference: string[]
    purchase_cycle: Record<string, unknown>
    consumable_alerts: string[]
    recency_score: number
    value_tier: string
  }>,
): Promise<AxiosResponse<UserProfile>> {
  return api.put(`/profiles/${userId}`, data)
}

// ===========================================================================
// Recommendations API
// ===========================================================================

export function getRecommendations(params?: {
  page?: number
  page_size?: number
  user_id?: string
}): Promise<AxiosResponse<PaginatedResponse<Recommendation>>> {
  return api.get('/recommendations', { params })
}

export function getUserRecommendations(
  userId: string,
): Promise<AxiosResponse<{ user_id: string; items: Recommendation[] }>> {
  return api.get(`/recommendations/${userId}`)
}

export function generateRecommendations(
  userId: string,
): Promise<AxiosResponse<{ message: string; items: Recommendation[] }>> {
  return api.post(`/recommendations/generate/${userId}`)
}

export function generateAllRecommendations(): Promise<
  AxiosResponse<{
    message: string
    results: { user_id: string; count: number }[]
    errors: { user_id: string; error: string }[]
  }>
> {
  return api.post('/recommendations/generate-all')
}

export function updateFeedback(
  id: number,
  status: 'accepted' | 'rejected' | 'modified',
  feedback_note?: string,
  modification?: string,
): Promise<AxiosResponse<{ message: string }>> {
  return api.put(`/recommendations/${id}/feedback`, {
    status,
    feedback_note: feedback_note || '',
    modification: modification || '',
  })
}

// ===========================================================================
// Chat API
// ===========================================================================

export function sendMessage(
  message: string,
  thread_id?: string | null,
): Promise<AxiosResponse<ChatResponse>> {
  return api.post('/chat', { message, thread_id: thread_id || undefined })
}

export function resumeChat(
  thread_id: string,
  approved: boolean,
): Promise<AxiosResponse<ChatResponse>> {
  return api.post('/chat/resume', { thread_id, approved })
}

// Streaming chat API
export type StreamComponent = 'thinking' | 'tool_call' | 'tool_execution' | 'tool_result' | 'tool_error' | 'flow' | 'response' | 'action' | 'done'

export interface StreamEvent {
  type: 'thread_id' | 'thinking' | 'tool_call' | 'tool_result' | 'token' | 'action' | 'done' | 'error'
  component?: StreamComponent
  thread_id?: string
  content?: string
  tool?: string
  input?: string
  output?: string
  action?: {
    type: string
    tool: string
    args: Record<string, unknown>
    message: string
    tool_call_id: string
  }
  response?: string
  message?: string
}

export function sendMessageStream(
  message: string,
  thread_id: string | null | undefined,
  onEvent: (event: StreamEvent) => void,
  onError?: (error: Error) => void,
): () => void {
  const controller = new AbortController()

  getBaseURL().then(baseURL => {
    const streamURL = baseURL.replace('/api', '') + '/api/chat/stream'

    fetch(streamURL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, thread_id: thread_id || undefined }),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }
        const reader = response.body?.getReader()
        if (!reader) return
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''
          for (const line of lines) {
            const trimmed = line.trim()
            if (trimmed.startsWith('data: ')) {
              const data = trimmed.slice(6)
              if (data === '[DONE]') return
              try {
                onEvent(JSON.parse(data))
              } catch { /* skip malformed lines */ }
            }
          }
        }
      })
      .catch((err) => {
        if (err.name !== 'AbortError') {
          onError?.(err)
        }
      })
  })

  return () => controller.abort()
}

export function resumeChatStream(
  thread_id: string,
  approved: boolean,
  onEvent: (event: StreamEvent) => void,
  onError?: (error: Error) => void,
): () => void {
  const controller = new AbortController()

  getBaseURL().then(baseURL => {
    const streamURL = baseURL.replace('/api', '') + '/api/chat/resume-stream'

    fetch(streamURL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thread_id, approved }),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }
        const reader = response.body?.getReader()
        if (!reader) return
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''
          for (const line of lines) {
            const trimmed = line.trim()
            if (trimmed.startsWith('data: ')) {
              const data = trimmed.slice(6)
              if (data === '[DONE]') return
              try {
                onEvent(JSON.parse(data))
              } catch { /* skip malformed lines */ }
            }
          }
        }
      })
      .catch((err) => {
        if (err.name !== 'AbortError') {
          onError?.(err)
        }
      })
  })

  return () => controller.abort()
}

// ===========================================================================
// Default export
// ===========================================================================
export default api

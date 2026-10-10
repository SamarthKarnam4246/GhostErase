import type {
  CreateDocumentResponse,
  CfoConnectResponse,
  CfoStatus,
  DashboardResponse,
  DocumentSummary,
  HealthResponse,
  SearchResult,
  VerificationResponse,
} from '../types'

const BASE_URL = '/api/v1'

async function readResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `Request failed with status ${response.status}`)
  }
  return response.json() as Promise<T>
}

export async function healthCheck(): Promise<HealthResponse> {
  return readResponse(await fetch(`${BASE_URL}/health`))
}

export async function dashboardData(): Promise<DashboardResponse> {
  return readResponse(await fetch(`${BASE_URL}/dashboard`))
}

export async function deletionReport(operationId: string): Promise<VerificationResponse> {
  return readResponse(await fetch(`${BASE_URL}/deletion-operations/${operationId}`))
}

export async function cfoStatus(): Promise<CfoStatus> {
  return readResponse(await fetch(`${BASE_URL}/integrations/cfo`))
}

export async function connectCfo(): Promise<CfoConnectResponse> {
  return readResponse(await fetch(`${BASE_URL}/integrations/cfo/connect`, { method: 'POST' }))
}

export async function selectCfoWorkspace(workspaceId: string): Promise<CfoStatus> {
  return readResponse(await fetch(`${BASE_URL}/integrations/cfo/workspace`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace_id: workspaceId }),
  }))
}

export async function readCfoFinancialData(toolName: string, arguments_: Record<string, unknown>): Promise<CfoStatus> {
  return readResponse(await fetch(`${BASE_URL}/integrations/cfo/read`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tool_name: toolName, arguments: arguments_ }),
  }))
}

export async function disconnectCfo(): Promise<CfoStatus> {
  return readResponse(await fetch(`${BASE_URL}/integrations/cfo`, { method: 'DELETE' }))
}

export async function uploadDocument(file: File): Promise<CreateDocumentResponse> {
  const form = new FormData()
  form.set('file', file)
  return readResponse(await fetch(`${BASE_URL}/documents/upload`, {
    method: 'POST',
    body: form,
  }))
}

export async function listDocuments(): Promise<DocumentSummary[]> {
  return readResponse(await fetch(`${BASE_URL}/documents`))
}

export async function searchDocuments(query: string): Promise<SearchResult[]> {
  const payload = await readResponse<{ results: SearchResult[] }>(await fetch(`${BASE_URL}/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, limit: 10 }),
  }))
  return payload.results
}

export async function deleteDocument(documentId: string, token: string): Promise<VerificationResponse> {
  return readResponse(await fetch(`${BASE_URL}/documents/${documentId}/delete`, {
    method: 'POST',
    headers: { 'X-Delete-Token': token },
  }))
}

export async function verifyDocument(documentId: string): Promise<VerificationResponse> {
  return readResponse(await fetch(`${BASE_URL}/documents/${documentId}/verify`, { method: 'POST' }))
}

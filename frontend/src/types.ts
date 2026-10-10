export type ServiceState =
  | 'pass'
  | 'fail'
  | 'blocked'
  | 'not_verified'
  | 'indexed'
  | 'deleted'
  | 'deletion_failed'
  | 'deletion_blocked'

export interface HealthResponse {
  status: string
  app: string
  vector_store: string
  embedding_provider: string
  deletion_authorization_configured: boolean
  neatlogs: {
    status: 'blocked' | 'unverified'
    verified: boolean
    detail: string
  }
}

export type CfoConnectionState =
  | 'not_connected'
  | 'awaiting_browser_authorization'
  | 'authentication_failed'
  | 'workspace_selection_required'
  | 'connected_but_not_verified'
  | 'verified'
  | 'reauthentication_required'
  | 'integration_blocked'

export interface CfoWorkspace {
  id: string
  name: string
}

export interface CfoReadTool {
  name: 'inspect_pages' | 'inspect_table_blocks'
  description: string | null
  input_schema: Record<string, unknown>
}

export interface CfoStatus {
  state: CfoConnectionState
  detail: string
  server: string
  verified: boolean
  workspaces: CfoWorkspace[]
  selected_workspace_id: string | null
  read_tools: CfoReadTool[]
  last_inspection: Record<string, unknown> | null
  financial_result: {
    status: 'insufficient_financial_data'
    tool?: string
    detail: string
    result?: unknown
  } | null
}

export interface CfoConnectResponse {
  state: 'awaiting_browser_authorization'
  authorization_url: string
  callback_uri: string
}

export interface CreateDocumentResponse {
  id: string
  source: string
  filename?: string
  status: ServiceState
  chunk_ids: string[]
  content_hash: string
}

export interface DocumentSummary {
  id: string
  filename: string
  source: string
  status: ServiceState
  created_at: string | null
  latest_deletion_status: string | null
  latest_deletion_operation_id: string | null
  chunk_count: number
  content_hash: string
}

export interface DashboardResponse {
  counts: {
    indexed_documents: number
    documents_by_status: Record<string, number>
    deletion_operations: number
    deletion_operations_by_status: Record<string, number>
    checks: Record<'pass' | 'fail' | 'blocked' | 'not_verified', number>
  }
  recent_operations: Array<{
    operation_id: string
    document_id: string
    document_name: string
    status: string
    created_at: string
    completed_at: string | null
  }>
  integrations: Record<string, {
    state: string
    verified: boolean
    detail: string
  }>
  financial_impact: {
    status: string
    detail: string
    result: unknown | null
    tool?: string
  }
}

export interface DeletionCheck {
  check_id: string
  operation_id: string
  checked_at: string
  document_id: string
  chunk_ids: string[]
  layer: string
  method: string
  status: ServiceState
  actual_result: Record<string, unknown>
  error_detail: string | null
}

export interface VerificationResponse {
  operation_id: string
  document_id: string
  chunk_ids: string[]
  status: ServiceState
  created_at: string
  completed_at: string | null
  checks: DeletionCheck[]
  scope: {
    api_level_accessibility_only: boolean
    physical_erasure_verified: boolean
    not_examined: string[]
    checked_layers: Array<{ layer: string; method: string; status: ServiceState }>
    storage_paths_checked?: Record<string, unknown>
    conclusion?: string
  }
}

export interface SearchResult {
  chunk_id: string
  document_id: string
  chunk_index: number
  content: string
  distance: number
}

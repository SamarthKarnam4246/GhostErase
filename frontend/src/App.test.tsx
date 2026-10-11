import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from './App.tsx'
import type { DocumentSummary } from './types.ts'

afterEach(() => cleanup())

const health = {
  status: 'ok',
  app: 'GhostErase',
  vector_store: 'ChromaDB PersistentClient',
  embedding_provider: 'ChromaDB DefaultEmbeddingFunction (all-MiniLM-L6-v2 ONNX)',
  deletion_authorization_configured: false,
  neatlogs: { status: 'blocked', verified: false, detail: 'Not configured.' },
}

const cfo = {
  state: 'not_connected',
  detail: 'Not connected.',
  server: 'https://api.cfo.ai/mcp',
  verified: false,
  workspaces: [],
  selected_workspace_id: null,
  read_tools: [],
  last_inspection: null,
  financial_result: null,
}

const failedDoc: DocumentSummary = {
  id: 'doc-fail',
  filename: 'failed.txt',
  source: 'upload',
  status: 'deletion_failed',
  created_at: '2026-10-10T00:00:00+00:00',
  latest_deletion_status: 'fail',
  latest_deletion_operation_id: 'op-fail',
  chunk_count: 1,
  content_hash: 'abc123',
}

const failedReport = {
  operation_id: 'op-fail',
  document_id: 'doc-fail',
  chunk_ids: ['chunk-1'],
  status: 'fail',
  kind: 'deletion',
  served_from_history: false,
  created_at: '2026-10-10T00:00:00+00:00',
  completed_at: '2026-10-10T00:01:00+00:00',
  checks: [
    {
      check_id: 'c-source',
      operation_id: 'op-fail',
      checked_at: '2026-10-10T00:00:10+00:00',
      document_id: 'doc-fail',
      chunk_ids: ['chunk-1'],
      layer: 'source_file',
      method: 'path.unlink_and_path.exists',
      status: 'pass',
      actual_result: {},
      error_detail: null,
    },
    {
      check_id: 'c-residual',
      operation_id: 'op-fail',
      checked_at: '2026-10-10T00:00:20+00:00',
      document_id: 'doc-fail',
      chunk_ids: ['chunk-1'],
      layer: 'chromadb_api',
      method: 'collection.get(where=document_id)',
      status: 'fail',
      actual_result: { residual_chunk_ids: ['chunk-1'], residual_count: 1 },
      error_detail: null,
    },
    {
      check_id: 'c-retrieval',
      operation_id: 'op-fail',
      checked_at: '2026-10-10T00:00:30+00:00',
      document_id: 'doc-fail',
      chunk_ids: ['chunk-1'],
      layer: 'application_retrieval',
      method: 'query',
      status: 'fail',
      actual_result: {},
      error_detail: null,
    },
  ],
  scope: {
    api_level_accessibility_only: true,
    physical_erasure_verified: false,
    not_examined: ['backups'],
    checked_layers: [],
    conclusion: 'At least one required check failed.',
  },
}

const lineage = {
  document: failedDoc,
  artifacts: [
    {
      artifact_id: 'doc-fail:source',
      artifact_type: 'source_file',
      backend: 'filesystem',
      identifier: '/data/uploads/doc-fail/failed.txt',
      parent_id: null,
      status: 'active',
      created_at: '2026-10-10T00:00:00+00:00',
      updated_at: '2026-10-10T00:00:00+00:00',
    },
  ],
  cache_entries: [],
  lineage_note: 'Known application-tracked artifacts only. External copies remain unverified.',
}

const explanationUnavailable = {
  operation_id: 'op-fail',
  verified_status: 'fail',
  advisory_only: true,
  note: 'Suggestions only.',
  evidence_check_ids: ['c-source'],
  outcome: 'unavailable',
  provider: null,
  summary: null,
  likely_causes: [],
  suggested_steps: [],
  dropped_unsafe_steps: 0,
  detail: 'No AI explanation provider is configured.',
}

const scenario = {
  documents: [] as DocumentSummary[],
  dashboardOperations: [] as Array<Record<string, unknown>>,
  failFetch: null as string | null,
  deleteDenied: false,
  blockUpload: null as Promise<unknown> | null,
  useHistory: false,
  useFreshVerify: false,
  searchHits: [] as Array<Record<string, unknown>>,
  deleteReport: {
    operation_id: 'op-9',
    document_id: 'doc-1',
    chunk_ids: [],
    status: 'pass',
    created_at: '2026-10-10T00:00:00+00:00',
    completed_at: '2026-10-10T00:01:00+00:00',
    checks: [],
    scope: {
      api_level_accessibility_only: true,
      physical_erasure_verified: false,
      not_examined: [],
      checked_layers: [],
    },
  } as Record<string, unknown>,
}

function route(url: string): unknown {
  if (url.endsWith('/health')) return health
  if (url.endsWith('/dashboard')) {
    return {
      counts: {
        indexed_documents: scenario.documents.length,
        documents_by_status: {},
        deletion_operations: scenario.dashboardOperations.length,
        deletion_operations_by_status: {},
        checks: { pass: 0, fail: 1, blocked: 0, not_verified: 0 },
      },
      recent_operations: scenario.dashboardOperations,
      integrations: {
        neatlogs_runtime: { state: 'unverified', verified: false, detail: 'Trace readback not verified.' },
        entire_copilot_cli: { state: 'not_configured', verified: false, detail: 'No capture configured.' },
        cfo_ai_runtime: { state: 'not_connected', verified: false, detail: 'Not connected.' },
      },
      financial_impact: { status: 'unavailable', detail: 'No data.', result: null },
    }
  }
  if (url.endsWith('/documents')) return scenario.documents
  if (url.endsWith('/integrations/cfo')) return cfo
  if (url.includes('/lineage')) return lineage
  if (url.endsWith('/explain')) return explanationUnavailable
  if (url.endsWith('/documents/upload')) {
    return { id: 'new-doc', source: 'upload', filename: 'a.txt', status: 'indexed', chunk_ids: ['c1'], content_hash: 'h' }
  }
  if (url.endsWith('/verify')) {
    return {
      ...failedReport,
      operation_id: 'op-fresh',
      kind: 'verification',
      served_from_history: false,
    }
  }
  if (url.includes('/deletion-operations/')) {
    return scenario.useHistory ? { ...failedReport, served_from_history: true } : failedReport
  }
  if (url.endsWith('/delete')) return scenario.deleteReport
  if (url.endsWith('/search')) return { query: '', results: scenario.searchHits }
  throw new Error(`unexpected request: ${url}`)
}

beforeEach(() => {
  scenario.documents = []
  scenario.dashboardOperations = []
  scenario.failFetch = null
  scenario.deleteDenied = false
  scenario.blockUpload = null
  document.documentElement.removeAttribute('data-theme')
  window.localStorage.removeItem('ghosterase-theme')
  scenario.useHistory = false
  Object.defineProperty(window, 'confirm', { value: vi.fn(() => true), configurable: true })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: unknown) => {
      const url = String(input)
      if (scenario.failFetch && url.includes(scenario.failFetch)) throw new TypeError('Network error')
      if (scenario.blockUpload && url.endsWith('/documents/upload')) await scenario.blockUpload
      if (scenario.deleteDenied && url.includes('/documents/') && url.endsWith('/delete')) {
        return {
          ok: false,
          status: 403,
          json: async () => ({ detail: 'A configured deletion authorization token is required.' }),
        } as unknown as Response
      }
      return {
        ok: true,
        json: async () => route(url),
      } as unknown as Response
    }),
  )
})

describe('overview', () => {
  it('shows real counts and empty states', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Overview' })).toBeDefined())
    expect(screen.getByText('No deletion operations have been recorded.')).toBeDefined()
    expect(screen.getByText('Ready. Upload a .txt or .md file to index it.')).toBeDefined()
  })

  it('labels unsupported integrations as text, not color alone', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getAllByText('not configured').length).toBeGreaterThan(0))
    expect(screen.getAllByText('not connected').length).toBeGreaterThan(0)
  })
})

describe('documents', () => {
  it('shows empty state and requires a token before deleting', async () => {
    scenario.documents = []
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    await waitFor(() => expect(screen.getByText('No indexed documents.')).toBeDefined())
  })

  it('expands lineage details with unknown-copies note', async () => {
    scenario.documents = [failedDoc]
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    await waitFor(() => expect(screen.getByText('Retry deletion and verify')).toBeDefined())
    fireEvent.click(screen.getByRole('button', { name: 'Details & lineage' }))
    await waitFor(() => expect(screen.getByText('Known lineage')).toBeDefined())
    expect(screen.getByText('source_file')).toBeDefined()
    expect(screen.getByText('Known application-tracked artifacts only. External copies remain unverified.')).toBeDefined()
    fireEvent.click(screen.getByRole('button', { name: 'Hide details & lineage' }))
    await waitFor(() =>
      expect(screen.queryByText('Known lineage')).toBeNull(),
    )
  })
})

describe('failed report', () => {  it('shows fail status with retrieval and storage indicators', async () => {
    scenario.documents = [failedDoc]
    scenario.dashboardOperations = [
      {
        operation_id: 'op-fail',
        document_id: 'doc-fail',
        document_name: 'failed.txt',
        status: 'fail',
        created_at: '2026-10-10T00:00:00+00:00',
        completed_at: '2026-10-10T00:01:00+00:00',
      },
    ]
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    await waitFor(() => expect(screen.getByText('View latest report')).toBeDefined())
    fireEvent.click(screen.getByText('View latest report'))
    await waitFor(() => expect(screen.getByText('Overall verification status:')).toBeDefined())
    expect(screen.getByText('Retrieval blocked:').parentElement?.textContent).toContain('No')
    expect(screen.getByText('At least one required check failed.')).toBeDefined()
  })

  it('shows unavailable advisory state without changing verified status', async () => {
    scenario.documents = [failedDoc]
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    await waitFor(() => expect(screen.getByText('View latest report')).toBeDefined())
    fireEvent.click(screen.getByText('View latest report'))
    await waitFor(() => expect(screen.getByText('Explain this report')).toBeDefined())
    fireEvent.click(screen.getByText('Explain this report'))
    await waitFor(() =>
      expect(screen.getByText('No AI explanation provider is configured.')).toBeDefined(),
    )
    expect(screen.getByText('Overall verification status:').parentElement?.textContent).toContain('fail')
  })
})

describe('failures', () => {
  it('shows backend-unavailable when the network fails', async () => {
    scenario.failFetch = '/health'
    render(<App />)
    await waitFor(() => expect(screen.getByText('Network error')).toBeDefined())
  })

  it('surfaces server detail on unauthorized delete', async () => {
    scenario.documents = [
      { ...failedDoc, status: 'indexed', latest_deletion_status: null, latest_deletion_operation_id: null },
    ]
    scenario.deleteDenied = true
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    await waitFor(() => expect(screen.getByText('Delete and verify')).toBeDefined())
    fireEvent.change(screen.getByLabelText(/Deletion authorization token/), { target: { value: 'wrong' } })
    fireEvent.click(screen.getByText('Delete and verify'))
    await waitFor(() =>
      expect(screen.getByText('A configured deletion authorization token is required.')).toBeDefined(),
    )
  })
})

describe('post-delete freshness', () => {
  it('clears search hits and lineage snapshots after delete', async () => {
    scenario.documents = [
      { ...failedDoc, id: 'doc-1', status: 'indexed', latest_deletion_status: null, latest_deletion_operation_id: null },
    ]
    scenario.searchHits = [
      { chunk_id: 'c1', document_id: 'doc-1', chunk_index: 0, content: 'stale snippet', distance: 0.1 },
    ]
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    await waitFor(() => expect(screen.getByText('Delete and verify')).toBeDefined())
    fireEvent.change(screen.getByLabelText(/Query/), { target: { value: 'stale' } })
    fireEvent.click(screen.getByText('Search ChromaDB'))
    await waitFor(() => expect(screen.getByText('stale snippet')).toBeDefined())
    fireEvent.click(screen.getByText('Details & lineage'))
    await waitFor(() => expect(screen.getByText('Known lineage')).toBeDefined())
    fireEvent.change(screen.getByLabelText(/Deletion authorization token/), { target: { value: 'token' } })
    fireEvent.click(screen.getByText('Delete and verify'))
    await waitFor(() => expect(screen.getByText('Deletion operation op-9: pass.')).toBeDefined())
    expect(screen.queryByText('stale snippet')).toBeNull()
    expect(screen.queryByText('Known lineage')).toBeNull()
  })

  it('keeps search hits and details when delete fails', async () => {
    scenario.documents = [
      { ...failedDoc, id: 'doc-1', status: 'indexed', latest_deletion_status: null, latest_deletion_operation_id: null },
    ]
    scenario.searchHits = [
      { chunk_id: 'c1', document_id: 'doc-1', chunk_index: 0, content: 'stale snippet', distance: 0.1 },
    ]
    scenario.deleteDenied = true
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    await waitFor(() => expect(screen.getByText('Delete and verify')).toBeDefined())
    fireEvent.change(screen.getByLabelText(/Query/), { target: { value: 'stale' } })
    fireEvent.click(screen.getByText('Search ChromaDB'))
    await waitFor(() => expect(screen.getByText('stale snippet')).toBeDefined())
    fireEvent.click(screen.getByText('Details & lineage'))
    await waitFor(() => expect(screen.getByText('Known lineage')).toBeDefined())
    fireEvent.change(screen.getByLabelText(/Deletion authorization token/), { target: { value: 'wrong' } })
    fireEvent.click(screen.getByText('Delete and verify'))
    await waitFor(() =>
      expect(screen.getByText('A configured deletion authorization token is required.')).toBeDefined(),
    )
    expect(screen.getByText('stale snippet')).toBeDefined()
    expect(screen.getByText('Known lineage')).toBeDefined()
  })
})

describe('verification freshness', () => {
  it('labels history-served reports and runs fresh verification on demand', async () => {
    scenario.documents = [failedDoc]
    scenario.useHistory = true
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    await waitFor(() => expect(screen.getByText('View latest report')).toBeDefined())
    fireEvent.click(screen.getByText('View latest report'))
    await waitFor(() => expect(screen.getByText(/Served from history/)).toBeDefined())
    expect(screen.getByText('Run type:').parentElement?.textContent).toContain('Deletion')
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    await waitFor(() => expect(screen.getByText('Verify now')).toBeDefined())
    fireEvent.click(screen.getByText('Verify now'))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Operation op-fresh' })).toBeDefined())
    expect(screen.getByText('Run type:').parentElement?.textContent).toContain('Fresh verification')
  })
})

describe('theme and progress', () => {
  it('toggles light and dark themes and persists the choice', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByText(/Ready\./)).toBeDefined())
    expect(document.documentElement.dataset.theme).toBe('dark')
    fireEvent.click(screen.getByText('Switch to light theme'))
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(window.localStorage.getItem('ghosterase-theme')).toBe('light')
    expect(screen.getByText('Switch to dark theme')).toBeDefined()
  })

  it('shows a working indicator while busy', async () => {
    let release!: () => void
    scenario.blockUpload = new Promise<void>((resolve) => {
      release = resolve
    })
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    await waitFor(() => expect(screen.getByText('Upload a document')).toBeDefined())
    const picked = new File(['stale body'], 'a.txt', { type: 'text/plain' })
    fireEvent.change(screen.getByLabelText(/\.txt or \.md file/), { target: { files: [picked] } })
    fireEvent.click(screen.getByText('Upload and index'))
    await waitFor(() => expect(screen.getByText('Working…')).toBeDefined())
    release()
    await waitFor(() => expect(screen.getByText('Indexed a.txt as new-doc (1 chunks).')).toBeDefined())
    expect(screen.queryByText('Working…')).toBeNull()
  })

  it('resets the file picker after a successful upload', async () => {
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    await waitFor(() => expect(screen.getByText('Upload a document')).toBeDefined())
    const picker = screen.getByLabelText(/\.txt or \.md file/)
    fireEvent.change(picker, { target: { files: [new File(['fresh body'], 'b.txt', { type: 'text/plain' })] } })
    fireEvent.click(screen.getByText('Upload and index'))
    await waitFor(() => expect(screen.getByText(/Indexed a.txt as new-doc/)).toBeDefined())
    // The picker must remount so re-selecting the same file fires onChange.
    expect(screen.getByLabelText(/\.txt or \.md file/)).not.toBe(picker)
  })
})

describe('failure guidance', () => {
  it('explains next steps on a failed report', async () => {
    scenario.documents = [failedDoc]
    scenario.dashboardOperations = [
      {
        operation_id: 'op-fail',
        document_id: 'doc-fail',
        document_name: 'failed.txt',
        status: 'fail',
        created_at: '2026-10-10T00:00:00+00:00',
        completed_at: '2026-10-10T00:01:00+00:00',
      },
    ]
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Documents' }))
    await waitFor(() => expect(screen.getByText('View latest report')).toBeDefined())
    fireEvent.click(screen.getByText('View latest report'))
    await waitFor(() => expect(screen.getByText('What to do next')).toBeDefined())
    expect(screen.getByText(/invalidate a residual cache entry/)).toBeDefined()
  })
})

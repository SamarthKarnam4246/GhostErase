import { useEffect, useState } from 'react'
import './App.css'
import {
  deleteDocument,
  cfoStatus as fetchCfoStatus,
  connectCfo,
  dashboardData,
  deletionReport,
  disconnectCfo,
  healthCheck,
  listDocuments,
  readCfoFinancialData,
  searchDocuments,
  selectCfoWorkspace,
  uploadDocument,
} from './api/client'
import type {
  CfoStatus,
  DashboardResponse,
  DocumentSummary,
  HealthResponse,
  SearchResult,
  VerificationResponse,
} from './types'

function App() {
  const [screen, setScreen] = useState<'overview' | 'documents' | 'reports' | 'integrations'>('overview')
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null)
  const [cfo, setCfo] = useState<CfoStatus | null>(null)
  const [cfoAuthorizationUrl, setCfoAuthorizationUrl] = useState<string | null>(null)
  const [workspaceSelection, setWorkspaceSelection] = useState('')
  const [cfoArguments, setCfoArguments] = useState('{}')
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [deleteToken, setDeleteToken] = useState('')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [report, setReport] = useState<VerificationResponse | null>(null)
  const [message, setMessage] = useState('Loading the local service status…')
  const [busy, setBusy] = useState(false)
  const financialReadTool = cfo?.read_tools.find((tool) => tool.name === 'inspect_table_blocks')

  const refresh = async () => {
    const [serviceHealth, indexedDocuments, cfoStatus, dashboardSnapshot] = await Promise.all([
      healthCheck(),
      listDocuments(),
      fetchCfoStatus(),
      dashboardData(),
    ])
    setHealth(serviceHealth)
    setDocuments(indexedDocuments)
    setCfo(cfoStatus)
    setDashboard(dashboardSnapshot)
  }

  /* oxlint-disable react/set-state-in-effect -- synchronize initial UI state with backend */
  useEffect(() => {
    void refresh()
      .then(() => setMessage('Ready. Upload a .txt or .md file to index it.'))
      .catch((error: unknown) => setMessage(error instanceof Error ? error.message : 'Backend unavailable.'))
  }, [])
  /* oxlint-enable react/set-state-in-effect */

  const upload = async () => {
    if (!file) {
      setMessage('Choose a .txt or .md file first.')
      return
    }
    setBusy(true)
    try {
      const created = await uploadDocument(file)
      setMessage(`Indexed ${created.filename} as ${created.id} (${created.chunk_ids.length} chunks).`)
      setFile(null)
      await refresh()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Upload failed.')
    } finally {
      setBusy(false)
    }
  }

  const search = async () => {
    setBusy(true)
    try {
      const hits = await searchDocuments(query)
      setResults(hits)
      setMessage(`Retrieved ${hits.length} known chunk${hits.length === 1 ? '' : 's'}.`)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Search failed.')
    } finally {
      setBusy(false)
    }
  }

  const remove = async (document: DocumentSummary) => {
    if (!deleteToken) {
      setMessage('Enter the configured deletion authorization token before deleting.')
      return
    }
    if (!window.confirm(`Run deletion and verification for ${document.filename}?`)) return
    setBusy(true)
    try {
      const completed = await deleteDocument(document.id, deleteToken)
      setReport(completed)
      setMessage(`Deletion operation ${completed.operation_id}: ${completed.status}.`)
      await refresh()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Deletion operation failed.')
    } finally {
      setBusy(false)
    }
  }

  const connectCfoAccount = async () => {
    const authWindow = window.open('about:blank', '_blank')
    if (authWindow) authWindow.opener = null
    setBusy(true)
    setMessage('Starting the official CFO.ai OAuth flow…')
    try {
      const started = await connectCfo()
      setCfoAuthorizationUrl(started.authorization_url)
      if (authWindow) authWindow.location.replace(started.authorization_url)
      let latest = await fetchCfoStatus()
      setCfo(latest)
      for (let attempt = 0; attempt < 300; attempt += 1) {
        if (latest.state !== 'awaiting_browser_authorization') break
        await new Promise((resolve) => window.setTimeout(resolve, 2000))
        latest = await fetchCfoStatus()
        setCfo(latest)
        if (latest.state !== 'awaiting_browser_authorization') {
          setDashboard(await dashboardData())
          setCfoAuthorizationUrl(null)
          setMessage(latest.detail)
          return
        }
      }
      setMessage('OAuth is still awaiting browser authorization. Complete consent in the CFO.ai window.')
    } catch (error) {
      authWindow?.close()
      setCfoAuthorizationUrl(null)
      setMessage(error instanceof Error ? error.message : 'CFO.ai connection failed.')
      try {
        setCfo(await fetchCfoStatus())
      } catch {
        setCfo(null)
      }
    } finally {
      setBusy(false)
    }
  }

  const chooseCfoWorkspace = async () => {
    if (!workspaceSelection) return
    setBusy(true)
    try {
      const latest = await selectCfoWorkspace(workspaceSelection)
      setCfo(latest)
      setDashboard(await dashboardData())
      setMessage(latest.detail)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Workspace selection failed.')
    } finally {
      setBusy(false)
    }
  }

  const loadCfoData = async () => {
    let parsed: unknown
    try {
      parsed = JSON.parse(cfoArguments)
    } catch {
      setMessage('CFO.ai arguments must be valid JSON.')
      return
    }
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      setMessage('CFO.ai arguments must be a JSON object.')
      return
    }
    setBusy(true)
    try {
      const args = Object.fromEntries(Object.entries(parsed))
      const latest = await readCfoFinancialData('inspect_table_blocks', args)
      setCfo(latest)
      setDashboard(await dashboardData())
      setMessage(latest.financial_result?.detail ?? latest.detail)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'CFO.ai read-only inspection failed.')
    } finally {
      setBusy(false)
    }
  }

  const disconnectCfoAccount = async () => {
    setBusy(true)
    try {
      setCfo(await disconnectCfo())
      setDashboard(await dashboardData())
      setCfoAuthorizationUrl(null)
      setMessage('CFO.ai connection credentials removed from the OS credential vault.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'CFO.ai disconnect failed.')
    } finally {
      setBusy(false)
    }
  }

  const openReport = async (operationId: string) => {
    setBusy(true)
    try {
      setReport(await deletionReport(operationId))
      setScreen('reports')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not load the deletion report.')
    } finally {
      setBusy(false)
    }
  }

  const formatTimestamp = (value: string | null) => value ? new Date(value).toLocaleString() : 'Not recorded'
  const checkCount = dashboard?.counts.checks
  const integrationLabels: Record<string, string> = {
    neatlogs_runtime: 'Neatlogs runtime tracing',
    neatlogs_developer_mcp: 'Neatlogs developer MCP',
    entire_copilot_cli: 'Entire / Copilot CLI tracking',
    cfo_ai_runtime: 'CFO.ai runtime',
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Deterministic deletion orchestration</p>
          <h1>GhostErase</h1>
        </div>
        <span className="subtle">Application and API-level evidence only</span>
      </header>

      <nav className="screen-nav" aria-label="Dashboard screens">
        {(['overview', 'documents', 'reports', 'integrations'] as const).map((item) => (
          <button
            key={item}
            type="button"
            className={screen === item ? 'nav-active' : 'nav-inactive'}
            aria-current={screen === item ? 'page' : undefined}
            onClick={() => setScreen(item)}
          >
            {item[0].toUpperCase() + item.slice(1)}
          </button>
        ))}
      </nav>

      {screen === 'overview' ? (
        <>
          <section className="panel">
            <h2>Overview</h2>
            <p>Counts are read from persistent document, deletion-operation, and check records.</p>
            <div className="status-grid">
              <div className="status-card"><span>Indexed documents</span><strong>{dashboard?.counts.indexed_documents ?? '—'}</strong></div>
              <div className="status-card"><span>Deletion operations</span><strong>{dashboard?.counts.deletion_operations ?? '—'}</strong></div>
              <div className="status-card"><span>Passed checks</span><strong>{checkCount?.pass ?? '—'}</strong></div>
              <div className="status-card"><span>Failed checks</span><strong>{checkCount?.fail ?? '—'}</strong></div>
              <div className="status-card"><span>Blocked checks</span><strong>{checkCount?.blocked ?? '—'}</strong></div>
              <div className="status-card"><span>Unverified checks</span><strong>{checkCount?.not_verified ?? '—'}</strong></div>
            </div>
            <h3>Persistent document states</h3>
            <p>{Object.entries(dashboard?.counts.documents_by_status ?? {}).map(([state, count]) => `${state}: ${count}`).join(' · ') || 'No document records.'}</p>
            <h3>Deletion operation states</h3>
            <p>{Object.entries(dashboard?.counts.deletion_operations_by_status ?? {}).map(([state, count]) => `${state}: ${count}`).join(' · ') || 'No deletion operations recorded.'}</p>
            <h3>Integration connection states</h3>
            <div className="integration-grid">
              {Object.entries(dashboard?.integrations ?? {}).map(([key, integration]) => (
                <article className="result-panel" key={key}>
                  <strong>{integrationLabels[key] ?? key}</strong>
                  <p className={`integration-state state-${integration.state}`}>{integration.state.replaceAll('_', ' ')}</p>
                </article>
              ))}
            </div>
          </section>
          <section className="panel">
            <h2>Recent deletion operations</h2>
            {dashboard?.recent_operations.length ? dashboard.recent_operations.slice(0, 10).map((operation) => (
              <article className="result-panel" key={operation.operation_id}>
                <strong>{operation.document_name} — {operation.status}</strong>
                <p>Started {formatTimestamp(operation.created_at)} · Document {operation.document_id}</p>
                <code>{operation.operation_id}</code>
                <button type="button" disabled={busy} onClick={() => void openReport(operation.operation_id)}>Open verification report</button>
              </article>
            )) : <p>No deletion operations have been recorded.</p>}
          </section>
        </>
      ) : null}

      {screen === 'documents' ? (
        <>
          <section className="status-grid">
        <div className="status-card"><span>Backend</span><strong>{health ? 'available' : 'unavailable'}</strong></div>
        <div className="status-card"><span>Vector store</span><strong>{health?.vector_store ?? 'checking'}</strong></div>
        <div className="status-card"><span>Embedding provider</span><strong>{health?.embedding_provider ?? 'checking'}</strong></div>
        <div className="status-card"><span>Deletion authorization</span><strong>{health?.deletion_authorization_configured ? 'configured' : 'blocked'}</strong></div>
          </section>
          <section className="panel">
        <h2>Upload a document</h2>
        <p>UTF-8 plain text or Markdown, up to 5 MiB. A persistent ChromaDB index and source file are created.</p>
        <label>
          .txt or .md file
          <input
            type="file"
            accept=".txt,.md,text/plain,text/markdown"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <button type="button" disabled={busy || !file} onClick={() => void upload()}>Upload and index</button>
      </section>

      <section className="panel">
        <h2>Search indexed chunks</h2>
        <label>
          Query
          <input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => {
            if (event.key === 'Enter') void search()
          }} />
        </label>
        <button type="button" disabled={busy || !query.trim()} onClick={() => void search()}>Search ChromaDB</button>
        {results.map((hit) => (
          <article className="result-panel" key={hit.chunk_id}>
            <code>{hit.document_id} / {hit.chunk_id}</code>
            <p>{hit.content}</p>
          </article>
        ))}
      </section>

      <section className="panel">
        <h2>Indexed documents</h2>
        <label>
          Deletion authorization token
          <input type="password" autoComplete="off" value={deleteToken} onChange={(event) => setDeleteToken(event.target.value)} />
        </label>
        {documents.length === 0 ? <p>No indexed documents.</p> : documents.map((document) => (
          <article className="result-panel" key={document.id}>
            <strong>{document.filename} — {document.status}</strong>
            <p>Created/indexed: {formatTimestamp(document.created_at)} · {document.chunk_count} chunks · SHA-256 {document.content_hash}</p>
            <p>Latest deletion state: {document.latest_deletion_status ?? 'No deletion operation'}</p>
            <code>{document.id}</code>
            <div className="document-actions">
              <button type="button" disabled={busy || !document.latest_deletion_operation_id} onClick={() => document.latest_deletion_operation_id && void openReport(document.latest_deletion_operation_id)}>View latest report</button>
              <button type="button" disabled={busy || document.status === 'deleted'} onClick={() => void remove(document)}>Delete and verify</button>
            </div>
          </article>
        ))}
      </section>
        </>
      ) : null}

      {screen === 'reports' ? (
        <>
          <section className="panel">
            <h2>Deletion verification reports</h2>
            <p>Choose a persisted operation to inspect its recorded evidence.</p>
            {dashboard?.recent_operations.length ? dashboard.recent_operations.map((operation) => (
              <article className="result-panel" key={operation.operation_id}>
                <strong>{operation.document_name} — {operation.status}</strong>
                <p>Operation {operation.operation_id} · {formatTimestamp(operation.created_at)}</p>
                <button type="button" disabled={busy} onClick={() => void openReport(operation.operation_id)}>Load report</button>
              </article>
            )) : <p>No deletion operation has been recorded.</p>}
          </section>
          {report ? (
            <section className="panel">
              <h2>Operation {report.operation_id}</h2>
              <p><strong>Overall verification status:</strong> {report.status}</p>
              <p><strong>Requested document:</strong> {report.document_id}</p>
              <p><strong>Started:</strong> {formatTimestamp(report.created_at)} · <strong>Completed:</strong> {formatTimestamp(report.completed_at)}</p>
              {report.checks.map((check) => (
                <article className="result-panel" key={check.check_id}>
                  <strong>{check.layer} — {check.status}</strong>
                  <p>Checked {formatTimestamp(check.checked_at)} · Method: {check.method}</p>
                  <p>Document {check.document_id} · {check.chunk_ids.length} recorded chunk IDs</p>
                  <pre>{JSON.stringify(check.actual_result, null, 2)}</pre>
                  {check.error_detail ? <p className="error-detail">Error: {check.error_detail}</p> : null}
                </article>
              ))}
              <h3>Scope and limitations</h3>
              <p>{report.scope.conclusion ?? 'Conclusions are limited to the recorded configured API checks.'}</p>
              <p><strong>API-level accessibility only:</strong> {report.scope.api_level_accessibility_only ? 'Yes' : 'No'}</p>
              <p><strong>Physical erasure verified:</strong> {report.scope.physical_erasure_verified ? 'Yes' : 'No'}</p>
              {report.scope.storage_paths_checked ? <pre>{JSON.stringify(report.scope.storage_paths_checked, null, 2)}</pre> : null}
              <p>Not examined: {report.scope.not_examined.join(', ') || 'None listed'}</p>
              <p role="status">{message}</p>
            </section>
          ) : <section className="panel"><p>Select an operation to view its verification report.</p></section>}
        </>
      ) : null}

      {screen === 'integrations' ? (
        <>
          <section className="panel">
            <h2>Integration status</h2>
            <div className="integration-grid">
              {Object.entries(dashboard?.integrations ?? {}).map(([key, integration]) => (
                <article className="result-panel" key={key}>
                  <strong>{integrationLabels[key] ?? key}</strong>
                  <p className={`integration-state state-${integration.state}`}>{integration.state.replaceAll('_', ' ')}</p>
                  <p>{integration.detail}</p>
                  <p>Verified: {integration.verified ? 'Yes' : 'No'}</p>
                </article>
              ))}
            </div>
          </section>
          <section className="panel">
            <h2>CFO.ai financial impact</h2>
            <p>Only information returned from the connected CFO.ai workspace is shown. GhostErase does not send document content or deletion evidence to CFO.ai, invoke write tools, or infer deletion costs or legal exposure.</p>
            <p><strong>Connection:</strong> {cfo?.detail ?? 'Checking CFO.ai status.'}</p>
            <p><strong>Workspace:</strong> {cfo?.selected_workspace_id ?? 'Not selected'}</p>
            {cfo?.state === 'not_connected' || cfo?.state === 'authentication_failed' || cfo?.state === 'reauthentication_required' ? (
              <button type="button" disabled={busy} onClick={() => void connectCfoAccount()}>Connect to CFO.ai</button>
            ) : null}
            {cfoAuthorizationUrl && cfo?.state === 'awaiting_browser_authorization' ? (
              <p><a href={cfoAuthorizationUrl} target="_blank" rel="noreferrer">Continue CFO.ai authorization</a></p>
            ) : null}
            {cfo?.state === 'workspace_selection_required' ? (
              <div className="document-actions">
                <label>
                  Authorized workspace
                  <select value={workspaceSelection} onChange={(event) => setWorkspaceSelection(event.target.value)}>
                    <option value="">Select a workspace</option>
                    {cfo.workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}
                  </select>
                </label>
                <button type="button" disabled={busy || !workspaceSelection} onClick={() => void chooseCfoWorkspace()}>Verify workspace</button>
              </div>
            ) : null}
            {cfo?.state === 'verified' ? (
              <>
                <p><strong>Read-only tools discovered:</strong> {cfo.read_tools.map((tool) => tool.name).join(', ') || 'none'}</p>
                {financialReadTool ? <details><summary>Live inspect_table_blocks input schema</summary><pre>{JSON.stringify(financialReadTool.input_schema, null, 2)}</pre></details> : null}
                <label>
                  Arguments for discovered inspect_table_blocks tool (JSON, validated against its live schema)
                  <textarea value={cfoArguments} onChange={(event) => setCfoArguments(event.target.value)} rows={4} />
                </label>
                <button type="button" disabled={busy || !financialReadTool} onClick={() => void loadCfoData()}>Read CFO.ai model table</button>
                <button type="button" disabled={busy} onClick={() => void disconnectCfoAccount()}>Disconnect CFO.ai</button>
                {cfo.last_inspection ? <details><summary>Initial read-only workspace verification</summary><pre>{JSON.stringify(cfo.last_inspection, null, 2)}</pre></details> : null}
              </>
            ) : null}
            {cfo?.state === 'connected_but_not_verified' ? <button type="button" disabled={busy} onClick={() => void connectCfoAccount()}>Reconnect and verify CFO.ai</button> : null}
            <div className="financial-evidence">
              <strong>Financial evidence: {dashboard?.financial_impact.status ?? 'checking'}</strong>
              <p>{cfo?.financial_result?.detail ?? dashboard?.financial_impact.detail ?? 'No verified financial data is available.'}</p>
              {cfo?.financial_result?.result ? <pre>{JSON.stringify(cfo.financial_result.result, null, 2)}</pre> : null}
            </div>
          </section>
        </>
      ) : null}
    </main>
  )
}

export default App

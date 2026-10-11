import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ReportIndicators, StatusBadge } from './Status'
import { checkLimitation } from '../checks'
import type { DeletionCheck, VerificationResponse } from '../types'

afterEach(() => cleanup())

function check(overrides: Partial<DeletionCheck>): DeletionCheck {
  return {
    check_id: 'check-1',
    operation_id: 'op-1',
    checked_at: '2026-10-10T00:00:00+00:00',
    document_id: 'doc-1',
    chunk_ids: [],
    layer: 'source_file',
    method: 'path.unlink_and_path.exists',
    status: 'pass',
    actual_result: {},
    error_detail: null,
    ...overrides,
  }
}

function reportWith(status: 'pass' | 'fail', retrieval: 'pass' | 'fail', residual: 'pass' | 'fail'): VerificationResponse {
  return {
    operation_id: 'op-1',
    document_id: 'doc-1',
    chunk_ids: [],
    status,
    kind: 'deletion',
    served_from_history: false,
    created_at: '2026-10-10T00:00:00+00:00',
    completed_at: '2026-10-10T00:01:00+00:00',
    checks: [
      check({ check_id: 'c-retrieval', layer: 'application_retrieval', method: 'query', status: retrieval }),
      check({ check_id: 'c-residual', layer: 'chromadb_api', method: 'collection.get(where=document_id)', status: residual }),
    ],
    scope: {
      api_level_accessibility_only: true,
      physical_erasure_verified: false,
      not_examined: [],
      checked_layers: [],
    },
  }
}

describe('StatusBadge', () => {
  it.each([
    ['pass', 'Pass'],
    ['fail', 'Fail'],
    ['blocked', 'Blocked'],
    ['not_verified', 'Not verified'],
    ['indexed', 'Indexed'],
    ['deleted', 'Deleted'],
    ['deletion_failed', 'Deletion failed'],
    ['not_configured', 'Not configured'],
  ] as Array<[string, string]>)('labels %s as text, not color alone', (state, label) => {
    const { unmount } = render(<StatusBadge state={state} />)
    expect(screen.getByText(label)).toBeDefined()
    unmount()
  })
})

describe('ReportIndicators', () => {
  it('distinguishes retrieval blocked from storage verified on success', () => {
    render(<ReportIndicators report={reportWith('pass', 'pass', 'pass')} />)
    expect(screen.getByText('Retrieval blocked:').parentElement?.textContent).toContain('Yes')
    expect(screen.getByText('Storage deletion verified:').parentElement?.textContent).toContain('Yes')
  })

  it('shows negative indicators on partial failure', () => {
    render(<ReportIndicators report={reportWith('fail', 'fail', 'pass')} />)
    expect(screen.getByText('Retrieval blocked:').parentElement?.textContent).toContain('No')
    expect(screen.getByText('Storage deletion verified:').parentElement?.textContent).toContain('Yes')
  })
})

describe('checkLimitation', () => {
  it('explains each check scope without upgrading status', () => {
    expect(checkLimitation('source_file', 'path.unlink_and_path.exists')).toContain('journals')
    expect(checkLimitation('chromadb_api', 'collection.get(ids, where=document_id) then collection.delete(ids, where=document_id)')).toContain(
      'residual check',
    )
    expect(checkLimitation('chromadb_api', 'collection.get(where=document_id)')).toContain('configured vector collection')
    expect(checkLimitation('app_cache', 'cache.invalidate(document_id) then cache.residuals(document_id)')).toContain(
      'Third-party caches',
    )
    expect(checkLimitation('application_retrieval', 'query')).toContain('probe queries')
    expect(checkLimitation('sql_metadata', 'SQLAlchemy clear content only after prior checks pass')).toContain(
      'retained for audit',
    )
    expect(checkLimitation('unknown_layer', 'unknown_method')).toContain('Scoped to the backends')
  })
})

import type { VerificationResponse } from '../types'

const STATE_LABELS: Record<string, string> = {
  pass: 'Pass',
  fail: 'Fail',
  blocked: 'Blocked',
  not_verified: 'Not verified',
  indexed: 'Indexed',
  deleted: 'Deleted',
  deletion_failed: 'Deletion failed',
  deletion_blocked: 'Deletion blocked',
  verified: 'Verified',
  not_configured: 'Not configured',
  unavailable: 'Unavailable',
  insufficient_financial_data: 'Insufficient data',
}

export function StatusBadge({ state }: { state: string }) {
  const label = STATE_LABELS[state] ?? state.replaceAll('_', ' ')
  return <span className={`badge badge-${state}`}>{label}</span>
}

export function ReportIndicators({ report }: { report: VerificationResponse }) {
  const retrieval = report.checks.find((check) => check.layer === 'application_retrieval')
  const residual = report.checks.find((check) => check.method === 'collection.get(where=document_id)')
  return (
    <>
      <h3>What the evidence shows</h3>
      <p>
        <strong>Retrieval blocked:</strong> {retrieval?.status === 'pass' ? 'Yes' : retrieval ? 'No' : 'Not checked'}{' '}
        {retrieval ? <StatusBadge state={retrieval.status} /> : null}
      </p>
      <p>
        <strong>Storage deletion verified:</strong>{' '}
        {residual?.status === 'pass' ? 'Yes, in the configured vector collection' : residual ? 'No' : 'Not checked'}{' '}
        {residual ? <StatusBadge state={residual.status} /> : null}
      </p>
    </>
  )
}

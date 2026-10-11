// Display-layer restatement of each verification check's scope. These strings
// explain evidence; they never upgrade a status and must stay consistent with
// the backend's claim/limitation comments in document_service.py.

interface LimitationRule {
  match: (layer: string, method: string) => boolean
  text: string
}

const LIMITATIONS: LimitationRule[] = [
  {
    match: (layer, method) => layer === 'source_file' && method.includes('unlink'),
    text: 'Ordinary file removal only. Does not examine filesystem journals, SSD remanence, or backups.',
  },
  {
    match: (layer, _method) => layer === 'source_file',
    text: 'Observes current file presence only. Does not examine filesystem journals, SSD remanence, or backups.',
  },
  {
    match: (_layer, method) => method.includes('collection.delete'),
    text: 'Acknowledgement of the delete call only — removal itself is verified by the residual check.',
  },
  {
    match: (layer, method) => layer === 'chromadb_api' && method.includes('where=document_id'),
    text: 'Covers only the configured vector collection. Replicas or other collections are not inspected.',
  },
  {
    match: (layer, method) => layer === 'app_cache' && method.includes('invalidate'),
    text: 'Covers only this application-owned cache. Third-party caches cannot be inspected and stay unverified.',
  },
  {
    match: (layer, _method) => layer === 'app_cache',
    text: 'Reads only this application-owned cache table. Third-party caches cannot be inspected and stay unverified.',
  },
  {
    match: (layer, _method) => layer === 'application_retrieval',
    text: 'Evidence only for the executed probe queries, not for every possible query.',
  },
  {
    match: (layer, method) => layer === 'sql_metadata' && method.includes('clear'),
    text: 'Clears the active text field only; identifiers and history are retained for audit.',
  },
  {
    match: (layer, _method) => layer === 'sql_metadata',
    text: 'Reads current metadata only; does not examine storage media or backups.',
  },
]

export function checkLimitation(layer: string, method: string): string {
  return (
    LIMITATIONS.find((rule) => rule.match(layer, method))?.text ??
    'Scoped to the backends this application controls; see scope and limitations.'
  )
}

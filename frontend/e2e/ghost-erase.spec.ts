import { spawn, type ChildProcess } from 'node:child_process'
import { createServer } from 'node:net'
import { once } from 'node:events'
import { mkdir, rm } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

const frontendDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = path.resolve(frontendDirectory, '..')

async function freePort(): Promise<number> {
  const server = createServer()
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('Could not allocate local test port')
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()))
  return address.port
}

async function waitForHttp(url: string, process: ChildProcess): Promise<void> {
  const deadline = Date.now() + 90_000
  while (Date.now() < deadline) {
    if (process.exitCode !== null) throw new Error(`Local test service exited with ${process.exitCode}`)
    try {
      const response = await fetch(url)
      if (response.ok) return
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 300))
    }
  }
  throw new Error(`Timed out waiting for local test service at ${url}`)
}

function stopProcess(process: ChildProcess): Promise<void> {
  if (process.exitCode !== null) return Promise.resolve()
  const exited = once(process, 'exit').then(() => undefined)
  process.kill()
  return Promise.race([
    exited,
    new Promise<void>((resolve) => setTimeout(resolve, 5_000)),
  ])
}

test('real browser workflow persists Chroma and deletion evidence across backend restart', async ({ page }) => {
  const backendPort = await freePort()
  const frontendPort = await freePort()
  const dataDirectory = path.join(repositoryRoot, 'frontend', '.playwright-data', `${Date.now()}-${Math.random().toString(16).slice(2)}`)
  await mkdir(dataDirectory, { recursive: true })
  const python = process.env.PYTHON ?? 'python'
  const baseEnvironment = {
    ...process.env,
    DATABASE_URL: `sqlite:///${path.join(dataDirectory, 'metadata.sqlite3').replaceAll('\\', '/')}`,
    GHOSTERASE_UPLOAD_DIR: path.join(dataDirectory, 'uploads'),
    CHROMA_DIR: path.join(dataDirectory, 'chroma'),
    DELETE_AUTH_TOKEN: `e2e-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    NEATLOGS_API_KEY: '',
    LITELLM_API_KEY: '',
    PYTHONPATH: repositoryRoot,
    GHOSTERASE_API_PROXY: `http://127.0.0.1:${backendPort}`,
  }
  const backendCommand = () => spawn(
    python,
    ['-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1', '--port', String(backendPort)],
    { cwd: repositoryRoot, env: baseEnvironment, stdio: 'ignore' },
  )
  const backendUrl = `http://127.0.0.1:${backendPort}`
  const vitePath = path.join(frontendDirectory, 'node_modules', 'vite', 'bin', 'vite.js')
  const frontendProcess = spawn(
    process.execPath,
    [vitePath, '--host', '127.0.0.1', '--port', String(frontendPort), '--strictPort'],
    {
      cwd: frontendDirectory,
      env: { ...process.env, GHOSTERASE_API_PROXY: `http://127.0.0.1:${backendPort}` },
      stdio: 'ignore',
    },
  )
  let backendProcess = backendCommand()
  try {
    await waitForHttp(`${backendUrl}/api/v1/health`, backendProcess)
    await waitForHttp(`http://127.0.0.1:${frontendPort}`, frontendProcess)
    // Hermetic precondition: the CFO OAuth grant lives in the host OS vault,
    // outside the isolated test dirs. Clear it so the integrations assertions
    // below hold on any machine (no-op where no grant exists; the server-side
    // grant itself is untouched and can be re-consented).
    const disconnectForIsolation = await page.request.delete(`${backendUrl}/api/v1/integrations/cfo`)
    expect(disconnectForIsolation.ok()).toBeTruthy()
    await page.goto(`http://127.0.0.1:${frontendPort}`)
    await expect(page.getByRole('heading', { name: 'GhostErase' })).toBeVisible()
    await page.getByRole('button', { name: 'Documents' }).click()

    const marker = `ghost-erase-e2e-${Date.now()}-${Math.random().toString(16).slice(2)}`
    const phrase = `Synthetic retrieval marker ${marker}; never customer data.`
    await page.getByLabel('.txt or .md file').setInputFiles({
      name: 'synthetic-e2e.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from(`${phrase} ${'This local end-to-end test verifies actual persistent vector indexing. '.repeat(4)}`),
    })
    await page.getByRole('button', { name: 'Upload and index' }).click()
    const documentCard = page.locator('.result-panel').filter({ hasText: 'synthetic-e2e.txt' })
    await expect(documentCard).toContainText('indexed', { timeout: 30_000 })
    const documentId = (await documentCard.locator('code').innerText()).trim()

    await page.getByLabel('Query').fill(marker)
    await page.getByRole('button', { name: 'Search ChromaDB' }).click()
    await expect(page.locator('.result-panel').filter({ hasText: marker })).toBeVisible({ timeout: 30_000 })

    const controlPhrase = `Independent control marker ${marker}-control.`
    const controlUpload = await page.request.post(`${backendUrl}/api/v1/documents/upload`, {
      multipart: {
        file: {
          name: 'unrelated-control.txt',
          mimeType: 'text/plain',
          buffer: Buffer.from(`${controlPhrase} This control must survive the targeted deletion.`),
        },
      },
    })
    expect(controlUpload.ok()).toBeTruthy()
    const controlDocumentId = (await controlUpload.json()).id as string
    const controlBefore = await page.request.post(`${backendUrl}/api/v1/search`, {
      data: { query: controlPhrase, limit: 10 },
    })
    expect((await controlBefore.json()).results.some(
      (hit: { document_id: string }) => hit.document_id === controlDocumentId,
    )).toBeTruthy()

    await page.getByRole('button', { name: 'Integrations' }).click()
    await expect(page.getByText('Neatlogs developer MCP', { exact: true }).locator('..')).toContainText('not configured')
    await expect(page.getByText('Entire / Copilot CLI tracking', { exact: true }).locator('..')).toContainText('not configured')
    await expect(page.getByText('CFO.ai runtime', { exact: true }).locator('..')).toContainText('not connected')
    await expect(page.getByText('Financial evidence: unavailable')).toBeVisible()
    await page.getByRole('button', { name: 'Documents' }).click()

    await page.getByLabel('Deletion authorization token').fill(baseEnvironment.DELETE_AUTH_TOKEN)
    page.once('dialog', (dialog) => dialog.accept())
    await documentCard.getByRole('button', { name: 'Delete and verify' }).click()
    await expect(documentCard).toContainText('Latest deletion state: pass', { timeout: 45_000 })
    await documentCard.getByRole('button', { name: 'View latest report' }).click()
    await expect(page.getByRole('heading', { name: /^Operation / })).toBeVisible()
    await expect(page.locator('p').filter({ hasText: 'Physical erasure verified:' })).toContainText('No')
    await expect(page.getByText('source_file — pass', { exact: true })).toBeVisible()
    await expect(page.getByText('chromadb_api — pass', { exact: true })).toHaveCount(2)
    await expect(page.getByText('app_cache — pass', { exact: true })).toBeVisible()
    await expect(page.getByText('application_retrieval — pass', { exact: true })).toBeVisible()
    const lineageAfterDelete = await page.request.get(`${backendUrl}/api/v1/documents/${documentId}/lineage`)
    expect(lineageAfterDelete.ok()).toBeTruthy()
    const lineageAfterDeleteBody = await lineageAfterDelete.json()
    expect(lineageAfterDeleteBody.cache_entries.length).toBeGreaterThan(0)
    expect(lineageAfterDeleteBody.cache_entries.every(
      (entry: { invalidated_at: string | null }) => entry.invalidated_at !== null,
    )).toBeTruthy()
    await page.getByRole('button', { name: 'Documents' }).click()
    await expect(documentCard).toContainText('Latest deletion state: pass', { timeout: 45_000 })
    await documentCard.getByRole('button', { name: 'Verify now' }).click()
    await expect(page.getByRole('heading', { name: /^Operation / })).toBeVisible()
    await expect(page.locator('p').filter({ hasText: 'Overall verification status:' })).toContainText('blocked')
    await expect(page.getByText('application_retrieval — blocked', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Documents' }).click()

    const vectorCheck = await page.request.get(`${backendUrl}/api/v1/documents`)
    expect(vectorCheck.ok()).toBeTruthy()
    const deletedResponse = await page.request.post(`${backendUrl}/api/v1/search`, {
      data: { query: marker, limit: 10 },
    })
    expect(deletedResponse.ok()).toBeTruthy()
    expect((await deletedResponse.json()).results.filter((hit: { document_id: string }) => hit.document_id === documentId)).toHaveLength(0)
    const deletedAgain = await page.request.post(`${backendUrl}/api/v1/search`, {
      data: { query: marker, limit: 10 },
    })
    expect(deletedAgain.ok()).toBeTruthy()
    expect((await deletedAgain.json()).results.filter((hit: { document_id: string }) => hit.document_id === documentId)).toHaveLength(0)
    const controlAfter = await page.request.post(`${backendUrl}/api/v1/search`, {
      data: { query: controlPhrase, limit: 10 },
    })
    expect((await controlAfter.json()).results.some(
      (hit: { document_id: string }) => hit.document_id === controlDocumentId,
    )).toBeTruthy()
    const controls = await page.request.get(`${backendUrl}/api/v1/dashboard`)
    expect((await controls.json()).integrations.entire_copilot_cli.state).toBe('not_configured')
    expect((await controls.json()).financial_impact.status).toBe('unavailable')

    await stopProcess(backendProcess)
    backendProcess = backendCommand()
    await waitForHttp(`${backendUrl}/api/v1/health`, backendProcess)
    await page.reload()
    await page.getByRole('button', { name: 'Documents' }).click()
    const recoveredDocument = page.locator('.result-panel').filter({ hasText: 'synthetic-e2e.txt' })
    await expect(recoveredDocument).toContainText('deleted', { timeout: 30_000 })
    await recoveredDocument.getByRole('button', { name: 'View latest report' }).click()
    await expect(page.getByRole('heading', { name: /^Operation / })).toBeVisible()
    await expect(page.locator('p').filter({ hasText: 'Overall verification status:' })).toContainText('pass')

    const persistentData = await page.request.get(`${backendUrl}/api/v1/dashboard`)
    const dashboard = await persistentData.json()
    expect(dashboard.counts.deletion_operations).toBe(1)
    expect(dashboard.counts.checks.pass).toBe(6)
    const lineageAfterRestart = await page.request.get(`${backendUrl}/api/v1/documents/${documentId}/lineage`)
    expect(lineageAfterRestart.ok()).toBeTruthy()
    const lineage = await lineageAfterRestart.json()
    expect(lineage.artifacts.length).toBeGreaterThan(0)
    expect(new Set(lineage.artifacts.map((artifact: { status: string }) => artifact.status))).toEqual(
      new Set(['deleted_verified']),
    )
    const controlAfterRestart = await page.request.post(`${backendUrl}/api/v1/search`, {
      data: { query: controlPhrase, limit: 10 },
    })
    expect((await controlAfterRestart.json()).results.some(
      (hit: { document_id: string }) => hit.document_id === controlDocumentId,
    )).toBeTruthy()
  } finally {
    await stopProcess(backendProcess)
    await stopProcess(frontendProcess)
    await rm(dataDirectory, { recursive: true, force: true })
  }
})

// Data layer for the dashboard: everything comes from the SiteSignal Safety API (safety-pipeline/api).
// The API runs the pipeline (Nemotron extraction -> weather -> Safety Index -> decisions) and stores results
// in Postgres. This file only fetches and reshapes them into the types the screens already use.
//
// Note on scores: the backend produces a Safety Index (0-100, 100 = safest), an uncalibrated triage score.
// It is NOT a failure probability, so `prob` is always null and the screens show `safety_index` instead.
import type {
  Asset,
  AssetDetail,
  Call,
  Decision,
  Driver,
  Message,
  PipelineJob,
  Portfolio,
  RiskTier,
  SafetyBand,
  Savings,
  ServiceRecord,
  WorkOrder,
} from './types'

export const API_BASE: string = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, init)
  } catch {
    throw new Error(`Cannot reach the SiteSignal API at ${API_BASE}. Is the backend running?`)
  }
  const body = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error((body as { detail?: string }).detail || `${res.status} ${res.statusText}`)
  return body as T
}

export function tierFromBand(band: SafetyBand | null | undefined): RiskTier {
  if (band === 'critical' || band === 'poor') return 'high'
  if (band === 'fair') return 'medium'
  if (band === 'good') return 'low'
  return null
}

// ---------------------------------------------------------------- raw API shapes
interface ApiAsset {
  asset_id: string; site_id: string; asset_type: string; safety_index: number | null; band: SafetyBand
  confidence: number; constraints: string[]; top_driver: string | null; site_name: string; site_type: string
  city: string | null; install_date: string | null; make: string | null; model: string | null
  refrigerant: string | null; last_service_date: string | null; service_spend_usd: number
  decision_id: number | null; decision_action: string | null; decision_urgency: string | null
  decision_status: string | null
}

interface ApiServiceRecord {
  id: number; asset_id: string; site_id: string; asset_type: string; site_name: string; city: string | null
  opened_at: string; kind: string; issue_category: string | null; refrigerant_added: boolean
  cost_usd: number | null; note: string | null
}

interface ApiPortfolio {
  sites: number; assets: number; work_orders: number; rejected_rows: number
  assets_by_band: Partial<Record<SafetyBand, number>>
  decisions_by_status: Record<string, number>
  revenue_per_day_at_poor_or_critical_sites_usd: number
  latest_run: { as_of: string; created_at: string } | null
}

interface ApiScore {
  safety_index: number | null; band: SafetyBand; confidence: number; constraints: string[]; data_gaps: string[]
  drivers: Array<{ factor: string; points_lost: number; why: string }>
  inputs: { age_years: number | null }
}

interface ApiAssetDetail {
  asset: { asset_id: string; site_id: string; asset_type: string; install_date: string | null; make: string | null
           model: string | null; refrigerant: string | null; site_name: string }
  score: ApiScore | null
  score_history: Array<{ scored_at: string; safety_index: number | null; band: SafetyBand; confidence: number }>
  work_orders: Array<{ opened_at: string; kind: string; issue_category: string | null; refrigerant_added: boolean
                       cost_usd: number | null; note: string | null }>
  forecast: Array<{ forecast_date: string; max_temp_f: number }>
  decision: Decision | null
}

// ---------------------------------------------------------------- reshaping
function toServiceRecord(r: ApiServiceRecord): ServiceRecord {
  return {
    source_row: r.id,
    asset_id: r.asset_id,
    site_id: r.site_id,
    machine_name: r.asset_id,
    site_name: r.site_name,
    city: r.city ?? '',
    date: r.opened_at,
    work_type: r.kind,
    problem: (r.issue_category ?? '').replaceAll('_', ' '),
    refrigerant_charge: null,
    invoice_total_usd: r.cost_usd,
    tech_notes: r.note ?? (r.refrigerant_added ? 'Refrigerant added' : ''),
  }
}

function toAsset(a: ApiAsset): Asset {
  return {
    id: a.asset_id,
    site_id: a.site_id,
    site_name: a.site_name,
    site_type: a.site_type,
    city: a.city ?? '',
    asset_type: a.asset_type,
    install_date: a.install_date,
    prob: null,
    tier: tierFromBand(a.band),
    main_reason: a.top_driver ?? (a.band === 'unknown' ? 'Install date missing: needs data before it can be scored' : 'No risk drivers'),
    last_service_date: a.last_service_date,
    historical_service_spend_usd: Math.round((a.service_spend_usd ?? 0) * 100) / 100,
    drivers: a.top_driver ? [a.top_driver] : [],
    safety_index: a.safety_index,
    band: a.band,
    confidence: a.confidence,
    constraints: a.constraints ?? [],
    decision_id: a.decision_id,
    decision_action: a.decision_action,
    decision_urgency: a.decision_urgency,
    decision_status: a.decision_status,
  }
}

let recordsCache: { at: number; rows: ApiServiceRecord[] } | null = null
async function allRecords(): Promise<ApiServiceRecord[]> {
  if (recordsCache && Date.now() - recordsCache.at < 2000) return recordsCache.rows
  const rows = await api<ApiServiceRecord[]>('/service-records?limit=20000')
  recordsCache = { at: Date.now(), rows }
  return rows
}

// ---------------------------------------------------------------- reads used by the screens
export async function getPortfolio(): Promise<Portfolio> {
  const [p, records] = await Promise.all([api<ApiPortfolio>('/portfolio'), allRecords()])
  const b = p.assets_by_band ?? {}
  return {
    sites: p.sites,
    machines: p.assets,
    supported_assets: p.assets - (b.unknown ?? 0),
    high_risk_count: (b.critical ?? 0) + (b.poor ?? 0),
    medium_risk_count: b.fair ?? 0,
    service_records: p.work_orders,
    latest_service_date: records[0]?.opened_at ?? null,
  }
}

export async function getSavings(): Promise<Savings> {
  const records = await allRecords()
  const paid = records.filter((r) => r.cost_usd !== null && r.cost_usd >= 0)
  return {
    historical_service_spend_usd: Math.round(paid.reduce((sum, r) => sum + (r.cost_usd ?? 0), 0) * 100) / 100,
    records_with_invoice_total: paid.length,
    total_service_records: records.length,
  }
}

export async function getAssets(): Promise<Asset[]> {
  return (await api<ApiAsset[]>('/assets')).map(toAsset)
}

export async function getAsset(id: string): Promise<AssetDetail | null> {
  let d: ApiAssetDetail
  try {
    d = await api<ApiAssetDetail>(`/assets/${encodeURIComponent(id)}`)
  } catch {
    return null
  }
  const s = d.score
  const history = d.work_orders.map((w, i) => toServiceRecord({
    id: i + 1, asset_id: d.asset.asset_id, site_id: d.asset.site_id, asset_type: d.asset.asset_type,
    site_name: d.asset.site_name, city: null, opened_at: w.opened_at, kind: w.kind,
    issue_category: w.issue_category, refrigerant_added: w.refrigerant_added, cost_usd: w.cost_usd, note: w.note,
  }))
  const drivers: Driver[] = (s?.drivers ?? []).map((x) => ({ factor: x.factor, detail: x.why, value: x.points_lost }))
  return {
    asset: {
      id: d.asset.asset_id,
      asset_type: d.asset.asset_type,
      install_date: d.asset.install_date,
      site_name: d.asset.site_name,
      site_type: '',
      city: '',
      square_feet: null,
      open_hours: null,
      manufacturer: d.asset.make ?? '',
      model: d.asset.model ?? '',
      refrigerant: d.asset.refrigerant ?? '',
    },
    risk_now: {
      prob: null,
      tier: tierFromBand(s?.band),
      safety_index: s?.safety_index ?? null,
      band: s?.band ?? 'unknown',
      confidence: s?.confidence ?? 0,
      constraints: s?.constraints ?? [],
      data_gaps: s?.data_gaps ?? [],
      age_years: s?.inputs?.age_years ?? null,
      multiplier: null,
      drivers,
    },
    risk_history: d.score_history.map((h) => ({ date: h.scored_at.slice(0, 10), prob: 0, safety_index: h.safety_index })),
    forecast: d.forecast.map((f) => ({ date: f.forecast_date, max_f: f.max_temp_f })),
    historical_service_spend_usd: Math.round(d.work_orders.reduce((sum, w) => sum + (w.cost_usd && w.cost_usd > 0 ? w.cost_usd : 0), 0) * 100) / 100,
    service_record_count: history.length,
    work_order_history: history,
    work_orders: [],
    decision: d.decision,
  }
}

export async function getWorkOrders(): Promise<WorkOrder[]> {
  return (await allRecords()).map(toServiceRecord)
}

export async function getDecisions(): Promise<Decision[]> {
  return api<Decision[]>('/decisions')
}

const ACTION_TEXT: Record<string, string> = {
  create_work_order: 'Create work order', request_data: 'Request data', monitor: 'Monitor', no_action: 'No action',
}
const URGENCY_TEXT: Record<string, string> = { today: 'today', '48h': 'within 48h', this_week: 'this week', none: '' }

export function decisionTitle(d: Pick<Decision, 'action' | 'urgency'>): string {
  return `${ACTION_TEXT[d.action] ?? d.action} ${URGENCY_TEXT[d.urgency] ?? ''}`.trim()
}

// The activity feed shows proposed actions awaiting review (nothing is sent automatically).
export async function getMessages(): Promise<Message[]> {
  const ds = await getDecisions()
  return ds
    .filter((d) => d.action !== 'no_action')
    .map((d) => ({
      id: `decision-${d.id}`,
      recipient: `${d.asset_id} · ${d.status.replace('_', ' ')}`,
      time: new Date().toISOString(),
      text: `${decisionTitle(d)}. ${d.reason}`,
      dry_run: true,
    }))
}

export async function getCalls(): Promise<Call[]> {
  return []
}

// ---------------------------------------------------------------- actions
export async function approveWorkOrder(id: string): Promise<null> {
  await api(`/decisions/${encodeURIComponent(id)}/approve`, { method: 'POST' })
  return null
}

export async function holdWorkOrder(id: string): Promise<null> {
  await api(`/decisions/${encodeURIComponent(id)}/reject`, { method: 'POST' })
  return null
}

export async function setDecisionStatus(id: number, action: 'approve' | 'reject' | 'done'): Promise<void> {
  await api(`/decisions/${id}/${action}`, { method: 'POST' })
}

async function waitForJob(jobId: string, onProgress?: (job: PipelineJob) => void): Promise<PipelineJob> {
  for (;;) {
    const job = await api<PipelineJob>(`/pipeline/jobs/${jobId}`)
    onProgress?.(job)
    if (job.status !== 'running') {
      if (job.status === 'failed') throw new Error(job.error || 'The pipeline run failed.')
      return job
    }
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
}

// Files are matched to their kind by name: "*site*" -> sites, "*asset*"/"*equipment*" -> assets,
// "*work*"/"*order*"/"*service*" -> work orders. Missing kinds are taken from what is already stored.
export function kindForFile(name: string): 'sites' | 'assets' | 'work_orders' | null {
  const n = name.toLowerCase()
  if (/work|order|service|repair|invoice/.test(n)) return 'work_orders'
  if (/asset|equipment|unit/.test(n)) return 'assets'
  if (/site|store|location/.test(n)) return 'sites'
  return null
}

export async function runPipeline(files: File[], onProgress?: (job: PipelineJob) => void): Promise<PipelineJob> {
  const form = new FormData()
  for (const file of files) {
    const kind = kindForFile(file.name)
    if (!kind) throw new Error(`Can't tell what "${file.name}" contains. Name it with "sites", "assets" or "work_orders".`)
    form.append(kind, file)
  }
  const { job_id } = await api<{ job_id: string }>('/pipeline/run', { method: 'POST', body: form })
  return waitForJob(job_id, onProgress)
}

export async function runSampleData(onProgress?: (job: PipelineJob) => void): Promise<PipelineJob> {
  const { job_id } = await api<{ job_id: string }>('/pipeline/run-sample', { method: 'POST' })
  return waitForJob(job_id, onProgress)
}

export async function runSweep(): Promise<{ finished_at: string; summary: string }> {
  const { job_id } = await api<{ job_id: string }>('/pipeline/run', { method: 'POST', body: new FormData() })
  const job = await waitForJob(job_id)
  const d = job.steps.decide ?? {}
  return { finished_at: new Date().toISOString(), summary: `Re-scored and re-decided: ${String(d.decisions ?? 0)} decisions in ${job.seconds_total ?? '?'}s` }
}

export async function simulateText(_text: string): Promise<Message> {
  throw new Error('Manager text messages are not part of this backend.')
}

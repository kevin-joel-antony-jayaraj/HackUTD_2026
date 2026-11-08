// Live data from the PenguinRE backend, translated into the shapes in types.ts.
// If the backend can't be reached, every call falls back to mockApi.ts so the demo never goes blank.
import * as mock from './mockApi'
import type {
  Asset,
  AssetDetail,
  Call,
  Driver,
  Message,
  Portfolio,
  RiskTier,
  Savings,
  Urgency,
  WorkOrder,
  WorkOrderStatus,
} from './types'

export const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'
// Local demo only: Vite builds this into the bundle, so don't deploy with a real admin token here.
const ADMIN_TOKEN = import.meta.env.VITE_ADMIN_TOKEN || ''

export let usingMockData = false

const ASSET_TYPES: Record<string, string> = {
  rtu: 'Rooftop AC',
  walk_in_cooler: 'Walk-in cooler',
  water_heater: 'Water heater',
}

const FACTOR_LABELS: Record<string, string> = {
  calls_90d: 'Service calls, last 90 days',
  refrigerant_recharges_180d: 'Refrigerant recharges (likely leak)',
  repeat_issue: 'Same problem came back',
  recent_repair: 'Repaired in the last 30 days',
  pm_overdue_months: 'Preventive maintenance overdue',
  hot_days_next_7: 'Hot days in the forecast',
  peak_100f: 'Forecast peak of 100°F or more',
  field_report_negative: 'Manager reported a problem',
}

/* ---------------------------------------------------------------- raw API shapes */
interface ApiDriver { factor: string; value?: number; detail?: string; multiplier?: number }
interface ApiCost {
  preventive_cost_usd: number
  emergency_cost_usd: number
  emergency_breakdown: { repair_usd: number; lost_revenue_usd: number; inventory_loss_usd: number }
  expected_savings_usd: number
  worth_acting_now: boolean
  cost_source: { preventive: string; emergency_repair: string }
}
interface ApiAsset {
  asset_id: string; site_id: string; site_name: string; city: string | null; asset_type: string
  prob: number | null; tier: RiskTier; drivers: ApiDriver[] | null; open_work_order: string | null; cost: ApiCost | null
}
interface ApiWorkOrder {
  id: string; asset_id: string; asset_type: string; site_name: string; city: string | null; tier: RiskTier
  urgency: Urgency; likely_issue: string; tasks: string[]; do_by: string; status: string
  created_at: string; cost: ApiCost | null
}

/* ---------------------------------------------------------------- helpers */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API_BASE + path, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `${res.status} on ${path}`)
  }
  return res.json() as Promise<T>
}

const post = <T>(path: string, body: unknown = {}) =>
  request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${ADMIN_TOKEN}` },
    body: JSON.stringify(body),
  })

// Reads use the backend when it's reachable and the mock data when it isn't (network error only;
// a real API error is shown to the user).
async function live<T>(fromApi: () => Promise<T>, fromMock: () => Promise<T>): Promise<T> {
  try {
    const value = await fromApi()
    usingMockData = false
    return value
  } catch (error) {
    if (!(error instanceof TypeError)) throw error
    usingMockData = true
    return fromMock()
  }
}

const machineName = (assetId: string, type: string) => {
  const n = assetId.match(/(\d+)$/)?.[1]
  return `${ASSET_TYPES[type] || type}${n ? ` ${n}` : ''}`
}

const mainReason = (drivers: ApiDriver[] | null): string => {
  const d = (drivers || []).find((x) => x.factor !== 'age') || drivers?.[0]
  if (!d) return 'No risk drivers reported'
  return d.factor === 'age' ? d.detail || 'Equipment age' : FACTOR_LABELS[d.factor] || d.factor.replaceAll('_', ' ')
}

const toDriver = (d: ApiDriver): Driver =>
  d.factor === 'age'
    ? { factor: 'age', value: d.value, detail: d.detail }
    : { factor: d.factor, detail: d.multiplier ? `${d.multiplier}× normal failure risk` : undefined }

// Gemini writes run summaries in Markdown; the dashboard shows plain text
const plainText = (md: string) =>
  md.replace(/\*\*|[*`#]/g, '').replace(/^\s*[-•]\s+/gm, '').replace(/\n{2,}/g, '\n').trim()

const toStatus = (s: string): WorkOrderStatus => (s === 'done' ? 'completed' : (s as WorkOrderStatus))

const sourceLabel = (cost: ApiCost) =>
  cost.cost_source.emergency_repair === 'portfolio_history' ? 'portfolio history' : 'national average'

/* ---------------------------------------------------------------- reads */
export const getPortfolio = () =>
  live<Portfolio>(async () => {
    const p = await request<{
      sites: number; square_feet: number; assets: number; assets_by_tier: Record<string, number>
      work_orders_by_status: Record<string, number>; texts_today: number
      last_run: { finished_at: string | null; summary: string | null } | null
    }>('/portfolio')
    return {
      sites: p.sites,
      square_feet: p.square_feet,
      machines: p.assets,
      high_risk_count: p.assets_by_tier.high || 0,
      medium_risk_count: p.assets_by_tier.medium || 0,
      work_orders_awaiting_approval: p.work_orders_by_status.pending_approval || 0,
      texts_sent_today: p.texts_today,
      last_run: p.last_run?.finished_at ? { finished_at: p.last_run.finished_at, summary: plainText(p.last_run.summary || '') } : null,
    }
  }, mock.getPortfolio)

export const getSavings = () =>
  live<Savings>(async () => {
    const s = await request<Savings>('/savings')
    return { savings_secured_usd: s.savings_secured_usd, savings_pending_usd: s.savings_pending_usd, exposure_usd: s.exposure_usd }
  }, mock.getSavings)

export const getAssets = () =>
  live<Asset[]>(async () => {
    const rows = await request<ApiAsset[]>('/assets?limit=500')
    return rows.map((a) => ({
      id: a.asset_id,
      site_id: a.site_id,
      site_name: a.site_name,
      city: a.city ? `${a.city}, TX` : '',
      asset_type: a.asset_type,
      prob: a.prob,
      tier: a.tier,
      main_reason: mainReason(a.drivers),
      open_work_order: a.open_work_order,
      expected_savings_usd: a.cost?.expected_savings_usd ?? 0,
      drivers: (a.drivers || []).map((d) => d.factor),
    }))
  }, mock.getAssets)

export const getAsset = (id: string) =>
  live<AssetDetail | null>(async () => {
    let d
    try {
      d = await request<{
        asset: { asset_id: string; asset_type: string; install_date: string | null; site_name: string; site_type: string
                 city: string | null; square_feet: number | null; open_hours: string | null }
        risk_now: { prob: number; tier: RiskTier | 'unknown'; age_years: number | null; multiplier: number; drivers: ApiDriver[] }
        risk_history: { scored_at: string; prob: number }[]
        forecast: { date: string; max_f: number }[]
        cost: ApiCost | null
        work_order_history: { date: string; kind: string; issue: string | null; note: string | null }[]
        work_orders: { id: string; status: string; urgency: Urgency; likely_issue: string; tasks: string[]; do_by: string }[]
      }>(`/assets/${encodeURIComponent(id)}`)
    } catch (error) {
      if (error instanceof Error && error.message.startsWith('unknown asset')) return null
      throw error
    }
    // one point per day, oldest first (the API returns newest first, sometimes several a day)
    const byDay = new Map<string, number>()
    for (const h of [...d.risk_history].reverse()) byDay.set(h.scored_at.slice(0, 10), h.prob)
    const cost = d.cost
    return {
      asset: {
        id: d.asset.asset_id,
        asset_type: d.asset.asset_type,
        install_date: d.asset.install_date || '',
        site_name: d.asset.site_name,
        site_type: d.asset.site_type,
        city: d.asset.city ? `${d.asset.city}, TX` : '',
        square_feet: d.asset.square_feet || 0,
        open_hours: d.asset.open_hours || '',
      },
      risk_now: {
        prob: d.risk_now.prob,
        tier: d.risk_now.tier === 'unknown' ? null : d.risk_now.tier,
        age_years: d.risk_now.age_years ?? 0,
        multiplier: d.risk_now.multiplier,
        drivers: d.risk_now.drivers.map(toDriver),
      },
      risk_history: [...byDay].map(([date, prob]) => ({ date, prob })),
      forecast: d.forecast,
      cost_comparison: {
        preventive_cost_usd: cost?.preventive_cost_usd ?? 0,
        emergency_cost_usd: cost?.emergency_cost_usd ?? 0,
        emergency_breakdown: {
          repair: cost?.emergency_breakdown.repair_usd ?? 0,
          lost_revenue: cost?.emergency_breakdown.lost_revenue_usd ?? 0,
          spoiled_stock: cost?.emergency_breakdown.inventory_loss_usd ?? 0,
        },
        expected_savings_usd: cost?.expected_savings_usd ?? 0,
        worth_acting_now: cost?.worth_acting_now ?? false,
        cost_source: cost ? sourceLabel(cost) : 'not available',
      },
      work_order_history: [...d.work_order_history].reverse().map((h) => ({
        date: h.date, kind: h.kind, issue: (h.issue || '').replaceAll('_', ' '), note: h.note || '',
      })),
      work_orders: d.work_orders.map((w) => ({
        id: w.id, status: toStatus(w.status), urgency: w.urgency, likely_issue: w.likely_issue,
        tasks: w.tasks, do_by: w.do_by, expected_savings_usd: cost?.expected_savings_usd ?? 0,
      })),
    }
  }, () => mock.getAsset(id))

export const getWorkOrders = () =>
  live<WorkOrder[]>(async () => {
    const rows = await request<ApiWorkOrder[]>('/work-orders')
    return rows.map((w) => ({
      id: w.id,
      asset_id: w.asset_id,
      machine_name: machineName(w.asset_id, w.asset_type),
      site: w.site_name,
      city: w.city ? `${w.city}, TX` : '',
      tier: w.tier,
      urgency: w.urgency,
      likely_issue: w.likely_issue,
      tasks: w.tasks,
      do_by: w.do_by,
      status: toStatus(w.status),
      expected_savings_usd: w.cost?.expected_savings_usd ?? 0,
      created_at: w.created_at,
    }))
  }, mock.getWorkOrders)

interface InboundResult {
  registered?: string; ignored?: string; recorded?: string; skipped?: string
  asset_id?: string | null; working?: boolean; summary?: string
}

// What PenguinRE did with a manager's text, in one line
function outcome(r: InboundResult | null): string {
  if (!r) return 'being handled'
  if (r.registered) return 'phone linked as the manager'
  if (r.ignored) return `not understood (${r.ignored})`
  if (r.recorded) return r.asset_id ? `noted for ${r.asset_id}${r.working ? ', reported working' : ''}` : 'noted, no machine matched'
  if (r.summary) {
    const text = plainText(r.summary)
      .replace(/^Gemini unavailable \([^)]*\); finished with the scripted planner\.\s*/, '')
      .replace(/^Run summary:\s*/, '')
    if (/already open/i.test(text) && !/escalat|created/i.test(text)) return 'risk updated; work order already open'
    const lines = text.split('\n').map((l) => l.trim()).filter((l) => l && !/^summary\b|:$/i.test(l))
    const best = lines.find((l) => /escalat|approved|on.hold|created|WO-\d+/i.test(l)) || lines[0] || 'handled'
    return best.slice(0, 180)
  }
  return 'handled'
}

export const getMessages = () =>
  live<Message[]>(async () => {
    const [rows, replies] = await Promise.all([
      request<{ id: number; recipient_name: string | null; to_key: string; created_at: string
                text: string; dry_run: boolean; sent: boolean; error: string | null }[]>('/messages'),
      request<{ message_id: string; text: string; result: InboundResult | null; received_at: string }[]>('/inbound'),
    ])
    const sent: Message[] = rows.map((m) => ({
      id: `MSG-${m.id}`,
      recipient: m.recipient_name || m.to_key,
      time: m.created_at,
      text: m.error && !m.sent && !m.dry_run ? `${m.text} (not delivered: ${m.error})` : m.text,
      dry_run: m.dry_run,
    }))
    const received: Message[] = replies
      .filter((r) => !r.result?.skipped)
      .map((r) => ({
        id: `IN-${r.message_id}`,
        recipient: 'Manager reply',
        time: r.received_at,
        text: `"${r.text.trim()}" → ${outcome(r.result)}`,
        dry_run: false,
      }))
    return [...sent, ...received].sort((a, b) => b.time.localeCompare(a.time))
  }, mock.getMessages)

export const getCalls = () =>
  live<Call[]>(async () => {
    const rows = await request<{ id: number; recipient_name: string | null; created_at: string; script: string
                                 dry_run: boolean }[]>('/calls')
    return rows.map((c) => ({
      id: `CALL-${c.id}`, recipient: c.recipient_name || 'Service worker', time: c.created_at,
      script: c.script, dry_run: c.dry_run,
    }))
  }, mock.getCalls)

/* ---------------------------------------------------------------- actions */
async function setStatus(id: string, action: 'approve' | 'hold'): Promise<WorkOrder | null> {
  if (usingMockData) return action === 'approve' ? mock.approveWorkOrder(id) : mock.holdWorkOrder(id)
  await post(`/work-orders/${encodeURIComponent(id)}/${action}`, { note: `${action === 'approve' ? 'approved' : 'held'} on dashboard` })
  return (await getWorkOrders()).find((w) => w.id === id) || null
}

export const approveWorkOrder = (id: string) => setStatus(id, 'approve')
export const holdWorkOrder = (id: string) => setStatus(id, 'hold')

// The backend runs the sweep in the background; wait for the new run to finish (Gemini takes ~30-60 s).
export async function runSweep(): Promise<{ finished_at: string; summary: string }> {
  if (usingMockData) return mock.runSweep()
  type Run = { run_id: number; finished_at: string | null; summary: string | null }
  const before = (await request<Run[]>('/runs?limit=1'))[0]?.run_id ?? 0
  await post('/jobs/sweep')
  const deadline = Date.now() + 180_000
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 3000))
    const run = (await request<Run[]>('/runs?limit=1'))[0]
    if (run && run.run_id > before && run.finished_at) {
      return { finished_at: run.finished_at, summary: plainText(run.summary || '') }
    }
  }
  throw new Error('The sweep is still running; results will appear when it finishes.')
}

export async function simulateText(text: string): Promise<Message> {
  if (usingMockData) return mock.simulateText(text)
  await post('/photon/simulate', { text })
  return { id: `SIM-${Date.now()}`, recipient: 'Regional manager · simulated', time: new Date().toISOString(), text, dry_run: true }
}

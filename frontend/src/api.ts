import assetsCsv from '../../sitesignal-backend/samples/assets.csv?raw'
import sitesCsv from '../../sitesignal-backend/samples/sites.csv?raw'
import workOrdersCsv from '../../sitesignal-backend/samples/work_orders.csv?raw'
import type {
  Asset,
  AssetDetail,
  Call,
  Driver,
  Message,
  Portfolio,
  Savings,
  ServiceRecord,
  WorkOrder,
} from './types'

export const API_BASE = 'http://localhost:8000'

interface SiteRow {
  id: string
  name: string
  category: string
  city: string
  region: string
  refrigerated: boolean
  latitude: number | null
  longitude: number | null
}

interface AssetRow {
  id: string
  siteId: string
  rawType: string
  type: string
  installDate: string | null
  manufacturer: string
  model: string
  refrigerant: string
}

interface WorkOrderRow {
  sourceRow: number
  assetId: string
  siteId: string
  date: string
  type: string
  problem: string
  refrigerantCharge: number | null
  invoiceTotal: number | null
  notes: string
}

interface AssetFeatures {
  calls90d: number
  refrigerantRecharges180d: number
  repeatIssue: boolean
  daysSinceLastRepair: number | null
  pmOverdueMonths: number
}

const DAY_MS = 24 * 60 * 60 * 1000
const HIGH_RISK_THRESHOLD = 0.2
const MEDIUM_RISK_THRESHOLD = 0.08
const MEDIAN_LIFE_YEARS: Record<string, number> = {
  rtu: 15,
  walk_in_cooler: 15,
  water_heater: 10,
}
const WEIGHTS: Record<string, number> = {
  calls_90d: 0.35,
  refrigerant_recharges_180d: 0.6,
  repeat_issue: 0.4,
  recent_repair: 0.25,
  pm_overdue_months: 0.1,
  hot_days_next_7: 0.2,
  peak_100f: 0.3,
  field_report_negative: 0.8,
}

function parseCsv(source: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let cell = ''
  let quoted = false

  for (let index = 0; index < source.length; index += 1) {
    const character = source[index]
    if (quoted) {
      if (character === '"' && source[index + 1] === '"') {
        cell += '"'
        index += 1
      } else if (character === '"') {
        quoted = false
      } else {
        cell += character
      }
    } else if (character === '"' && cell.length === 0) {
      quoted = true
    } else if (character === ',') {
      row.push(cell)
      cell = ''
    } else if (character === '\n' || character === '\r') {
      if (character === '\r' && source[index + 1] === '\n') index += 1
      row.push(cell)
      if (row.some((value) => value.length > 0)) rows.push(row)
      row = []
      cell = ''
    } else {
      cell += character
    }
  }

  if (cell.length > 0 || row.length > 0) {
    row.push(cell)
    if (row.some((value) => value.length > 0)) rows.push(row)
  }
  return rows
}

function recordsFromCsv(source: string): Array<Record<string, string>> {
  const [headers, ...rows] = parseCsv(source)
  if (!headers) throw new Error('The imported portfolio CSV is empty.')
  return rows.map((row) => Object.fromEntries(headers.map((header, index) => [header, row[index] ?? ''])))
}

function numberValue(value: string): number | null {
  if (!value.trim()) return null
  const parsed = Number(value.replace(/[$,]/g, ''))
  return Number.isFinite(parsed) ? parsed : null
}

function parseDate(value: string): string | null {
  const input = value.trim()
  if (!input) return null
  if (/^\d{4}$/.test(input)) return `${input}-07-01`
  if (/^\d{4}-\d{2}$/.test(input)) return `${input}-01`
  if (/^\d{4}-\d{2}-\d{2}$/.test(input)) return input
  const monthFirst = input.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/)
  if (monthFirst) {
    return `${monthFirst[3]}-${monthFirst[1].padStart(2, '0')}-${monthFirst[2].padStart(2, '0')}`
  }
  return null
}

function dateNumber(value: string): number {
  return Date.parse(`${value}T00:00:00Z`)
}

function ageDays(date: string, today: string): number {
  return Math.floor((dateNumber(today) - dateNumber(date)) / DAY_MS)
}

function assetType(value: string): string {
  const type = value.toLowerCase()
  if (['rtu', 'rooftop', 'roof top', 'packaged unit', 'package unit', ' a/c', 'a/c', 'hvac'].some((term) => type.includes(term))) {
    return 'rtu'
  }
  if (['walk-in', 'walk in', 'cooler', 'freezer', 'refrigeration'].some((term) => type.includes(term))) {
    return 'walk_in_cooler'
  }
  if (['water heater', 'boiler', 'hot water'].some((term) => type.includes(term))) return 'water_heater'
  return value
}

function workKind(value: string): 'repair' | 'emergency' | 'pm' | 'inspection' {
  const type = value.toLowerCase()
  if (['emergency', 'urgent', 'after hours', 'p1', 'critical'].some((term) => type.includes(term))) return 'emergency'
  if (['pm', 'preventive', 'preventative', 'maintenance', 'filter', 'scheduled'].some((term) => type.includes(term))) return 'pm'
  if (['inspection', 'inspect', 'survey'].some((term) => type.includes(term))) return 'inspection'
  return 'repair'
}

function issueCategory(problem: string, notes: string): string | null {
  const text = `${problem} ${notes}`.toLowerCase()
  if (['warm', 'not cool', 'no cool', 'not cold', 'blowing hot'].some((term) => text.includes(term))) return 'not_cooling'
  if (['leak', 'low charge', 'low refrigerant'].some((term) => text.includes(term))) return 'leak'
  if (['short cycl', 'cycling'].some((term) => text.includes(term))) return 'short_cycling'
  if (text.includes('capacitor')) return 'capacitor'
  if (text.includes('compressor')) return 'compressor'
  if (['not holding temp', 'cooler temp', 'temp high'].some((term) => text.includes(term))) return 'cooler_temp'
  if (['noise', 'loud', 'rattl', 'squeal'].some((term) => text.includes(term))) return 'noise'
  if (['water', 'drain', 'drip'].some((term) => text.includes(term))) return 'water'
  return null
}

function riskTier(probability: number | null): Asset['tier'] {
  if (probability === null) return null
  if (probability >= HIGH_RISK_THRESHOLD) return 'high'
  if (probability >= MEDIUM_RISK_THRESHOLD) return 'medium'
  return 'low'
}

const sites = recordsFromCsv(sitesCsv).map((row): SiteRow => ({
  id: row['Store Number'].trim().toUpperCase(),
  name: row['Store Name'],
  category: row.Category,
  city: row.City,
  region: row.Market,
  refrigerated: row.Refrigerated.toLowerCase() === 'y',
  latitude: numberValue(row.Latitude),
  longitude: numberValue(row.Longitude),
}))
const sitesById = new Map(sites.map((site) => [site.id, site]))

const sourceAssets = recordsFromCsv(assetsCsv).map((row): AssetRow => ({
  id: row['Asset Tag'].trim().toUpperCase(),
  siteId: row.Store.trim().toUpperCase(),
  rawType: row['Equipment Type'],
  type: assetType(row['Equipment Type']),
  installDate: parseDate(row.Installed),
  manufacturer: row.Manufacturer,
  model: row.Model,
  refrigerant: row.Refrigerant,
}))
const assetsById = new Map(sourceAssets.map((asset) => [asset.id, asset]))

const serviceHistory = recordsFromCsv(workOrdersCsv).flatMap((row, index): WorkOrderRow[] => {
  const date = parseDate(row['Service Date'])
  const id = row['Equipment ID'].trim().toUpperCase()
  const siteId = row.Store.trim().toUpperCase()
  if (!date) return []
  return [{
    sourceRow: index + 2,
    assetId: id,
    siteId,
    date,
    type: row['Work Type'],
    problem: row.Problem,
    refrigerantCharge: numberValue(row['Refrigerant Charge']),
    invoiceTotal: numberValue(row['Invoice Total']),
    notes: row['Tech Notes'],
  }]
})

const today = new Date().toISOString().slice(0, 10)
const assetHistory = new Map<string, WorkOrderRow[]>()
for (const record of serviceHistory) {
  if (!assetsById.has(record.assetId)) continue
  const rows = assetHistory.get(record.assetId) ?? []
  rows.push(record)
  assetHistory.set(record.assetId, rows)
}

function featuresFor(asset: AssetRow): AssetFeatures {
  const records = (assetHistory.get(asset.id) ?? []).filter((row) => row.date <= today)
  const repairRecords = records.filter((row) => {
    const kind = workKind(row.type)
    return kind === 'repair' || kind === 'emergency'
  })
  const recentRepairs = repairRecords.filter((row) => ageDays(row.date, today) < 90)
  const repeatingIssues = new Set<string>()
  const issueCounts = new Map<string, number>()
  for (const row of recentRepairs) {
    const category = issueCategory(row.problem, row.notes)
    if (category) issueCounts.set(category, (issueCounts.get(category) ?? 0) + 1)
  }
  for (const [category, count] of issueCounts) {
    if (count >= 2) repeatingIssues.add(category)
  }

  const pmRecords = records
    .filter((row) => workKind(row.type) === 'pm')
    .sort((a, b) => b.date.localeCompare(a.date))
  const daysSincePm = pmRecords[0] ? ageDays(pmRecords[0].date, today) : 0

  return {
    calls90d: recentRepairs.length,
    refrigerantRecharges180d: records.filter((row) =>
      ageDays(row.date, today) < 180 &&
      ((row.refrigerantCharge !== null && row.refrigerantCharge > 0) || /\b(added|topped off|top off|recharg\w*|charged)\b.*refrig|refrig\w*\s+(added|charge)/i.test(row.notes)),
    ).length,
    repeatIssue: repeatingIssues.size > 0,
    daysSinceLastRepair: repairRecords[0]
      ? ageDays(repairRecords.reduce((latest, row) => row.date > latest ? row.date : latest, repairRecords[0].date), today)
      : null,
    pmOverdueMonths: pmRecords.length ? Math.max(0, Math.round(((daysSincePm - 90) / 30) * 10) / 10) : 0,
  }
}

function calculateRisk(asset: AssetRow): { prob: number | null; age: number | null; multiplier: number | null; drivers: Driver[] } {
  const medianLife = MEDIAN_LIFE_YEARS[asset.type]
  if (!asset.installDate || !medianLife) return { prob: null, age: null, multiplier: null, drivers: [] }

  const features = featuresFor(asset)
  const age = Math.max(ageDays(asset.installDate, today) / 365.25, 0)
  const shape = 3
  const scale = medianLife / Math.pow(Math.log(2), 1 / shape)
  const baseSurvival = Math.exp(Math.pow(age / scale, shape) - Math.pow((age + 30 / 365.25) / scale, shape))
  const terms: Record<string, number> = {
    calls_90d: WEIGHTS.calls_90d * features.calls90d,
    refrigerant_recharges_180d: WEIGHTS.refrigerant_recharges_180d * features.refrigerantRecharges180d,
    repeat_issue: WEIGHTS.repeat_issue * Number(features.repeatIssue),
    recent_repair: WEIGHTS.recent_repair * Number(features.daysSinceLastRepair !== null && features.daysSinceLastRepair < 30),
    pm_overdue_months: WEIGHTS.pm_overdue_months * Math.min(features.pmOverdueMonths, 12),
    hot_days_next_7: 0,
    peak_100f: 0,
    field_report_negative: 0,
  }
  const multiplier = Math.exp(Math.min(Object.values(terms).reduce((sum, value) => sum + value, 0), 8))
  const probability = 1 - Math.pow(baseSurvival, multiplier)
  const drivers: Driver[] = [{
    factor: 'age',
    value: Math.round(age * 10) / 10,
    detail: `${age.toFixed(1)} yrs vs ${medianLife}-yr median life`,
  }]
  for (const [factor, logHazard] of Object.entries(terms).sort((a, b) => b[1] - a[1])) {
    if (logHazard <= 0) continue
    const detail = factor === 'calls_90d'
      ? `${features.calls90d} repair or emergency records in the last 90 days`
      : factor === 'refrigerant_recharges_180d'
        ? `${features.refrigerantRecharges180d} recorded refrigerant charges in the last 180 days`
        : factor === 'repeat_issue'
          ? 'The same issue appeared at least twice in recent service history'
          : factor === 'recent_repair'
            ? `A repair was recorded ${features.daysSinceLastRepair} days ago`
            : `${features.pmOverdueMonths} months beyond the 90-day PM interval`
    drivers.push({
      factor,
      value: factor === 'calls_90d' ? features.calls90d
        : factor === 'refrigerant_recharges_180d' ? features.refrigerantRecharges180d
          : factor === 'repeat_issue' ? features.repeatIssue
            : factor === 'recent_repair' ? true : features.pmOverdueMonths,
      detail,
      log_hazard: logHazard,
      multiplier: Math.round(Math.exp(logHazard) * 100) / 100,
    })
  }
  return { prob: Math.round(probability * 10_000) / 10_000, age: Math.round(age * 100) / 100, multiplier: Math.round(multiplier * 1000) / 1000, drivers }
}

function machineName(asset: AssetRow): string {
  const label = asset.type === 'rtu'
    ? 'Rooftop AC'
    : asset.type === 'walk_in_cooler'
      ? 'Walk-in cooler'
      : asset.type === 'water_heater'
        ? 'Water heater'
        : asset.rawType
  const suffix = asset.id.match(/(?:RTU|WIC|WH)(\d+)$/i)?.[1]
  return suffix ? `${label} ${suffix}` : label
}

function toServiceRecord(row: WorkOrderRow): ServiceRecord {
  const asset = assetsById.get(row.assetId)
  const site = sitesById.get(row.siteId)
  return {
    source_row: row.sourceRow,
    asset_id: row.assetId,
    site_id: row.siteId,
    machine_name: asset ? machineName(asset) : row.assetId,
    site_name: site?.name ?? row.siteId,
    city: site?.city ?? '',
    date: row.date,
    work_type: row.type,
    problem: row.problem,
    refrigerant_charge: row.refrigerantCharge,
    invoice_total_usd: row.invoiceTotal,
    tech_notes: row.notes,
  }
}

const serviceRecords = serviceHistory.map(toServiceRecord).sort((a, b) => b.date.localeCompare(a.date))
const scoredAssets: Asset[] = sourceAssets.map((asset) => {
  const site = sitesById.get(asset.siteId)
  if (!site) throw new Error(`Asset ${asset.id} references missing site ${asset.siteId}.`)
  const risk = calculateRisk(asset)
  const primaryDriver = risk.drivers.find((driver) => driver.factor !== 'age' && (driver.log_hazard ?? 0) > 0)
  const records = assetHistory.get(asset.id) ?? []
  const lastService = records.reduce<string | null>((latest, row) => (
    row.date <= today && (!latest || row.date > latest) ? row.date : latest
  ), null)
  const spend = records.reduce((sum, row) => sum + (row.invoiceTotal !== null && row.invoiceTotal >= 0 ? row.invoiceTotal : 0), 0)
  const mainReason = risk.prob === null
    ? asset.installDate ? 'Risk model does not support this equipment type' : 'Installation date not provided'
    : primaryDriver ? primaryDriver.detail ?? 'Additional factors from service history'
      : 'Age-based estimate; no additional risk factors in the available records'
  return {
    id: asset.id,
    site_id: asset.siteId,
    site_name: site.name,
    site_type: site.category,
    city: site.city,
    asset_type: asset.type,
    install_date: asset.installDate,
    prob: risk.prob,
    tier: riskTier(risk.prob),
    main_reason: mainReason,
    last_service_date: lastService,
    historical_service_spend_usd: Math.round(spend * 100) / 100,
    drivers: risk.drivers.map((driver) => driver.factor),
  }
})

const assetsByIdForDetails = new Map(scoredAssets.map((asset) => [asset.id, asset]))

function detailFor(id: string): AssetDetail | null {
  const summary = assetsByIdForDetails.get(id)
  const source = assetsById.get(id)
  if (!summary || !source) return null
  const site = sitesById.get(source.siteId)
  if (!site) throw new Error(`Asset ${id} references missing site ${source.siteId}.`)
  const risk = calculateRisk(source)
  const history = (assetHistory.get(id) ?? [])
    .slice()
    .sort((a, b) => b.date.localeCompare(a.date))
    .map(toServiceRecord)
  return {
    asset: {
      id,
      asset_type: source.type,
      install_date: source.installDate,
      site_name: site.name,
      site_type: site.category,
      city: site.city,
      square_feet: null,
      open_hours: null,
      manufacturer: source.manufacturer,
      model: source.model,
      refrigerant: source.refrigerant,
    },
    risk_now: {
      prob: risk.prob,
      tier: riskTier(risk.prob),
      age_years: risk.age,
      multiplier: risk.multiplier,
      drivers: risk.drivers,
    },
    risk_history: [],
    forecast: [],
    historical_service_spend_usd: summary.historical_service_spend_usd,
    service_record_count: history.length,
    work_order_history: history,
    work_orders: [],
  }
}

const invoiceRecords = serviceHistory.filter((record) => record.invoiceTotal !== null && record.invoiceTotal >= 0)
const totalInvoiceSpend = invoiceRecords.reduce((sum, record) => sum + (record.invoiceTotal ?? 0), 0)
const portfolio: Portfolio = {
  sites: sites.length,
  machines: sourceAssets.length,
  supported_assets: scoredAssets.filter((asset) => asset.prob !== null).length,
  high_risk_count: scoredAssets.filter((asset) => asset.tier === 'high').length,
  medium_risk_count: scoredAssets.filter((asset) => asset.tier === 'medium').length,
  service_records: serviceHistory.length,
  latest_service_date: serviceHistory.reduce<string | null>((latest, record) => (
    record.date <= today && (!latest || record.date > latest) ? record.date : latest
  ), null),
}
const savings: Savings = {
  historical_service_spend_usd: Math.round(totalInvoiceSpend * 100) / 100,
  records_with_invoice_total: invoiceRecords.length,
  total_service_records: serviceHistory.length,
}

const copy = <T>(value: T): T => structuredClone(value)

export async function getPortfolio(): Promise<Portfolio> {
  return copy(portfolio)
}

export async function getSavings(): Promise<Savings> {
  return copy(savings)
}

export async function getAssets(): Promise<Asset[]> {
  return copy(scoredAssets)
}

export async function getAsset(id: string): Promise<AssetDetail | null> {
  return copy(detailFor(id))
}

export async function getWorkOrders(): Promise<WorkOrder[]> {
  return copy(serviceRecords)
}

export async function getMessages(): Promise<Message[]> {
  return []
}

export async function getCalls(): Promise<Call[]> {
  return []
}

export async function approveWorkOrder(id: string): Promise<null> {
  throw new Error(`Work order approval is unavailable for historical CSV record ${id}.`)
}

export async function holdWorkOrder(id: string): Promise<null> {
  throw new Error(`Work order hold is unavailable for historical CSV record ${id}.`)
}

export async function runSweep(): Promise<{ finished_at: string; summary: string }> {
  throw new Error('Agent sweeps are not available in the CSV-backed dashboard.')
}

export async function simulateText(_text: string): Promise<Message> {
  throw new Error('Manager messages are not included in the imported CSV data.')
}

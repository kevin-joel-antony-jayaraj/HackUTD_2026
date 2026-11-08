export type RiskTier = 'high' | 'medium' | 'low' | null
// Safety Index bands from the backend (0-100, 100 = safest; an uncalibrated triage score, not a probability)
export type SafetyBand = 'critical' | 'poor' | 'fair' | 'good' | 'unknown'

export interface Portfolio {
  sites: number
  machines: number
  supported_assets: number
  high_risk_count: number
  medium_risk_count: number
  service_records: number
  latest_service_date: string | null
}

export interface Savings {
  historical_service_spend_usd: number
  records_with_invoice_total: number
  total_service_records: number
}

export interface Driver {
  factor: string
  value?: number | boolean
  detail?: string
  multiplier?: number
  log_hazard?: number
}

export interface Asset {
  id: string
  site_id: string
  site_name: string
  site_type: string
  city: string
  asset_type: string
  install_date: string | null
  prob: number | null
  tier: RiskTier
  main_reason: string
  last_service_date: string | null
  historical_service_spend_usd: number
  drivers: string[]
  safety_index: number | null
  band: SafetyBand
  confidence: number
  constraints: string[]
  decision_id: number | null
  decision_action: string | null
  decision_urgency: string | null
  decision_status: string | null
}

export interface Decision {
  id: number
  asset_id: string
  site_id: string
  action: 'create_work_order' | 'request_data' | 'monitor' | 'no_action'
  urgency: 'today' | '48h' | 'this_week' | 'none'
  tasks: string[]
  reason: string
  requires_human_review: boolean
  status: 'pending_review' | 'approved' | 'rejected' | 'done'
  source: 'llm' | 'rule' | 'rule_default'
  agreement: boolean
  safety_index: number | null
  band: SafetyBand
  confidence: number
}

export interface PipelineJob {
  id: string
  status: 'running' | 'done' | 'failed'
  step: string | null
  steps: Record<string, { status: string; seconds?: number; [key: string]: unknown }>
  step_order: string[]
  error?: string
  seconds_total?: number
}

export interface RiskHistoryPoint {
  date: string
  prob: number
  safety_index: number | null
}

export interface ForecastDay {
  date: string
  max_f: number
}

export interface ServiceRecord {
  source_row: number
  asset_id: string
  site_id: string
  machine_name: string
  site_name: string
  city: string
  date: string
  work_type: string
  problem: string
  refrigerant_charge: number | null
  invoice_total_usd: number | null
  tech_notes: string
}

export interface AssetDetail {
  asset: {
    id: string
    asset_type: string
    install_date: string | null
    site_name: string
    site_type: string
    city: string
    square_feet: null
    open_hours: null
    manufacturer: string
    model: string
    refrigerant: string
  }
  risk_now: {
    prob: number | null
    tier: RiskTier
    safety_index: number | null
    band: SafetyBand
    confidence: number
    constraints: string[]
    data_gaps: string[]
    age_years: number | null
    multiplier: number | null
    drivers: Driver[]
  }
  risk_history: RiskHistoryPoint[]
  forecast: ForecastDay[]
  historical_service_spend_usd: number
  service_record_count: number
  work_order_history: ServiceRecord[]
  work_orders: ServiceRecord[]
  decision: Decision | null
}

export type WorkOrder = ServiceRecord

export interface Message {
  id: string
  recipient: string
  time: string
  text: string
  dry_run: boolean
}

export interface Call {
  id: string
  recipient: string
  time: string
  script: string
  dry_run: boolean
}

export type ActivityItem =
  | (Message & { kind: 'message' })
  | (Call & { kind: 'call' })

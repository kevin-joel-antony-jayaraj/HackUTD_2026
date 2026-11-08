export type RiskTier = 'high' | 'medium' | 'low' | null
export type WorkOrderStatus = 'pending_approval' | 'approved' | 'on_hold' | 'completed'
export type Urgency = 'today' | '48h' | 'this_week'

export interface LastRun {
  finished_at: string
  summary: string
}

export interface Portfolio {
  sites: number
  square_feet: number
  machines: number
  high_risk_count: number
  medium_risk_count: number
  work_orders_awaiting_approval: number
  texts_sent_today: number
  last_run: LastRun | null
}

export interface Savings {
  savings_secured_usd: number
  savings_pending_usd: number
  exposure_usd: number
}

export interface Driver {
  factor: string
  value?: number | boolean
  detail?: string
}

export interface Asset {
  id: string
  site_id: string
  site_name: string
  city: string
  asset_type: string
  prob: number | null
  tier: RiskTier
  main_reason: string
  open_work_order: string | null
  expected_savings_usd: number
  drivers: string[]
}

export interface RiskHistoryPoint {
  date: string
  prob: number
}

export interface ForecastDay {
  date: string
  max_f: number
}

export interface RepairRecord {
  date: string
  kind: string
  issue: string
  note: string
}

export interface CostComparison {
  preventive_cost_usd: number
  emergency_cost_usd: number
  emergency_breakdown: {
    repair: number
    lost_revenue: number
    spoiled_stock: number
  }
  expected_savings_usd: number
  worth_acting_now: boolean
  cost_source: string
}

export interface AssetDetail {
  asset: {
    id: string
    asset_type: string
    install_date: string
    site_name: string
    site_type: string
    city: string
    square_feet: number
    open_hours: string
  }
  risk_now: {
    prob: number
    tier: RiskTier
    age_years: number
    multiplier: number
    drivers: Driver[]
  }
  risk_history: RiskHistoryPoint[]
  forecast: ForecastDay[]
  cost_comparison: CostComparison
  work_order_history: RepairRecord[]
  work_orders: Array<{
    id: string
    status: WorkOrderStatus
    urgency: Urgency
    likely_issue: string
    tasks: string[]
    do_by: string
    expected_savings_usd: number
  }>
}

export interface WorkOrder {
  id: string
  asset_id: string
  machine_name: string
  site: string
  city: string
  tier: RiskTier
  urgency: Urgency
  likely_issue: string
  tasks: string[]
  do_by: string
  status: WorkOrderStatus
  expected_savings_usd: number
  created_at: string
}

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

export interface MockData {
  portfolio: Portfolio
  savings: Savings
  assets: Asset[]
  assetDetails: Record<string, AssetDetail>
  workOrders: WorkOrder[]
  messages: Message[]
  calls: Call[]
}

export type ActivityItem =
  | (Message & { kind: 'message' })
  | (Call & { kind: 'call' })

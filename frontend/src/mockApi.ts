import initialMockData from './mockData.json'
import type {
  Asset,
  AssetDetail,
  Call,
  Message,
  MockData,
  Portfolio,
  Savings,
  WorkOrder,
} from './types'

export const API_BASE = 'http://localhost:8000'

const mockData = structuredClone(initialMockData) as MockData

const copy = <T>(value: T): T => structuredClone(value)

export async function getPortfolio(): Promise<Portfolio> {
  return copy(mockData.portfolio)
}

export async function getSavings(): Promise<Savings> {
  return copy(mockData.savings)
}

export async function getAssets(): Promise<Asset[]> {
  return copy(mockData.assets)
}

export async function getAsset(id: string): Promise<AssetDetail | null> {
  return mockData.assetDetails[id] ? copy(mockData.assetDetails[id]) : null
}

export async function getWorkOrders(): Promise<WorkOrder[]> {
  return copy(mockData.workOrders)
}

export async function getMessages(): Promise<Message[]> {
  return copy(mockData.messages)
}

export async function getCalls(): Promise<Call[]> {
  return copy(mockData.calls)
}

export async function approveWorkOrder(id: string): Promise<WorkOrder | null> {
  const workOrder = mockData.workOrders.find((item) => item.id === id)
  if (!workOrder || workOrder.status !== 'pending_approval') return null

  workOrder.status = 'approved'
  mockData.portfolio.work_orders_awaiting_approval = Math.max(
    mockData.portfolio.work_orders_awaiting_approval - 1,
    0,
  )
  mockData.savings.savings_secured_usd += workOrder.expected_savings_usd
  mockData.savings.savings_pending_usd = Math.max(
    mockData.savings.savings_pending_usd - workOrder.expected_savings_usd,
    0,
  )

  const detail = mockData.assetDetails[workOrder.asset_id]
  const detailWorkOrder = detail?.work_orders.find((item) => item.id === id)
  if (detailWorkOrder) detailWorkOrder.status = 'approved'

  return copy(workOrder)
}

export async function holdWorkOrder(id: string): Promise<WorkOrder | null> {
  const workOrder = mockData.workOrders.find((item) => item.id === id)
  if (!workOrder || workOrder.status !== 'pending_approval') return null

  workOrder.status = 'on_hold'
  mockData.portfolio.work_orders_awaiting_approval = Math.max(
    mockData.portfolio.work_orders_awaiting_approval - 1,
    0,
  )
  const detail = mockData.assetDetails[workOrder.asset_id]
  const detailWorkOrder = detail?.work_orders.find((item) => item.id === id)
  if (detailWorkOrder) detailWorkOrder.status = 'on_hold'

  return copy(workOrder)
}

export async function runSweep(): Promise<{ finished_at: string; summary: string }> {
  const finishedAt = new Date().toISOString()
  const summary = `Reviewed ${mockData.portfolio.machines} machines. Demo sweep completed; no external messages were sent.`
  mockData.portfolio.last_run = { finished_at: finishedAt, summary }
  return copy(mockData.portfolio.last_run)
}

export async function simulateText(text: string): Promise<Message> {
  const now = new Date().toISOString()
  const message: Message = {
    id: `MSG-DEMO-${Date.now()}`,
    recipient: 'Regional manager · simulated inbound',
    time: now,
    text,
    dry_run: true,
  }
  mockData.messages.unshift(message)

  const approvalMatch = text.match(/\bAPPROVE\s+(WO-\d+)\b/i)
  const holdMatch = text.match(/\bHOLD\s+(WO-\d+)\b/i)
  if (approvalMatch) await approveWorkOrder(approvalMatch[1].toUpperCase())
  if (holdMatch) await holdWorkOrder(holdMatch[1].toUpperCase())

  return copy(message)
}

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import {
  approveWorkOrder,
  getAsset,
  getAssets,
  getCalls,
  getMessages,
  getPortfolio,
  getSavings,
  getWorkOrders,
  holdWorkOrder,
  runSweep,
  simulateText,
} from './api'
import penguinLogo from './assets/Pengu.jpg'
import type {
  ActivityItem,
  Asset,
  AssetDetail,
  Call,
  Message,
  Portfolio,
  RiskTier,
  Savings,
  WorkOrder,
} from './types'
import './App.css'

type Screen = 'overview' | 'machines' | 'detail' | 'work-orders' | 'activity' | 'demo'
type Theme = 'light' | 'dark'

interface DashboardData {
  portfolio: Portfolio
  savings: Savings
  assets: Asset[]
  workOrders: WorkOrder[]
  messages: Message[]
  calls: Call[]
}

const MACHINE_LABELS: Record<string, string> = {
  rtu: 'Rooftop AC',
  walk_in_cooler: 'Walk-in cooler',
  water_heater: 'Water heater',
}

const DRIVER_LABELS: Record<string, string> = {
  calls_90d: 'Service calls, last 90 days',
  refrigerant_recharges_180d: 'Refrigerant recharges (likely leak)',
  repeat_issue: 'Same problem came back',
  recent_repair: 'Repaired in the last 30 days',
  pm_overdue_months: 'Preventive maintenance overdue',
  hot_days_next_7: 'Hot days in the forecast',
  peak_100f: 'Forecast peak of 100°F or more',
  field_report_negative: 'Manager reported a problem',
}

const NAV_ITEMS: Array<{ id: Screen; label: string; icon: string }> = [
  { id: 'overview', label: 'Overview', icon: '⌂' },
  { id: 'machines', label: 'Machines', icon: '▦' },
  { id: 'work-orders', label: 'Work orders', icon: '☷' },
  { id: 'activity', label: 'Activity', icon: '◷' },
  { id: 'demo', label: 'Demo controls', icon: '⚙' },
]
const TODAY = new Date()
const EMPTY_ASSETS: Asset[] = []

const CURRENCY = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
})

function money(amount: number): string {
  return CURRENCY.format(amount)
}

function percent(probability: number | null): string {
  if (probability === null || !Number.isFinite(probability)) return '—'
  if (probability > 0 && probability < 0.005) return '<1%'
  return `${Math.round(probability * 100)}%`
}

function relativeTime(timestamp: string): string {
  const date = new Date(timestamp)
  if (Number.isNaN(date.getTime())) return 'Time unavailable'
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000))
  if (seconds < 60) return 'Just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} hr ago`
  const days = Math.floor(hours / 24)
  return days === 1 ? 'Yesterday' : `${days} days ago`
}

function shortDate(date: string): string {
  const parsed = new Date(`${date}T12:00:00`)
  return Number.isNaN(parsed.getTime())
    ? 'Date unavailable'
    : parsed.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
}

function longDate(date: Date): string {
  return date.toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  })
}

function machineName(asset: Pick<Asset, 'id' | 'asset_type'>): string {
  const label = MACHINE_LABELS[asset.asset_type] || 'Machine'
  const suffix = asset.id.match(/(?:RTU|WIC|WH)(\d+)$/i)?.[1]
  return suffix ? `${label} ${suffix}` : label
}

function tierLabel(tier: RiskTier): string {
  if (tier === null) return 'No data'
  return `${tier[0].toUpperCase()}${tier.slice(1)}`
}

function RiskBadge({ tier }: { tier: RiskTier }) {
  const icon = tier === 'high' ? '▲' : tier === 'medium' ? '!' : tier === 'low' ? '✓' : '—'
  return (
    <span className={`risk-badge risk-${tier || 'none'}`}>
      <span className="risk-icon" aria-hidden="true">{icon}</span>
      {tierLabel(tier)}
    </span>
  )
}

function Icon({ children }: { children: ReactNode }) {
  return <span className="nav-icon" aria-hidden="true">{children}</span>
}

function StatCard({ label, value, note, icon }: { label: string; value: string; note: string; icon: string }) {
  return (
    <article className="stat-card">
      <div className="stat-heading"><span>{label}</span><Icon>{icon}</Icon></div>
      <strong>{value}</strong>
      <small>{note}</small>
    </article>
  )
}

function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>
}

function PageHeader({ title, description, actions }: { title: string; description: string; actions?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <p className="date-line">{longDate(TODAY)}</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions}
    </div>
  )
}

function App() {
  const [screen, setScreen] = useState<Screen>('overview')
  const [previousScreen, setPreviousScreen] = useState<Screen>('machines')
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null)
  const [selectedAsset, setSelectedAsset] = useState<AssetDetail | null>(null)
  const [data, setData] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [theme, setTheme] = useState<Theme>('light')
  const [riskFilter, setRiskFilter] = useState<'all' | 'high' | 'medium' | 'low'>('all')
  const [search, setSearch] = useState('')
  const [simulatedText, setSimulatedText] = useState('')
  const [actionMessage, setActionMessage] = useState('')
  const [actionError, setActionError] = useState('')
  const [busyAction, setBusyAction] = useState('')
  const selectedAssetIdRef = useRef(selectedAssetId)
  const detailRequestId = useRef(0)

  const refreshData = useCallback(async () => {
    try {
      const currentAssetId = selectedAssetIdRef.current
      const [portfolio, savings, assets, workOrders, messages, calls, detail] = await Promise.all([
        getPortfolio(),
        getSavings(),
        getAssets(),
        getWorkOrders(),
        getMessages(),
        getCalls(),
        currentAssetId ? getAsset(currentAssetId) : Promise.resolve(null),
      ])
      setData({ portfolio, savings, assets, workOrders, messages, calls })
      if (currentAssetId && currentAssetId === selectedAssetIdRef.current) {
        setSelectedAsset(detail)
      }
      setError(currentAssetId && !detail ? `No detail data found for machine ${currentAssetId}.` : '')
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Dashboard data could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const initialLoad = window.setTimeout(() => void refreshData(), 0)
    const timer = window.setInterval(() => void refreshData(), 15_000)
    return () => {
      window.clearTimeout(initialLoad)
      window.clearInterval(timer)
    }
  }, [refreshData])

  const assets = data?.assets ?? EMPTY_ASSETS
  const sortedAssets = useMemo(
    () => [...assets].sort((a, b) => (b.prob ?? -1) - (a.prob ?? -1)),
    [assets],
  )
  const filteredAssets = useMemo(() => {
    const query = search.trim().toLowerCase()
    return sortedAssets.filter((asset) => {
      const matchesTier = riskFilter === 'all' || asset.tier === riskFilter
      const haystack = [
        machineName(asset),
        asset.id,
        asset.site_name,
        asset.city,
        asset.asset_type,
      ].join(' ').toLowerCase()
      return matchesTier && (!query || haystack.includes(query))
    })
  }, [riskFilter, search, sortedAssets])
  const attentionAssets = sortedAssets.filter((asset) => asset.tier === 'high' || asset.tier === 'medium')
  const activities = useMemo<ActivityItem[]>(() => [
    ...(data?.messages ?? []).map((item) => ({ ...item, kind: 'message' as const })),
    ...(data?.calls ?? []).map((item) => ({ ...item, kind: 'call' as const })),
  ].sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime()), [data])

  const openMachine = (id: string, from: Screen) => {
    const requestId = ++detailRequestId.current
    selectedAssetIdRef.current = id
    setPreviousScreen(from)
    setSelectedAssetId(id)
    setSelectedAsset(null)
    setScreen('detail')
    setError('')
    getAsset(id)
      .then((detail) => {
        if (requestId !== detailRequestId.current) return
        setSelectedAsset(detail)
        if (!detail) setError(`No detail data found for machine ${id}.`)
      })
      .catch((loadError: unknown) => {
        if (requestId !== detailRequestId.current) return
        setError(loadError instanceof Error ? loadError.message : 'Machine details could not be loaded.')
      })
  }

  const changeWorkOrder = async (id: string, action: 'approve' | 'hold') => {
    setBusyAction(id)
    setActionError('')
    setActionMessage('')
    try {
      const result = action === 'approve' ? await approveWorkOrder(id) : await holdWorkOrder(id)
      if (!result) throw new Error(`Work order ${id} could not be ${action === 'approve' ? 'approved' : 'put on hold'}.`)
      setActionMessage(`${id} ${action === 'approve' ? 'approved' : 'put on hold'}.`)
      await refreshData()
    } catch (actionFailure) {
      setActionError(actionFailure instanceof Error ? actionFailure.message : 'Work order update failed.')
    } finally {
      setBusyAction('')
    }
  }

  const submitSweep = async () => {
    setBusyAction('sweep')
    setActionError('')
    setActionMessage('')
    try {
      const result = await runSweep()
      setActionMessage(`Sweep complete: ${result.summary}`)
      await refreshData()
    } catch (actionFailure) {
      setActionError(actionFailure instanceof Error ? actionFailure.message : 'The sweep could not be started.')
    } finally {
      setBusyAction('')
    }
  }

  const submitSimulatedText = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!simulatedText.trim()) return
    setBusyAction('text')
    setActionError('')
    setActionMessage('')
    try {
      await simulateText(simulatedText.trim())
      setActionMessage('Manager text received. The agent is handling it; the dashboard will update shortly.')
      setSimulatedText('')
      await refreshData()
    } catch (actionFailure) {
      setActionError(actionFailure instanceof Error ? actionFailure.message : 'The text could not be simulated.')
    } finally {
      setBusyAction('')
    }
  }

  const pageTitle = screen === 'overview'
    ? 'Overview'
    : screen === 'machines'
      ? 'Machines'
      : screen === 'work-orders'
        ? 'Work orders'
        : screen === 'activity'
          ? 'Activity'
          : screen === 'demo'
            ? 'Demo controls'
            : machineName(assets.find((asset) => asset.id === selectedAssetId) || { id: selectedAssetId || '', asset_type: '' })

  return (
    <div className="app-shell" data-theme={theme}>
      <aside className="sidebar">
        <a className="sidebar-brand" href="#" onClick={(event) => { event.preventDefault(); setScreen('overview') }}>
          <img src={penguinLogo} alt="" />
          <span>Penguin<span>RE</span></span>
        </a>
        <div className="workspace-switcher">
          <span className="workspace-avatar">P</span>
          <span><strong>PenguinRE Demo</strong><small>Operations workspace</small></span>
          <span className="workspace-chevron" aria-hidden="true">⌄</span>
        </div>
        <p className="sidebar-label">WORKSPACE</p>
        <nav className="sidebar-nav" aria-label="Main navigation">
          {NAV_ITEMS.map((item) => {
            const active = screen === item.id || (screen === 'detail' && item.id === 'machines')
            return (
              <button
                type="button"
                key={item.id}
                className={`sidebar-link ${active ? 'active' : ''}`}
                aria-current={active ? 'page' : undefined}
                onClick={() => {
                  setScreen(item.id)
                  setActionError('')
                  setActionMessage('')
                }}
              >
                <Icon>{item.icon}</Icon>
                <span>{item.label}</span>
                {item.id === 'work-orders' && (data?.portfolio.work_orders_awaiting_approval ?? 0) > 0 && (
                  <span className="nav-count">{data?.portfolio.work_orders_awaiting_approval}</span>
                )}
              </button>
            )
          })}
        </nav>
        <div className="sidebar-bottom">
          <div className="monitoring-status"><span />Monitoring active</div>
          <button className="theme-button" type="button" onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>
            <Icon>{theme === 'light' ? '◐' : '☼'}</Icon>
            <span>{theme === 'light' ? 'Dark mode' : 'Light mode'}</span>
          </button>
          <div className="sidebar-user">
            <span className="user-avatar">O</span>
            <span><strong>Operations team</strong><small>Facilities manager</small></span>
          </div>
          <p className="sidebar-footer">PenguinRE · Demo data</p>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumb"><span>Workspace</span><span>/</span><strong>{pageTitle}</strong></div>
          <div className="topbar-right">
            <span className="refresh-indicator"><span /> Updated every 15 seconds</span>
            <button type="button" className="refresh-button" onClick={() => void refreshData()} aria-label="Refresh dashboard">
              <span aria-hidden="true">↻</span>
            </button>
            <span className="topbar-avatar">O</span>
          </div>
        </header>

        <div className="page-content">
          {error && <div className="error-banner" role="alert"><strong>Data unavailable</strong><span>{error}</span><button type="button" onClick={() => void refreshData()}>Retry</button></div>}
          {actionMessage && <div className="success-banner" role="status">{actionMessage}<button type="button" aria-label="Dismiss message" onClick={() => setActionMessage('')}>×</button></div>}
          {actionError && <div className="error-banner" role="alert"><strong>Action failed</strong><span>{actionError}</span><button type="button" onClick={() => setActionError('')}>Dismiss</button></div>}
          {loading && !data ? (
            <EmptyState>Loading dashboard data…</EmptyState>
          ) : (
            <>
              {screen === 'overview' && data && (
                <OverviewScreen
                  data={data}
                  attentionAssets={attentionAssets}
                  onOpenMachine={(id) => openMachine(id, 'overview')}
                  onViewMachines={() => setScreen('machines')}
                />
              )}
              {screen === 'machines' && data && (
                <MachinesScreen
                  assets={filteredAssets}
                  total={assets.length}
                  riskFilter={riskFilter}
                  search={search}
                  onFilter={setRiskFilter}
                  onSearch={setSearch}
                  onOpenMachine={(id) => openMachine(id, 'machines')}
                />
              )}
              {screen === 'detail' && selectedAsset && (
                <MachineDetailScreen
                  detail={selectedAsset}
                  asset={assets.find((item) => item.id === selectedAssetId)}
                  workOrders={data?.workOrders ?? []}
                  onBack={() => setScreen(previousScreen)}
                  onOpenMachine={(id) => openMachine(id, 'detail')}
                />
              )}
              {screen === 'detail' && !selectedAsset && !error && (
                <EmptyState>Loading machine details…</EmptyState>
              )}
              {screen === 'work-orders' && data && (
                <WorkOrdersScreen
                  workOrders={data.workOrders}
                  busyAction={busyAction}
                  onApprove={(id) => void changeWorkOrder(id, 'approve')}
                  onHold={(id) => void changeWorkOrder(id, 'hold')}
                  onOpenMachine={(id) => openMachine(id, 'work-orders')}
                />
              )}
              {screen === 'activity' && <ActivityScreen items={activities} />}
              {screen === 'demo' && (
                <DemoScreen
                  lastRun={data?.portfolio.last_run ?? null}
                  busyAction={busyAction}
                  simulatedText={simulatedText}
                  onTextChange={setSimulatedText}
                  onRunSweep={() => void submitSweep()}
                  onSimulateText={(event) => void submitSimulatedText(event)}
                />
              )}
            </>
          )}
        </div>
      </main>
    </div>
  )
}

function OverviewScreen({
  data,
  attentionAssets,
  onOpenMachine,
  onViewMachines,
}: {
  data: DashboardData
  attentionAssets: Asset[]
  onOpenMachine: (id: string) => void
  onViewMachines: () => void
}) {
  const { portfolio, savings } = data
  return (
    <>
      <PageHeader title="Good morning" description="Here’s the latest across your equipment portfolio." actions={<span className="today-chip">{longDate(TODAY)}</span>} />
      <section className="stats-grid" aria-label="Portfolio summary">
        <StatCard label="Sites" value={portfolio.sites.toLocaleString()} note="Active commercial locations" icon="⌂" />
        <StatCard label="Square feet" value={portfolio.square_feet.toLocaleString()} note="Across managed sites" icon="▱" />
        <StatCard label="Machines monitored" value={portfolio.machines.toLocaleString()} note="Across all active locations" icon="▦" />
        <StatCard label="High risk" value={portfolio.high_risk_count.toLocaleString()} note="Requires immediate attention" icon="▲" />
        <StatCard label="Medium risk" value={portfolio.medium_risk_count.toLocaleString()} note="Keep a close watch" icon="!" />
        <StatCard label="Awaiting approval" value={portfolio.work_orders_awaiting_approval.toLocaleString()} note="Work orders need a decision" icon="☷" />
        <StatCard label="Texts sent today" value={portfolio.texts_sent_today.toLocaleString()} note="Regional manager updates" icon="↗" />
      </section>

      <section className="overview-lower">
        <article className="panel savings-panel">
          <div className="section-heading">
            <div><p className="overline">PORTFOLIO IMPACT</p><h2>Money protected by acting early</h2></div>
            <span className="savings-icon" aria-hidden="true">$</span>
          </div>
          <p className="savings-amount">{money(savings.savings_secured_usd)}</p>
          <p className="savings-caption">Saved by approved work orders</p>
          <div className="savings-details">
            <div><span className="detail-dot pending-dot" /><span><strong>{money(savings.savings_pending_usd)}</strong><small>More if pending work orders are approved</small></span></div>
            <div><span className="detail-dot exposure-dot" /><span><strong>{money(savings.exposure_usd)}</strong><small>Expected emergency cost in the next 30 days if nothing is done</small></span></div>
          </div>
          <div className="savings-footnote">Based on estimated preventive and emergency repair costs.</div>
        </article>

        <article className="panel attention-panel">
          <div className="section-heading">
            <div><p className="overline">PRIORITIZED QUEUE</p><h2>Needs attention</h2></div>
            <button type="button" className="text-button" onClick={onViewMachines}>View machines <span>→</span></button>
          </div>
          {attentionAssets.length === 0 ? (
            <EmptyState>No high- or medium-risk machines need attention.</EmptyState>
          ) : (
            <div className="attention-list">
              {attentionAssets.slice(0, 5).map((asset) => (
                <button className="attention-row" type="button" key={asset.id} onClick={() => onOpenMachine(asset.id)}>
                  <span className={`attention-mark ${asset.tier || 'none'}`}>{asset.tier === 'high' ? '▲' : '!'}</span>
                  <span className="attention-main">
                    <strong>{machineName(asset)} <small>· {asset.site_name}</small></strong>
                    <span>{asset.main_reason}</span>
                    <small>{asset.open_work_order ? `Work order ${asset.open_work_order}` : 'No open work order'}</small>
                  </span>
                  <span className="attention-risk">{percent(asset.prob)}<small>30-day risk</small></span>
                  <span className="row-chevron" aria-hidden="true">›</span>
                </button>
              ))}
            </div>
          )}
        </article>
      </section>

      <section className="panel last-sweep-panel">
        <div className="sweep-status-icon" aria-hidden="true">✦</div>
        <div className="sweep-summary">
          <span>Last agent sweep</span>
          <strong>{portfolio.last_run?.summary || 'No agent sweep has run yet.'}</strong>
        </div>
        <span className="sweep-time">{portfolio.last_run ? relativeTime(portfolio.last_run.finished_at) : 'Not run'}</span>
      </section>
    </>
  )
}

function MachinesScreen({
  assets,
  total,
  riskFilter,
  search,
  onFilter,
  onSearch,
  onOpenMachine,
}: {
  assets: Asset[]
  total: number
  riskFilter: 'all' | 'high' | 'medium' | 'low'
  search: string
  onFilter: (filter: 'all' | 'high' | 'medium' | 'low') => void
  onSearch: (search: string) => void
  onOpenMachine: (id: string) => void
}) {
  return (
    <>
      <PageHeader title="Machines" description="Monitor machine health across every site." actions={<span className="count-chip">{total} machines</span>} />
      <section className="panel table-panel">
        <div className="table-toolbar">
          <div className="filter-chips" aria-label="Filter by risk">
            {(['all', 'high', 'medium', 'low'] as const).map((filter) => (
              <button key={filter} type="button" className={`filter-chip ${riskFilter === filter ? 'selected' : ''}`} onClick={() => onFilter(filter)}>
                {filter === 'all' ? 'All' : tierLabel(filter)}
              </button>
            ))}
          </div>
          <label className="search-control">
            <span aria-hidden="true">⌕</span>
            <input type="search" value={search} onChange={(event) => onSearch(event.target.value)} placeholder="Search machines or sites" />
          </label>
        </div>
        {assets.length === 0 ? (
          <EmptyState>No machines match these filters.</EmptyState>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr><th>Machine</th><th>Site</th><th>City</th><th>Risk</th><th>Level</th><th>Main reason</th><th>Work order</th><th>Expected savings</th></tr></thead>
              <tbody>
                {assets.map((asset) => (
                  <tr key={asset.id} className="clickable-row" onClick={() => onOpenMachine(asset.id)}>
                    <td><button className="table-link" type="button" onClick={(event) => { event.stopPropagation(); onOpenMachine(asset.id) }}>{machineName(asset)}<small>{asset.id}</small></button></td>
                    <td>{asset.site_name}</td>
                    <td>{asset.city}</td>
                    <td className="risk-percent">{percent(asset.prob)}</td>
                    <td><RiskBadge tier={asset.tier} /></td>
                    <td className="reason-cell">{asset.main_reason}</td>
                    <td>{asset.open_work_order ? <span className="work-order-link">{asset.open_work_order}</span> : <span className="muted">—</span>}</td>
                    <td className="money-cell">{money(asset.expected_savings_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="table-footer">Showing {assets.length} of {total} machines <span>Sorted by highest failure risk</span></div>
      </section>
    </>
  )
}

function RiskChart({ detail }: { detail: AssetDetail }) {
  const { risk_history: history } = detail
  const width = 720
  const height = 220
  const left = 44
  const right = 18
  const top = 14
  const bottom = 34
  const plotWidth = width - left - right
  const plotHeight = height - top - bottom
  const y = (prob: number) => top + (1 - Math.min(Math.max(prob, 0), 0.5) / 0.5) * plotHeight
  const x = (index: number) => left + (history.length <= 1 ? plotWidth / 2 : (index / (history.length - 1)) * plotWidth)
  const line = history.map((point, index) => `${x(index)},${y(point.prob)}`).join(' ')
  const highY = y(0.2)
  const mediumY = y(0.08)

  if (history.length === 0) return <EmptyState>No risk history available for this machine.</EmptyState>

  return (
    <svg className="risk-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Machine failure risk over the last 30 days">
      {[0, 0.1, 0.2, 0.3, 0.4, 0.5].map((tick) => (
        <g key={tick}>
          <line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} className="chart-gridline" />
          <text x={left - 8} y={y(tick) + 4} textAnchor="end" className="chart-axis-label">{Math.round(tick * 100)}%</text>
        </g>
      ))}
      <line x1={left} x2={width - right} y1={highY} y2={highY} className="chart-threshold high-threshold" />
      <text x={width - right - 4} y={highY - 5} textAnchor="end" className="chart-threshold-label high-label">High 20%</text>
      <line x1={left} x2={width - right} y1={mediumY} y2={mediumY} className="chart-threshold medium-threshold" />
      <text x={width - right - 4} y={mediumY - 5} textAnchor="end" className="chart-threshold-label medium-label">Medium 8%</text>
      <polyline points={line} className="chart-line" />
      {history.map((point, index) => <circle key={point.date} cx={x(index)} cy={y(point.prob)} r="3.5" className="chart-point"><title>{shortDate(point.date)}: {percent(point.prob)}</title></circle>)}
      <text x={left} y={height - 8} className="chart-axis-label">{shortDate(history[0].date)}</text>
      <text x={width - right} y={height - 8} textAnchor="end" className="chart-axis-label">{shortDate(history[history.length - 1].date)}</text>
    </svg>
  )
}

function MachineDetailScreen({
  detail,
  asset,
  workOrders,
  onBack,
  onOpenMachine,
}: {
  detail: AssetDetail
  asset?: Asset
  workOrders: WorkOrder[]
  onBack: () => void
  onOpenMachine: (id: string) => void
}) {
  const { risk_now: risk, cost_comparison: costs } = detail
  const assetWorkOrders = workOrders.filter((order) => order.asset_id === detail.asset.id)
  return (
    <>
      <button type="button" className="back-button" onClick={onBack}>← Back</button>
      <PageHeader title={`${machineName(detail.asset)} · ${detail.asset.site_name}`} description={`${detail.asset.city} · ${detail.asset.site_type} · ${detail.asset.square_feet.toLocaleString()} sq ft`} actions={<RiskBadge tier={risk.tier} />} />
      <section className="detail-hero panel">
        <div className="hero-risk">
          <span className="overline">30-DAY FAILURE RISK</span>
          <strong>{percent(risk.prob)}</strong>
          <RiskBadge tier={risk.tier} />
        </div>
        <div className="hero-divider" />
        <div className="asset-facts">
          <div><span>Machine</span><strong>{detail.asset.id}</strong></div>
          <div><span>Installed</span><strong>{shortDate(detail.asset.install_date)}</strong></div>
          <div><span>Age</span><strong>{risk.age_years.toFixed(1)} years</strong></div>
          <div><span>Hours</span><strong>{detail.asset.open_hours}</strong></div>
        </div>
        <div className="multiplier-chip">{risk.multiplier.toFixed(1)}x risk</div>
      </section>

      <section className="detail-grid">
        <article className="panel detail-panel chart-panel">
          <div className="section-heading"><div><p className="overline">30-DAY TREND</p><h2>Failure risk history</h2></div><span className="current-risk">{percent(risk.prob)} today</span></div>
          <RiskChart detail={detail} />
        </article>

        <article className="panel detail-panel why-panel">
          <div className="section-heading"><div><p className="overline">RISK DRIVERS</p><h2>Why this machine</h2></div></div>
          {risk.drivers.length === 0 ? <EmptyState>No risk drivers reported.</EmptyState> : (
            <ul className="driver-list">
              {risk.drivers.map((driver, index) => (
                <li key={`${driver.factor}-${index}`}><span className="driver-mark">↗</span><span><strong>{driver.factor === 'age' ? driver.detail || 'Equipment age' : DRIVER_LABELS[driver.factor] || driver.factor.replaceAll('_', ' ')}</strong><small>{driver.factor !== 'age' && driver.detail ? driver.detail : driver.value !== undefined ? `${typeof driver.value === 'boolean' ? (driver.value ? 'Yes' : 'No') : driver.value} recorded` : 'Contributing to current score'}</small></span></li>
              ))}
            </ul>
          )}
        </article>

        <article className="panel detail-panel forecast-panel">
          <div className="section-heading"><div><p className="overline">WEATHER</p><h2>7-day forecast</h2></div><span className="muted">Daily high</span></div>
          {detail.forecast.length === 0 ? <EmptyState>No forecast data available.</EmptyState> : (
            <div className="forecast-list">
              {detail.forecast.map((day) => (
                <div className="forecast-day" key={day.date}><span>{shortDate(day.date)}</span><span className="weather-symbol" aria-hidden="true">{day.max_f >= 90 ? '☀' : '◒'}</span><strong>{day.max_f}°F</strong></div>
              ))}
            </div>
          )}
        </article>

        <article className="panel detail-panel cost-panel">
          <div className="section-heading"><div><p className="overline">COST COMPARISON</p><h2>Acting early protects your budget</h2></div><span className="source-label">Source: {costs.cost_source}</span></div>
          <div className="cost-comparison">
            <div><span>Preventive repair</span><strong>{money(costs.preventive_cost_usd)}</strong></div>
            <span className="cost-vs">vs</span>
            <div><span>Emergency failure</span><strong className="emergency-cost">{money(costs.emergency_cost_usd)}</strong></div>
          </div>
          <div className="breakdown-row"><span>Repair</span><strong>{money(costs.emergency_breakdown.repair)}</strong></div>
          <div className="breakdown-row"><span>Lost revenue</span><strong>{money(costs.emergency_breakdown.lost_revenue)}</strong></div>
          <div className="breakdown-row"><span>Spoiled stock</span><strong>{money(costs.emergency_breakdown.spoiled_stock)}</strong></div>
          <div className="expected-savings"><span>Expected savings</span><strong>{money(costs.expected_savings_usd)}</strong></div>
          <div className={`worth-acting ${costs.worth_acting_now ? 'yes' : 'no'}`}>{costs.worth_acting_now ? '✓ Worth acting now' : '○ Monitor for now'}</div>
        </article>

        <article className="panel detail-panel history-panel">
          <div className="section-heading"><div><p className="overline">SERVICE RECORD</p><h2>Repair history</h2></div></div>
          {detail.work_order_history.length === 0 ? <EmptyState>No repair history on file.</EmptyState> : (
            <div className="timeline-list">
              {detail.work_order_history.map((record, index) => (
                <div className="timeline-row" key={`${record.date}-${index}`}><span className="timeline-dot" /><div><strong>{record.issue}</strong><span>{record.kind} · {shortDate(record.date)}</span><p>{record.note}</p></div></div>
              ))}
            </div>
          )}
        </article>

        <article className="panel detail-panel asset-orders-panel">
          <div className="section-heading"><div><p className="overline">FOLLOW-UP</p><h2>This machine’s work orders</h2></div></div>
          {assetWorkOrders.length === 0 ? <EmptyState>No work orders for this machine.</EmptyState> : (
            <div className="compact-orders">
              {assetWorkOrders.map((order) => <div key={order.id}><strong>{order.id}</strong><span>{order.likely_issue}</span><RiskBadge tier={order.tier} /><small>{tierLabel(order.tier)} · {shortDate(order.do_by)} · {order.status.replaceAll('_', ' ')}</small></div>)}
            </div>
          )}
          {asset && asset.id !== detail.asset.id && <button className="text-button" type="button" onClick={() => onOpenMachine(asset.id)}>Open machine</button>}
        </article>
      </section>
    </>
  )
}

function WorkOrdersScreen({
  workOrders,
  busyAction,
  onApprove,
  onHold,
  onOpenMachine,
}: {
  workOrders: WorkOrder[]
  busyAction: string
  onApprove: (id: string) => void
  onHold: (id: string) => void
  onOpenMachine: (id: string) => void
}) {
  const pendingCount = workOrders.filter((order) => order.status === 'pending_approval').length
  return (
    <>
      <PageHeader title="Work orders" description="Review agent recommendations and track planned maintenance." actions={<span className="count-chip">{pendingCount} awaiting approval</span>} />
      <section className="panel table-panel">
        {workOrders.length === 0 ? <EmptyState>No work orders have been created.</EmptyState> : (
          <div className="table-scroll">
            <table className="data-table work-orders-table">
              <thead><tr><th>Work order</th><th>Machine / site</th><th>Risk</th><th>Urgency</th><th>Likely issue / tasks</th><th>Do by</th><th>Status</th><th>Expected savings</th><th>Actions</th></tr></thead>
              <tbody>
                {workOrders.map((order) => (
                  <tr key={order.id}>
                    <td><strong>{order.id}</strong></td>
                    <td><button className="table-link" type="button" onClick={() => onOpenMachine(order.asset_id)}>{order.machine_name}<small>{order.site} · {order.city}</small></button></td>
                    <td><RiskBadge tier={order.tier} /></td>
                    <td><span className={`urgency urgency-${order.urgency}`}>{order.urgency === '48h' ? 'Within 48 hours' : order.urgency === 'this_week' ? 'This week' : 'Today'}</span></td>
                    <td className="order-tasks"><strong>{order.likely_issue}</strong><small>{order.tasks.join(' · ')}</small></td>
                    <td>{shortDate(order.do_by)}</td>
                    <td><span className={`status-pill status-${order.status}`}>{order.status.replaceAll('_', ' ')}</span></td>
                    <td className="money-cell">{money(order.expected_savings_usd)}</td>
                    <td>
                      {order.status === 'pending_approval' ? (
                        <div className="row-actions">
                          <button type="button" className="approve-button" disabled={busyAction === order.id} onClick={() => onApprove(order.id)}>{busyAction === order.id ? '…' : 'Approve'}</button>
                          <button type="button" className="hold-button" disabled={busyAction === order.id} onClick={() => onHold(order.id)}>Hold</button>
                        </div>
                      ) : <span className="muted">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="table-footer">{workOrders.length} work orders <span>Approvals update simulated savings</span></div>
      </section>
    </>
  )
}

function ActivityScreen({ items }: { items: ActivityItem[] }) {
  return (
    <>
      <PageHeader title="Activity" description="Messages to regional managers and calls to service workers." />
      <section className="panel activity-panel">
        {items.length === 0 ? <EmptyState>No messages or calls have been logged.</EmptyState> : (
          <div className="activity-feed">
            {items.map((item) => (
              <article className="activity-item" key={`${item.kind}-${item.id}`}>
                <span className={`activity-kind ${item.kind}`} aria-hidden="true">{item.kind === 'message' ? '↗' : '☎'}</span>
                <div className="activity-content">
                  <div className="activity-title"><strong>{item.kind === 'message' ? 'Text message' : 'Service call'}</strong><span>To {item.recipient}</span></div>
                  <p>{item.kind === 'message' ? item.text : item.script}</p>
                  {item.dry_run && <span className="dry-run-label">Logged only (sending is off)</span>}
                </div>
                <time dateTime={item.time}>{relativeTime(item.time)}</time>
              </article>
            ))}
          </div>
        )}
      </section>
    </>
  )
}

function DemoScreen({
  lastRun,
  busyAction,
  simulatedText,
  onTextChange,
  onRunSweep,
  onSimulateText,
}: {
  lastRun: Portfolio['last_run']
  busyAction: string
  simulatedText: string
  onTextChange: (value: string) => void
  onRunSweep: () => void
  onSimulateText: (event: FormEvent<HTMLFormElement>) => void
}) {
  return (
    <>
      <PageHeader title="Demo controls" description="Try the agent workflow with safe, local mock data." />
      <section className="demo-grid">
        <article className="panel demo-card">
          <span className="demo-icon" aria-hidden="true">✦</span>
          <p className="overline">AGENT</p>
          <h2>Run a portfolio sweep</h2>
          <p>Re-check machine risk, summarize findings, and update the latest sweep record. This demo does not contact anyone.</p>
          <button type="button" className="primary-button" disabled={busyAction === 'sweep'} onClick={onRunSweep}>{busyAction === 'sweep' ? 'Running sweep…' : 'Run sweep now'} <span>→</span></button>
          <div className="demo-last-run"><span>Last run</span><strong>{lastRun ? `${relativeTime(lastRun.finished_at)} · ${lastRun.summary}` : 'No sweep run yet.'}</strong></div>
        </article>

        <article className="panel demo-card">
          <span className="demo-icon" aria-hidden="true">↗</span>
          <p className="overline">SIMULATOR</p>
          <h2>Simulate a manager text</h2>
          <p>Test an incoming field report or an approval reply. Try “Katy clinic has no hot water again” or “APPROVE WO-0003”.</p>
          <form className="simulate-form" onSubmit={onSimulateText}>
            <label htmlFor="simulate-message">Incoming message</label>
            <textarea id="simulate-message" rows={4} value={simulatedText} onChange={(event) => onTextChange(event.target.value)} placeholder="Type a message from a site or regional manager…" />
            <button type="submit" className="primary-button" disabled={!simulatedText.trim() || busyAction === 'text'}>{busyAction === 'text' ? 'Simulating…' : 'Simulate text'} <span>→</span></button>
          </form>
        </article>
      </section>
      <p className="demo-notice">All actions in this demo update local mock data only. No texts or phone calls are sent.</p>
    </>
  )
}

export default App

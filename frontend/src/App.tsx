import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode, type RefObject } from 'react'
import {
  decisionTitle,
  getAsset,
  getAssets,
  getCalls,
  getMessages,
  getPortfolio,
  getSavings,
  getWorkOrders,
  runPipeline,
  runSampleData,
  setDecisionStatus,
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

type Screen = 'overview' | 'machines' | 'detail' | 'work-orders' | 'activity' | 'data-notes'
type Theme = 'light' | 'dark'

interface DashboardData {
  portfolio: Portfolio
  savings: Savings
  assets: Asset[]
  workOrders: WorkOrder[]
  messages: Message[]
  calls: Call[]
}

interface Profile {
  company: string
  name: string
  email: string
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
  refrigerant_recharges: 'Refrigerant recharges (likely leak)',
  repair_frequency: 'Repair calls, last 90 days',
  pm_overdue: 'Preventive maintenance overdue',
  heat_forecast: 'Hot days in the forecast',
  no_history_cap: 'No work-order history on file',
}

const NAV_ITEMS: Array<{ id: Screen; label: string; icon: string }> = [
  { id: 'overview', label: 'Overview', icon: '⌂' },
  { id: 'machines', label: 'Equipment', icon: '▦' },
  { id: 'work-orders', label: 'Service History', icon: '☷' },
  { id: 'activity', label: 'Activity', icon: '◷' },
  { id: 'data-notes', label: 'Data notes', icon: 'ⓘ' },
]
const TODAY = new Date()
const EMPTY_ASSETS: Asset[] = []

const CURRENCY = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
})

function money(amount: number | null): string {
  return amount === null ? 'Not provided' : CURRENCY.format(amount)
}

function fileSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function indexText(safetyIndex: number | null | undefined): string {
  return safetyIndex === null || safetyIndex === undefined ? '—' : String(safetyIndex)
}

function bandText(band: string | null | undefined): string {
  if (!band || band === 'unknown') return 'Needs data'
  return `${band[0].toUpperCase()}${band.slice(1)}`
}

function relativeTime(timestamp: string): string {
  const date = new Date(timestamp)
  if (Number.isNaN(date.getTime())) return 'Time unavailable'
  const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60_000))
  if (minutes < 60) return minutes === 0 ? 'Just now' : `${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  return hours < 24 ? `${hours} hr ago` : `${Math.floor(hours / 24)} days ago`
}

function shortDate(date: string | null): string {
  if (!date) return 'Not provided'
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
  const [profile, setProfile] = useState<Profile | null>(null)
  const [loginStep, setLoginStep] = useState<'company' | 'contact'>('company')
  const [loginCompany, setLoginCompany] = useState('')
  const [loginName, setLoginName] = useState('')
  const [loginEmail, setLoginEmail] = useState('')
  const [loginError, setLoginError] = useState('')
  const [accountMenuOpen, setAccountMenuOpen] = useState(false)
  const [importFiles, setImportFiles] = useState<File[]>([])
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
  const selectedAssetIdRef = useRef(selectedAssetId)
  const detailRequestId = useRef(0)
  const importInputRef = useRef<HTMLInputElement>(null)

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
    if (!profile) return undefined
    const initialLoad = window.setTimeout(() => void refreshData(), 0)
    return () => window.clearTimeout(initialLoad)
  }, [profile, refreshData])

  const assets = data?.assets ?? EMPTY_ASSETS
  const sortedAssets = useMemo(
    () => [...assets].sort((a, b) => (a.safety_index ?? 1000) - (b.safety_index ?? 1000)),
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

  const continueLogin = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setLoginError('')
    if (loginStep === 'company') {
      if (!loginCompany.trim()) {
        setLoginError('Enter your company name to continue.')
        return
      }
      setLoginStep('contact')
      return
    }
    if (!loginName.trim() || !loginEmail.trim()) {
      setLoginError('Enter your name and company email to continue.')
      return
    }
    setProfile({
      company: loginCompany.trim(),
      name: loginName.trim(),
      email: loginEmail.trim(),
    })
  }

  const skipLogin = () => {
    setProfile({ company: 'Demo Company', name: 'Demo User', email: 'demo@penguinre.com' })
  }

  const logOut = () => {
    setAccountMenuOpen(false)
    setProfile(null)
    setLoginStep('company')
    setLoginCompany('')
    setLoginName('')
    setLoginEmail('')
    setLoginError('')
    setScreen('overview')
    setSelectedAssetId(null)
    selectedAssetIdRef.current = null
    setSelectedAsset(null)
  }

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

  const pageTitle = screen === 'overview'
    ? 'Overview'
    : screen === 'machines'
      ? 'Equipment'
      : screen === 'work-orders'
      ? 'Service history'
        : screen === 'activity'
          ? 'Activity'
          : screen === 'data-notes'
            ? 'Data notes'
            : machineName(assets.find((asset) => asset.id === selectedAssetId) || { id: selectedAssetId || '', asset_type: '' })

  if (!profile) {
    return (
      <main className="login-page">
        <section className="login-card" aria-labelledby="login-title">
          <div className="login-brand">
            <img className="login-logo" src={penguinLogo} alt="PenguinRE logo" />
          </div>
          <div className="login-intro">
            <p className="login-eyebrow">Preventing operational failures for the 90% that get ignored</p>
            <h1 id="login-title">
              {loginStep === 'company'
                ? <>Welcome to Penguin<span className="login-title-accent">RE</span></>
                : 'A little about you'}
            </h1>
            <p>{loginStep === 'company' ? 'Connect to your company’s workspace to access shared data' : `Set up your profile for ${loginCompany.trim()}.`}</p>
          </div>
          <div className="login-progress" aria-label={`Step ${loginStep === 'company' ? '1' : '2'} of 2`}>
            <span className="complete" />
            <span className={loginStep === 'contact' ? 'complete' : ''} />
          </div>
          <p className="login-step-label">STEP {loginStep === 'company' ? '1' : '2'} OF 2</p>
          <form className="login-form" onSubmit={continueLogin}>
            {loginStep === 'company' ? (
              <label>
                <span>Company name</span>
                <input
                  autoComplete="organization"
                  autoFocus
                  placeholder="e.g. CBRE"
                  value={loginCompany}
                  onChange={(event) => setLoginCompany(event.target.value)}
                  required
                />
              </label>
            ) : (
              <>
                <label>
                  <span>Your name</span>
                  <input
                    autoComplete="name"
                    autoFocus
                    placeholder="e.g. Jordan Lee"
                    value={loginName}
                    onChange={(event) => setLoginName(event.target.value)}
                    required
                  />
                </label>
                <label>
                  <span>Company email</span>
                  <input
                    autoComplete="email"
                    type="email"
                    placeholder="you@company.com"
                    value={loginEmail}
                    onChange={(event) => setLoginEmail(event.target.value)}
                    required
                  />
                </label>
              </>
            )}
            {loginError && <p className="login-error" role="alert">{loginError}</p>}
            <div className="login-actions">
              {loginStep === 'contact' && (
                <button className="login-back-button" type="button" onClick={() => { setLoginError(''); setLoginStep('company') }}>
                  Back
                </button>
              )}
              <button className="login-continue-button" type="submit">
                {loginStep === 'company' ? 'Continue' : 'Open dashboard'} <span aria-hidden="true">→</span>
              </button>
            </div>
            <button className="login-skip-button" type="button" onClick={skipLogin}>
              Skip for demo
            </button>
          </form>
        </section>
      </main>
    )
  }

  return (
    <div className="app-shell" data-theme={theme}>
      <aside className="sidebar">
        <a className="sidebar-brand" href="#" onClick={(event) => { event.preventDefault(); setScreen('overview') }}>
          <img src={penguinLogo} alt="" />
          <span>Penguin<span>RE</span></span>
        </a>
        <div className="workspace-switcher">
          <span className="workspace-avatar">P</span>
          <span><strong>{profile.company}</strong><small>Imported portfolio</small></span>
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
                }}
              >
                <Icon>{item.icon}</Icon>
                <span>{item.label}</span>
              </button>
            )
          })}
        </nav>
        <div className="sidebar-bottom">
          <div className="monitoring-status"><span />Live data · SiteSignal API</div>
          <button className="theme-button" type="button" onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>
            <Icon>{theme === 'light' ? '◐' : '☼'}</Icon>
            <span>{theme === 'light' ? 'Dark mode' : 'Light mode'}</span>
          </button>
          <button
            className="sidebar-user"
            type="button"
            aria-expanded={accountMenuOpen}
            aria-controls="account-menu"
            onClick={() => setAccountMenuOpen((open) => !open)}
          >
            <span className="user-avatar">{profile.name.charAt(0).toUpperCase()}</span>
            <span><strong>{profile.name}</strong><small>{profile.email}</small></span>
            <span className="workspace-chevron" aria-hidden="true">{accountMenuOpen ? '⌃' : '⌄'}</span>
          </button>
          {accountMenuOpen && (
            <div className="account-menu" id="account-menu">
              <button type="button" onClick={logOut}>Log out</button>
            </div>
          )}
          <p className="sidebar-footer">PenguinRE · SiteSignal API</p>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumb"><span>Workspace</span><span>/</span><strong>{pageTitle}</strong></div>
          <div className="topbar-right">
            <span className="refresh-indicator"><span /> Database · latest record {shortDate(data?.portfolio.latest_service_date ?? null)}</span>
            <span className="topbar-avatar">{profile.name.charAt(0).toUpperCase()}</span>
          </div>
        </header>

        <div className="page-content">
          {error && <div className="error-banner" role="alert"><strong>Data unavailable</strong><span>{error}</span><button type="button" onClick={() => void refreshData()}>Retry</button></div>}
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
                  onChanged={() => void refreshData()}
                />
              )}
              {screen === 'detail' && !selectedAsset && !error && (
                <EmptyState>Loading machine details…</EmptyState>
              )}
              {screen === 'work-orders' && data && (
                <WorkOrdersScreen
                  workOrders={data.workOrders}
                  assetIds={new Set(assets.map((asset) => asset.id))}
                  onOpenMachine={(id) => openMachine(id, 'work-orders')}
                />
              )}
              {screen === 'activity' && <ActivityScreen items={activities} />}
              {screen === 'data-notes' && data && (
                <DataNotesScreen
                  portfolio={data.portfolio}
                  savings={data.savings}
                  files={importFiles}
                  inputRef={importInputRef}
                  onFilesSelected={(selectedFiles) => setImportFiles((current) => {
                    const filesByKey = new Map(current.map((file) => [`${file.name}:${file.size}:${file.lastModified}`, file]))
                    selectedFiles.forEach((file) => {
                      const key = `${file.name}:${file.size}:${file.lastModified}`
                      if (!filesByKey.has(key)) filesByKey.set(key, file)
                    })
                    return Array.from(filesByKey.values())
                  })}
                  onRemoveFile={(fileToRemove) => setImportFiles((current) => current.filter((file) => file !== fileToRemove))}
                  onClearFiles={() => setImportFiles([])}
                  onPipelineDone={() => { setImportFiles([]); void refreshData() }}
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
  const highRiskAssets = attentionAssets.filter((asset) => asset.tier === 'high').length
  const mediumRiskAssets = attentionAssets.filter((asset) => asset.tier === 'medium').length
  return (
    <>
      <PageHeader title="Portfolio Overview" description="Sites, equipment, and service history from the imported source files." actions={<span className="today-chip">{longDate(TODAY)}</span>} />
      <section className="stats-grid" aria-label="Portfolio Summary">
        <StatCard label="Sites" value={portfolio.sites.toLocaleString()} note="Imported locations" icon="⌂" />
        <StatCard label="Equipment Assets" value={portfolio.machines.toLocaleString()} note={`${portfolio.supported_assets.toLocaleString()} supported by the risk model`} icon="▦" />
        <StatCard label="Service Records" value={portfolio.service_records.toLocaleString()} note={`Latest: ${shortDate(portfolio.latest_service_date)}`} icon="☷" />
      </section>

      <section className="overview-lower">
        <article className="panel savings-panel">
          <div className="section-heading">
            <div><p className="overline">SOURCE INVOICES</p><h2>Recorded service spend</h2></div>
            <span className="savings-icon" aria-hidden="true">$</span>
          </div>
          <p className="savings-amount">{money(savings.historical_service_spend_usd)}</p>
          <p className="savings-caption">Invoice total across imported service records</p>
          <div className="savings-details">
            <div><span className="detail-dot pending-dot" /><span><strong>{savings.records_with_invoice_total.toLocaleString()}</strong><small>Records with an invoice total</small></span></div>
            <div><span className="detail-dot exposure-dot" /><span><strong>{(savings.total_service_records - savings.records_with_invoice_total).toLocaleString()}</strong><small>Records without an invoice total</small></span></div>
          </div>
          <div className="savings-footnote">Historical spend is not a savings or future-cost estimate.</div>
        </article>

        <article className="panel attention-panel">
          <div className="section-heading">
            <div><p className="overline">EQUIPMENT RISK</p><h2>Needs Attention</h2></div>
            <button type="button" className="text-button" onClick={onViewMachines}>View Equipment <span>→</span></button>
          </div>
          <p className="attention-description">
            {attentionAssets.length === 0
              ? 'No equipment is currently rated high or medium risk.'
              : `${attentionAssets.length} high- or medium-risk ${attentionAssets.length === 1 ? 'asset' : 'assets'}, sorted by Safety Index, least safe first.`}
          </p>
          {attentionAssets.length > 0 && (
            <div className="attention-counts">
              <span className="attention-count high"><span aria-hidden="true">▲</span> {highRiskAssets} high</span>
              <span className="attention-count medium"><span aria-hidden="true">!</span> {mediumRiskAssets} medium</span>
              <span className="attention-hint">Select an item for details</span>
            </div>
          )}
          {attentionAssets.length === 0 ? (
            <EmptyState>Equipment rated low risk or with insufficient data is not listed here.</EmptyState>
          ) : (
            <div className="attention-list">
              {attentionAssets.slice(0, 5).map((asset) => (
                <button className="attention-row" type="button" key={asset.id} onClick={() => onOpenMachine(asset.id)}>
                  <span className={`attention-mark ${asset.tier || 'none'}`} aria-label={`${asset.tier} risk`}>{asset.tier === 'high' ? '▲' : '!'}</span>
                  <span className="attention-main">
                    <strong>{machineName(asset)} <small>· {asset.site_name}</small></strong>
                    <span>{asset.main_reason}</span>
                    <small>Last service: {shortDate(asset.last_service_date)}</small>
                  </span>
                  <span className="attention-risk">
                    <small className={`attention-tier ${asset.tier}`}>{bandText(asset.band)}</small>
                    {indexText(asset.safety_index)}<small>Safety Index (100 = safest)</small>
                  </span>
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
          <span>Safety Index basis</span>
          <strong>Equipment age, work-order history and the 7-day National Weather Service forecast. An uncalibrated triage score, not a failure probability.</strong>
        </div>
        <span className="sweep-time">Updated each pipeline run</span>
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
      <PageHeader title="Equipment" description="Every machine with its Safety Index (100 = safest). ‘Needs data’ means the score could not be computed, not that it is safe." actions={<span className="count-chip">{total} assets</span>} />
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
              <thead><tr><th>Equipment</th><th>Site</th><th>City</th><th>Safety Index</th><th>Level</th><th>Risk basis / data status</th><th>Installed</th><th>Service spend</th></tr></thead>
              <tbody>
                {assets.map((asset) => (
                  <tr key={asset.id} className="clickable-row" onClick={() => onOpenMachine(asset.id)}>
                    <td><button className="table-link" type="button" onClick={(event) => { event.stopPropagation(); onOpenMachine(asset.id) }}>{machineName(asset)}<small>{asset.id}</small></button></td>
                    <td>{asset.site_name}</td>
                    <td>{asset.city}</td>
                    <td className="risk-percent">{indexText(asset.safety_index)}</td>
                    <td><RiskBadge tier={asset.tier} /></td>
                    <td className="reason-cell">{asset.main_reason}</td>
                    <td>{shortDate(asset.install_date)}</td>
                    <td className="money-cell">{money(asset.historical_service_spend_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="table-footer">Showing {assets.length} of {total} assets <span>Risk estimates use source install dates and repair history</span></div>
      </section>
    </>
  )
}

function RiskChart({ detail }: { detail: AssetDetail }) {
  const history = detail.risk_history.filter((point) => point.safety_index !== null)
  const width = 720
  const height = 220
  const left = 44
  const right = 18
  const top = 14
  const bottom = 34
  const plotWidth = width - left - right
  const plotHeight = height - top - bottom
  const y = (index: number) => top + (1 - Math.min(Math.max(index, 0), 100) / 100) * plotHeight
  const x = (i: number) => left + (history.length <= 1 ? plotWidth / 2 : (i / (history.length - 1)) * plotWidth)
  const line = history.map((point, i) => `${x(i)},${y(point.safety_index ?? 0)}`).join(' ')

  if (history.length === 0) return <EmptyState>No Safety Index history yet for this machine.</EmptyState>

  return (
    <svg className="risk-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Safety Index over time">
      {[0, 25, 50, 75, 100].map((tick) => (
        <g key={tick}>
          <line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} className="chart-gridline" />
          <text x={left - 8} y={y(tick) + 4} textAnchor="end" className="chart-axis-label">{tick}</text>
        </g>
      ))}
      <line x1={left} x2={width - right} y1={y(30)} y2={y(30)} className="chart-threshold high-threshold" />
      <text x={width - right - 4} y={y(30) + 14} textAnchor="end" className="chart-threshold-label high-label">Critical below 30</text>
      <line x1={left} x2={width - right} y1={y(50)} y2={y(50)} className="chart-threshold medium-threshold" />
      <text x={width - right - 4} y={y(50) - 5} textAnchor="end" className="chart-threshold-label medium-label">Poor below 50</text>
      <polyline points={line} className="chart-line" />
      {history.map((point, i) => <circle key={`${point.date}-${i}`} cx={x(i)} cy={y(point.safety_index ?? 0)} r="3.5" className="chart-point"><title>{shortDate(point.date)}: {indexText(point.safety_index)}</title></circle>)}
      <text x={left} y={height - 8} className="chart-axis-label">{shortDate(history[0].date)}</text>
      <text x={width - right} y={height - 8} textAnchor="end" className="chart-axis-label">{shortDate(history[history.length - 1].date)}</text>
    </svg>
  )
}

function DecisionPanel({ detail, onChanged }: { detail: AssetDetail; onChanged: () => void }) {
  const decision = detail.decision
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  if (!decision) return null
  const act = async (action: 'approve' | 'reject' | 'done') => {
    setBusy(true)
    setMessage('')
    try {
      await setDecisionStatus(decision.id, action)
      onChanged()
    } catch (actionError) {
      setMessage(actionError instanceof Error ? actionError.message : 'Could not update the decision.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="panel decision-panel">
      <div className="section-heading">
        <div><p className="overline">PROPOSED ACTION · {decision.status.replace('_', ' ').toUpperCase()}</p><h2>{decisionTitle(decision)}</h2></div>
        <span className="source-label">{decision.source === 'llm' ? 'Nemotron, rules checked in code' : 'Decided by rule'}</span>
      </div>
      <p className="muted">{decision.reason}</p>
      {decision.tasks.length > 0 && <ul className="decision-tasks">{decision.tasks.map((task) => <li key={task}>{task}</li>)}</ul>}
      <div className="decision-flags">
        {decision.requires_human_review && <span className="decision-flag warn">Needs human review</span>}
        {!decision.agreement && <span className="decision-flag warn">Model runs disagreed: safer option chosen</span>}
      </div>
      <div className="decision-actions">
        {decision.status === 'pending_review' && <>
          <button className="primary-button" type="button" disabled={busy} onClick={() => void act('approve')}>Approve</button>
          <button className="secondary-button" type="button" disabled={busy} onClick={() => void act('reject')}>Reject</button>
        </>}
        {decision.status === 'approved' && <button className="primary-button" type="button" disabled={busy} onClick={() => void act('done')}>Mark done</button>}
        {message && <span className="error-text">{message}</span>}
      </div>
    </section>
  )
}

function MachineDetailScreen({
  detail,
  asset,
  workOrders,
  onBack,
  onOpenMachine,
  onChanged,
}: {
  detail: AssetDetail
  asset?: Asset
  workOrders: WorkOrder[]
  onBack: () => void
  onOpenMachine: (id: string) => void
  onChanged: () => void
}) {
  const { risk_now: risk } = detail
  const assetWorkOrders = workOrders.filter((order) => order.asset_id === detail.asset.id)
  return (
    <>
      <button type="button" className="back-button" onClick={onBack}>← Back</button>
      <PageHeader title={`${machineName(detail.asset)} · ${detail.asset.site_name}`} description={`${detail.asset.city} · ${detail.asset.site_type}`} actions={<RiskBadge tier={risk.tier} />} />
      <section className="detail-hero panel">
        <div className="hero-risk">
          <span className="overline">SAFETY INDEX (100 = SAFEST)</span>
          <strong>{indexText(risk.safety_index)}</strong>
          <RiskBadge tier={risk.tier} />
        </div>
        <div className="hero-divider" />
        <div className="asset-facts">
          <div><span>Machine</span><strong>{detail.asset.id}</strong></div>
          <div><span>Installed</span><strong>{shortDate(detail.asset.install_date)}</strong></div>
          <div><span>Age</span><strong>{risk.age_years === null ? 'Not available' : `${risk.age_years.toFixed(1)} years`}</strong></div>
          <div><span>Hours</span><strong>{detail.asset.open_hours ?? 'Not provided'}</strong></div>
        </div>
        <div className="multiplier-chip">{`${bandText(risk.band)} · confidence ${risk.confidence.toFixed(2)}`}</div>
      </section>
      <DecisionPanel detail={detail} onChanged={onChanged} />

      <section className="detail-grid">
        <article className="panel detail-panel chart-panel">
          <div className="section-heading"><div><p className="overline">HISTORY</p><h2>Safety Index over time</h2></div><span className="current-risk">{indexText(risk.safety_index)} now</span></div>
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
          <div className="section-heading"><div><p className="overline">WEATHER</p><h2>7-day forecast</h2></div><span className="muted">National Weather Service</span></div>
          {detail.forecast.length === 0 ? <EmptyState>No forecast for this site (missing coordinates or outside the US).</EmptyState> : (
            <div className="forecast-list">
              {detail.forecast.map((day) => (
                <div className="forecast-day" key={day.date}><span>{shortDate(day.date)}</span><span className="weather-symbol" aria-hidden="true">{day.max_f >= 90 ? '☀' : '◒'}</span><strong>{day.max_f}°F</strong></div>
              ))}
            </div>
          )}
        </article>

        <article className="panel detail-panel cost-panel">
          <div className="section-heading"><div><p className="overline">SERVICE COSTS</p><h2>Historical invoice totals</h2></div><span className="source-label">Source: service records</span></div>
          <div className="expected-savings"><span>Recorded spend</span><strong>{money(detail.historical_service_spend_usd)}</strong></div>
          <div className="breakdown-row"><span>Service records</span><strong>{detail.service_record_count.toLocaleString()}</strong></div>
          <p className="muted">The source contains no preventive-versus-emergency cost comparison or savings estimate.</p>
        </article>

        <article className="panel detail-panel history-panel">
          <div className="section-heading"><div><p className="overline">SERVICE RECORD</p><h2>Imported maintenance history</h2></div></div>
          {detail.work_order_history.length === 0 ? <EmptyState>No service history is linked to this equipment ID.</EmptyState> : (
            <div className="timeline-list">
              {detail.work_order_history.slice(0, 30).map((record) => (
                <div className="timeline-row" key={record.source_row}><span className="timeline-dot" /><div><strong>{record.problem || record.work_type}</strong><span>{record.work_type} · {shortDate(record.date)}</span><p>{record.tech_notes || 'No technician note in source.'}</p></div></div>
              ))}
            </div>
          )}
        </article>

        <article className="panel detail-panel asset-orders-panel">
          <div className="section-heading"><div><p className="overline">EQUIPMENT DETAILS</p><h2>Manufacturer and model</h2></div></div>
          <div className="breakdown-row"><span>Manufacturer</span><strong>{detail.asset.manufacturer || 'Not provided'}</strong></div>
          <div className="breakdown-row"><span>Model</span><strong>{detail.asset.model || 'Not provided'}</strong></div>
          <div className="breakdown-row"><span>Refrigerant</span><strong>{detail.asset.refrigerant || 'Not provided'}</strong></div>
          <div className="breakdown-row"><span>Floor area</span><strong>Not provided</strong></div>
          <div className="section-heading service-record-heading"><div><p className="overline">MAINTENANCE</p><h2>Records for this machine</h2></div></div>
          {assetWorkOrders.length === 0 ? <EmptyState>No service records are linked to this equipment ID.</EmptyState> : (
            <div className="compact-orders">
              {assetWorkOrders.slice(0, 10).map((record) => <div key={record.source_row}><strong>{record.work_type}</strong><span>{record.problem || 'No problem specified'}</span><small>{shortDate(record.date)} · Invoice {money(record.invoice_total_usd)}</small></div>)}
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
  assetIds,
  onOpenMachine,
}: {
  workOrders: WorkOrder[]
  assetIds: ReadonlySet<string>
  onOpenMachine: (id: string) => void
}) {
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const pageSize = 50
  const filteredRecords = useMemo(() => {
    const query = search.trim().toLowerCase()
    if (!query) return workOrders
    return workOrders.filter((record) => [
      record.asset_id,
      record.site_name,
      record.city,
      record.work_type,
      record.problem,
      record.tech_notes,
      record.date,
    ].join(' ').toLowerCase().includes(query))
  }, [search, workOrders])
  const pageCount = Math.max(1, Math.ceil(filteredRecords.length / pageSize))
  const visibleRecords = filteredRecords.slice(page * pageSize, (page + 1) * pageSize)

  return (
    <>
      <PageHeader title="Service History" description="Historical maintenance records imported from the work-order CSV." actions={<span className="count-chip">{workOrders.length.toLocaleString()} records</span>} />
      <section className="panel table-panel">
        {workOrders.length === 0 ? <EmptyState>No service history is available in the source files.</EmptyState> : (
          <>
            <div className="table-toolbar history-toolbar">
              <label className="search-control">
                <span aria-hidden="true">⌕</span>
                <input type="search" value={search} onChange={(event) => { setSearch(event.target.value); setPage(0) }} placeholder="Search equipment, site, problem, or notes" />
              </label>
              <div className="pagination-controls">
                <button type="button" className="hold-button" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
                <span>Page {page + 1} of {pageCount}</span>
                <button type="button" className="hold-button" disabled={page + 1 >= pageCount} onClick={() => setPage(page + 1)}>Next</button>
              </div>
            </div>
            {visibleRecords.length === 0 ? <EmptyState>No service records match this search.</EmptyState> : <div className="table-scroll">
              <table className="data-table work-orders-table">
              <thead><tr><th>Source row</th><th>Equipment / site</th><th>Service type</th><th>Problem</th><th>Refrigerant charge</th><th>Service date</th><th>Invoice total</th><th>Technician notes</th></tr></thead>
              <tbody>
                {visibleRecords.map((order) => (
                  <tr key={order.source_row}>
                    <td><strong>CSV row {order.source_row}</strong></td>
                    <td>{assetIds.has(order.asset_id)
                      ? <button className="table-link" type="button" onClick={() => onOpenMachine(order.asset_id)}>{order.machine_name}<small>{order.site_name} · {order.city}</small></button>
                      : <span>{order.machine_name}<small className="table-subtext">{order.site_name} · equipment record unmatched</small></span>}</td>
                    <td>{order.work_type}</td>
                    <td>{order.problem || 'Not specified'}</td>
                    <td>{order.refrigerant_charge === null ? '—' : order.refrigerant_charge}</td>
                    <td>{shortDate(order.date)}</td>
                    <td className="money-cell">{money(order.invoice_total_usd)}</td>
                    <td className="reason-cell">{order.tech_notes || '—'}</td>
                  </tr>
                ))}
              </tbody>
              </table>
            </div>}
          </>
        )}
        <div className="table-footer">Showing {filteredRecords.length ? page * pageSize + 1 : 0}–{Math.min((page + 1) * pageSize, filteredRecords.length)} of {filteredRecords.length.toLocaleString()} matching records <span>These are historical records, not pending work orders</span></div>
      </section>
    </>
  )
}

function ActivityScreen({ items }: { items: ActivityItem[] }) {
  return (
    <>
      <PageHeader title="Activity" description="Communication records are not included in the imported source data." />
      <section className="panel activity-panel">
        {items.length === 0 ? <EmptyState>Messages and calls are not included in the imported CSV files.</EmptyState> : (
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

function DataNotesScreen({
  portfolio,
  savings,
  files,
  inputRef,
  onFilesSelected,
  onRemoveFile,
  onClearFiles,
  onPipelineDone,
}: {
  portfolio: Portfolio
  savings: Savings
  files: File[]
  inputRef: RefObject<HTMLInputElement | null>
  onFilesSelected: (files: File[]) => void
  onRemoveFile: (file: File) => void
  onClearFiles: () => void
  onPipelineDone: () => void
}) {
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState('')
  const [failed, setFailed] = useState(false)
  const STEP_TEXT: Record<string, string> = { extract: 'Reading files (Nemotron)', load: 'Saving', weather: 'Fetching weather', score: 'Scoring', decide: 'Deciding actions (Nemotron)' }
  const run = async (useDemo: boolean) => {
    setRunning(true)
    setFailed(false)
    setStatus('Starting…')
    try {
      const job = useDemo
        ? await runSampleData((j) => setStatus(j.step ? `${STEP_TEXT[j.step] ?? j.step}…` : 'Starting…'))
        : await runPipeline(files, (j) => setStatus(j.step ? `${STEP_TEXT[j.step] ?? j.step}…` : 'Starting…'))
      const extract = job.steps.extract ?? {}
      const decide = job.steps.decide ?? {}
      setStatus(`Done in ${job.seconds_total ?? '?'}s: ${String(extract.sites ?? 0)} sites, ${String(extract.assets ?? 0)} machines, ${String(extract.work_orders ?? 0)} work orders read; ${String(extract.rejected ?? 0)} rows rejected; ${String(decide.decisions ?? 0)} actions proposed.`)
      onPipelineDone()
    } catch (runError) {
      setFailed(true)
      setStatus(runError instanceof Error ? runError.message : 'The pipeline run failed.')
    } finally {
      setRunning(false)
    }
  }
  return (
    <>
      <PageHeader title="Data notes & import" description="Upload your sites, equipment and work-order files, or run the bundled demo portfolio." />
      <section className="demo-grid">
        <article className="panel demo-card import-data-card">
          <span className="demo-icon" aria-hidden="true">⇧</span>
          <p className="overline">IMPORT DATA</p>
          <h2>Select files to import</h2>
          <p>CSV or Excel, messy is fine. Name files with “sites”, “assets” and “work_orders”; any you leave out are taken from what is already loaded. Weather is fetched automatically.</p>
          <input
            ref={inputRef}
            id="import-files"
            type="file"
            multiple
            hidden
            onChange={(event) => {
              onFilesSelected(Array.from(event.currentTarget.files ?? []))
              event.currentTarget.value = ''
            }}
          />
          <div className="import-file-actions">
            <button className="primary-button" type="button" aria-controls="import-files" onClick={() => inputRef.current?.click()}>
              Choose File <span aria-hidden="true">＋</span>
            </button>
            {files.length > 0 && <span className="import-file-count">{files.length} {files.length === 1 ? 'file' : 'files'} selected</span>}
            {files.length > 0 && <button className="text-button" type="button" onClick={onClearFiles}>Clear all</button>}
          </div>
          {files.length > 0 && (
            <ul className="import-file-list" aria-label="Selected files">
              {files.map((file) => (
                <li key={`${file.name}:${file.size}:${file.lastModified}`}>
                  <span><strong>{file.name}</strong><small>{fileSize(file.size)}</small></span>
                  <button type="button" aria-label={`Remove ${file.name}`} onClick={() => onRemoveFile(file)}>×</button>
                </li>
              ))}
            </ul>
          )}
          <div className="import-file-actions">
            <button className="primary-button" type="button" disabled={running || files.length === 0} onClick={() => void run(false)}>Run analysis</button>
            <button className="secondary-button" type="button" disabled={running} onClick={() => void run(true)}>Use demo data (30 sites)</button>
          </div>
          <p className={`import-note ${failed ? 'error-text' : ''}`} role="status">{status || 'Nothing has been run yet in this session.'}</p>
        </article>
        <article className="panel demo-card">
          <span className="demo-icon" aria-hidden="true">▤</span>
          <p className="overline">IMPORTED DATA</p>
          <h2>Source file coverage</h2>
          <p>{portfolio.sites.toLocaleString()} sites, {portfolio.machines.toLocaleString()} equipment records, and {portfolio.service_records.toLocaleString()} service-history rows are loaded in the database.</p>
          <div className="demo-last-run"><span>Supported risk inputs</span><strong>{portfolio.supported_assets.toLocaleString()} assets have both a supported equipment category and an installation date.</strong></div>
          <div className="demo-last-run"><span>Invoice totals</span><strong>{savings.records_with_invoice_total.toLocaleString()} of {savings.total_service_records.toLocaleString()} service records include an invoice amount.</strong></div>
        </article>

        <article className="panel demo-card">
          <span className="demo-icon" aria-hidden="true">ⓘ</span>
          <p className="overline">LIMITATIONS</p>
          <h2>What the scores are, and aren’t</h2>
          <p>Equipment types other than rooftop units, walk-in coolers/freezers and water heaters are not scored; rows that can’t be trusted are rejected with a reason.</p>
          <div className="demo-last-run"><span>Safety Index</span><strong>0–100 from equipment age, work-order history and the weather forecast. The weights are expert judgement, not fitted to failure data; it is a triage score, not a probability.</strong></div>
          <div className="demo-last-run"><span>Actions</span><strong>Proposed by Nemotron within rules enforced in code, and stored for human review. Nothing is sent or executed automatically.</strong></div>
        </article>
      </section>
    </>
  )
}

export default App

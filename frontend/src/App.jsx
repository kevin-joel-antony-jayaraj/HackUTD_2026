import { useCallback, useEffect, useMemo, useState } from 'react'
import penguinLogo from './assets/Pengu.jpg'
import './App.css'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'
const FILTERS = ['all', 'high', 'medium', 'low']
const IMPORT_TYPES = [
  { id: 'sites', label: 'Sites', description: 'Site and location details' },
  { id: 'assets', label: 'Equipment', description: 'Equipment and installation details' },
  { id: 'work_orders', label: 'Work orders', description: 'Service and maintenance history' },
]
const TODAY = new Date()

function getGreeting(date) {
  const hour = date.getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

function formatDate(date) {
  return date.toLocaleDateString(undefined, {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  })
}

function formatRisk(probability) {
  return Number.isFinite(probability) ? `${Math.round(probability * 100)}%` : '—'
}

function MetricIcon({ type }) {
  const paths = {
    sites: <><path d="M3 21h18" /><path d="M5 21V7l8-4v18" /><path d="M19 21V11l-6-4" /><path d="M9 9v.01M9 12v.01M9 15v.01M9 18v.01M16 14v.01M16 17v.01" /></>,
    equipment: <><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M9 9h6v6H9zM9 1v3m6-3v3M9 20v3m6-3v3M1 9h3m-3 6h3m16-6h3m-3 6h3" /></>,
    risk: <><path d="m12 3 10 18H2L12 3Z" /><path d="M12 9v5m0 3h.01" /></>,
    orders: <><path d="M8 6h13M8 12h13M8 18h13" /><path d="M3 6h.01M3 12h.01M3 18h.01" /></>,
  }

  return (
    <span className="metric-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        {paths[type]}
      </svg>
    </span>
  )
}

function App() {
  const [profile, setProfile] = useState(null)
  const [loginStep, setLoginStep] = useState('company')
  const [loginCompany, setLoginCompany] = useState('')
  const [loginName, setLoginName] = useState('')
  const [loginEmail, setLoginEmail] = useState('')
  const [loginError, setLoginError] = useState('')
  const [portfolio, setPortfolio] = useState(null)
  const [equipment, setEquipment] = useState([])
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState('overview')
  const [importKind, setImportKind] = useState('assets')
  const [selectedFile, setSelectedFile] = useState(null)
  const [isDragging, setIsDragging] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [importError, setImportError] = useState('')
  const [importResult, setImportResult] = useState(null)

  const requestDashboard = useCallback(async () => {
    const [portfolioResponse, equipmentResponse] = await Promise.all([
      fetch(`${API_BASE}/portfolio`),
      fetch(`${API_BASE}/assets?limit=100`),
    ])
    if (!portfolioResponse.ok || !equipmentResponse.ok) {
      throw new Error('The dashboard data could not be loaded. Please try again.')
    }
    const [portfolioData, equipmentData] = await Promise.all([
      portfolioResponse.json(),
      equipmentResponse.json(),
    ])
    return { portfolioData, equipmentData }
  }, [])

  const applyDashboard = useCallback(({ portfolioData, equipmentData }) => {
    setError('')
    setPortfolio(portfolioData)
    setEquipment(equipmentData)
    setLoading(false)
  }, [])

  const showLoadError = useCallback((loadError) => {
    setError(
      loadError instanceof TypeError
        ? 'Unable to connect to the dashboard API. Check that the backend is running and try again.'
        : loadError.message || 'The dashboard data could not be loaded.',
    )
    setLoading(false)
  }, [])

  useEffect(() => {
    if (!profile) return undefined

    let active = true
    requestDashboard()
      .then((data) => {
        if (active) applyDashboard(data)
      })
      .catch((loadError) => {
        if (active) showLoadError(loadError)
      })
    return () => {
      active = false
    }
  }, [applyDashboard, profile, requestDashboard, showLoadError])

  const continueLogin = (event) => {
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

  const refreshDashboard = () => {
    setLoading(true)
    setError('')
    requestDashboard().then(applyDashboard).catch(showLoadError)
  }

  const selectImportFile = (file) => {
    setImportError('')
    setImportResult(null)
    if (!file) {
      setSelectedFile(null)
      return
    }
    if (!/\.(csv|xlsx|xls)$/i.test(file.name)) {
      setSelectedFile(null)
      setImportError('Choose a CSV or Excel file (.csv, .xlsx, or .xls).')
      return
    }
    if (file.size > 20 * 1024 * 1024) {
      setSelectedFile(null)
      setImportError('This file is larger than 20 MB. Choose a smaller file.')
      return
    }
    setSelectedFile(file)
  }

  const uploadFile = async (event) => {
    event.preventDefault()
    if (!selectedFile || isUploading) return

    setIsUploading(true)
    setImportError('')
    setImportResult(null)
    try {
      const body = new FormData()
      body.append('file', selectedFile)
      const response = await fetch(`${API_BASE}/import/${importKind}`, {
        method: 'POST',
        body,
      })
      const result = await response.json()
      if (!response.ok) {
        if (response.status === 401) {
          throw new Error('This API requires administrator authorization. Ask your administrator to configure access for imports.')
        }
        throw new Error(result.detail || 'The file could not be imported.')
      }
      setImportResult(result)
      setSelectedFile(null)
      setLoading(true)
      requestDashboard().then(applyDashboard).catch(showLoadError)
    } catch (uploadError) {
      setImportError(
        uploadError instanceof TypeError
          ? 'Unable to connect to the dashboard API. Check that the backend is running and try again.'
          : uploadError.message || 'The file could not be imported.',
      )
    } finally {
      setIsUploading(false)
    }
  }

  const handleDrop = (event) => {
    event.preventDefault()
    setIsDragging(false)
    selectImportFile(event.dataTransfer.files[0])
  }

  const visibleEquipment = useMemo(() => {
    const query = search.trim().toLowerCase()
    return equipment.filter((item) => {
      const matchesFilter = filter === 'all' || item.tier === filter
      const searchable = [item.asset_id, item.asset_type, item.site_name, item.site_id, item.region_id]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return matchesFilter && (!query || searchable.includes(query))
    })
  }, [equipment, filter, search])

  const highRiskCount = portfolio?.assets_by_tier?.high ?? 0
  const activeWorkOrders = (portfolio?.work_orders_by_status?.pending_approval ?? 0)
    + (portfolio?.work_orders_by_status?.approved ?? 0)
  const lastRun = portfolio?.last_run

  if (!profile) {
    return (
      <main className="login-page">
        <section className="login-card" aria-labelledby="login-title">
          <div className="login-brand">
            <img className="login-logo" src={penguinLogo} alt="PenguinRE penguin logo" />
          </div>

          <div className="login-intro">
            <p className="login-eyebrow">Preventing operational failures for the 90% that get ignored</p>
            <h1 id="login-title">
              {loginStep === 'company' ? (
                <>Welcome to Penguin<span className="login-title-accent">RE</span></>
              ) : 'A little about you'}
            </h1>
            <p>
              {loginStep === 'company'
                ? 'Let’s get your company workspace set up.'
                : `Set up your profile for ${loginCompany.trim()}.`}
            </p>
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
                <button
                  className="login-back-button"
                  type="button"
                  onClick={() => {
                    setLoginError('')
                    setLoginStep('company')
                  }}
                >
                  Back
                </button>
              )}
              <button className="login-continue-button" type="submit">
                {loginStep === 'company' ? 'Continue' : 'Open dashboard'}
                <span aria-hidden="true">→</span>
              </button>
            </div>
          </form>

          <p className="login-note">Demo setup only — this screen does not authenticate or save your details.</p>
        </section>
      </main>
    )
  }

  return (
    <div className="dashboard-shell">
      <header className="topbar">
        <a className="brand" href="#overview" aria-label="PenguinRE dashboard">
          <span className="brand-mark" aria-hidden="true">
            <img src={penguinLogo} alt="" />
          </span>
          <span className="brand-name">Penguin<span>RE</span></span>
        </a>
        <div className="topbar-divider" />
        <nav className="primary-tabs" aria-label="Dashboard sections">
          <button
            className={`primary-tab ${activeTab === 'overview' ? 'active' : ''}`}
            type="button"
            aria-current={activeTab === 'overview' ? 'page' : undefined}
            onClick={() => setActiveTab('overview')}
          >
            Overview
          </button>
          <button
            className={`primary-tab ${activeTab === 'data' ? 'active' : ''}`}
            type="button"
            aria-current={activeTab === 'data' ? 'page' : undefined}
            onClick={() => setActiveTab('data')}
          >
            Data
          </button>
        </nav>
        <div className="topbar-spacer" />
        <span className={`system-status ${error ? 'system-status-offline' : ''}`}>
          <span />{error ? 'API unavailable' : loading ? 'Connecting' : 'Portfolio monitoring'}
        </span>
        <button className="refresh-button" type="button" onClick={refreshDashboard} disabled={loading}>
          <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path d="M16.5 9.2A6.6 6.6 0 0 0 4.2 6.8L3 8m0 0V4.8M3 8h3.2M3.5 10.8a6.6 6.6 0 0 0 12.3 2.4L17 12m0 0v3.2m0-3.2h-3.2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span>{loading ? 'Updating' : 'Refresh'}</span>
        </button>
        <span className="signed-in-user" title={`${profile.name} · ${profile.email}`}>
          <span className="user-avatar">{profile.name.trim().charAt(0).toUpperCase()}</span>
          <span className="user-name">{profile.name}</span>
        </span>
        <button
          className="sign-out-button"
          type="button"
          onClick={() => {
            setProfile(null)
            setLoginStep('company')
            setLoginCompany('')
            setLoginName('')
            setLoginEmail('')
          }}
        >
          Sign out
        </button>
      </header>

      <main id="overview" className="dashboard-content">
        <section className="welcome-row">
          <div>
            <p className="eyebrow">{formatDate(TODAY)}</p>
            <h1>{activeTab === 'overview' ? `${getGreeting(TODAY)}, ${profile.name.split(/\s+/)[0]}` : 'Add to your portfolio'}</h1>
            <p className="welcome-copy">
              {activeTab === 'overview'
                ? 'Here’s the latest across your equipment portfolio.'
                : 'Upload your site, equipment, or service history data.'}
            </p>
          </div>
        </section>

        {activeTab === 'overview' ? (
          <>
            <section className="metrics-grid" aria-label="Portfolio summary">
          <article className="metric-card">
            <div className="metric-card-top"><span className="metric-label">Sites monitored</span><MetricIcon type="sites" /></div>
            <p className="metric-value">{portfolio ? portfolio.sites : '—'}</p>
            <p className="metric-caption">Across your portfolio</p>
          </article>
          <article className="metric-card">
            <div className="metric-card-top"><span className="metric-label">Equipment tracked</span><MetricIcon type="equipment" /></div>
            <p className="metric-value">{portfolio ? portfolio.assets : '—'}</p>
            <p className="metric-caption">Assets being monitored</p>
          </article>
          <article className="metric-card metric-card-highlight">
            <div className="metric-card-top"><span className="metric-label">High risk</span><MetricIcon type="risk" /></div>
            <p className="metric-value">{portfolio ? highRiskCount : '—'}</p>
            <p className="metric-caption">Assets needing attention</p>
          </article>
          <article className="metric-card">
            <div className="metric-card-top"><span className="metric-label">Active work orders</span><MetricIcon type="orders" /></div>
            <p className="metric-value">{portfolio ? activeWorkOrders : '—'}</p>
            <p className="metric-caption">Pending approval or in progress</p>
          </article>
            </section>

            <section className="content-grid">
          <article className="panel equipment-panel" id="equipment">
            <div className="panel-heading">
              <div>
                <h2>Equipment overview</h2>
                <p className="panel-subtitle">Your assets, ordered by risk level</p>
              </div>
              <span className="asset-count">{portfolio?.assets ?? '—'} assets</span>
            </div>

            <div className="table-controls">
              <div className="filter-tabs" role="group" aria-label="Filter equipment by risk">
                {FILTERS.map((item) => (
                  <button
                    className={`filter-tab ${filter === item ? 'active' : ''}`}
                    key={item}
                    onClick={() => setFilter(item)}
                    type="button"
                  >
                    {item === 'all' ? 'All assets' : `${item[0].toUpperCase()}${item.slice(1)} risk`}
                  </button>
                ))}
              </div>
              <label className="search-field">
                <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
                  <circle cx="8.8" cy="8.8" r="5.8" stroke="currentColor" strokeWidth="1.5" />
                  <path d="m13 13 4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
                <input
                  type="search"
                  placeholder="Search equipment"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </label>
            </div>

            {error ? (
              <div className="state-message error-state" role="alert">
                <span>{error}</span>
                <button type="button" onClick={refreshDashboard}>Try again</button>
              </div>
            ) : loading && equipment.length === 0 ? (
              <div className="state-message">Loading your equipment…</div>
            ) : visibleEquipment.length === 0 ? (
              <div className="state-message">
                {equipment.length === 0 ? 'No equipment found in your portfolio yet.' : 'No equipment matches your search.'}
              </div>
            ) : (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Equipment</th>
                      <th scope="col">Site</th>
                      <th scope="col">Risk level</th>
                      <th scope="col">Failure risk</th>
                      <th scope="col">Work order</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleEquipment.map((item) => (
                      <tr key={item.asset_id}>
                        <td>
                          <div className="equipment-cell">
                            <span className="equipment-avatar" aria-hidden="true">
                              <MetricIcon type="equipment" />
                            </span>
                            <span><strong>{item.asset_id}</strong><small>{item.asset_type || 'Equipment'}</small></span>
                          </div>
                        </td>
                        <td><span className="site-name">{item.site_name || item.site_id}</span><small className="site-region">{item.region_id || item.site_id}</small></td>
                        <td><span className={`risk-badge risk-${item.tier || 'unknown'}`}><span />{item.tier || 'Unknown'}</span></td>
                        <td className="probability-value">{formatRisk(item.prob)}</td>
                        <td>{item.open_work_order ? <span className="work-order-badge">Open</span> : <span className="no-work-order">—</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="panel-footer">
              <span>{portfolio ? `Showing ${visibleEquipment.length} of ${equipment.length} assets` : 'Waiting for portfolio data'}</span>
              <span className="sort-note">Sorted by highest risk</span>
            </div>
          </article>

          <aside className="panel insight-panel" id="work-orders">
            <div className="panel-heading">
              <div><h2>At a glance</h2><p className="panel-subtitle">Portfolio health</p></div>
              <span className="insight-sparkle" aria-hidden="true">✦</span>
            </div>
            <div className="risk-summary">
              <div className="risk-summary-header"><span>Risk distribution</span><span>{portfolio ? `${portfolio.assets} assets` : '—'}</span></div>
              {['high', 'medium', 'low'].map((tier) => {
                const count = portfolio?.assets_by_tier?.[tier] ?? 0
                const total = portfolio?.assets ?? 0
                const percent = total ? Math.min((count / total) * 100, 100) : 0
                return (
                  <div className="risk-row" key={tier}>
                    <div className="risk-row-label"><span className={`risk-dot risk-dot-${tier}`} />{tier[0].toUpperCase() + tier.slice(1)}<strong>{portfolio ? count : '—'}</strong></div>
                    <div className="risk-track"><span className={`risk-fill risk-fill-${tier}`} style={{ width: `${percent}%` }} /></div>
                  </div>
                )
              })}
            </div>
            <div className="insight-divider" />
            <div className="last-sweep">
              <span className="sweep-icon" aria-hidden="true">
                <svg viewBox="0 0 20 20" fill="none"><path d="M10 2.5v2m0 11v2m7.5-7.5h-2m-11 0h-2m12.8-5.3-1.4 1.4M6.1 13.9l-1.4 1.4m10.6 0-1.4-1.4M6.1 6.1 4.7 4.7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /><circle cx="10" cy="10" r="3.5" stroke="currentColor" strokeWidth="1.5" /></svg>
              </span>
              <div><span className="sweep-label">Latest risk sweep</span><strong>{lastRun?.summary || (portfolio ? 'No risk sweep yet' : 'Waiting for portfolio data')}</strong></div>
            </div>
            <p className="insight-footnote">Risk scores update as your portfolio data changes.</p>
          </aside>
            </section>
          </>
        ) : (
          <section className="data-import-layout" aria-label="Import and current data">
            <article className="panel import-panel">
              <div className="panel-heading import-heading">
                <div>
                  <h2>Upload a data file</h2>
                  <p className="panel-subtitle">Import a CSV or Excel export into your portfolio.</p>
                </div>
                <span className="import-heading-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24" fill="none"><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 14v5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>
                </span>
              </div>

              <div className="import-type-tabs" role="tablist" aria-label="Data type">
                {IMPORT_TYPES.map((type) => (
                  <button
                    className={`import-type-tab ${importKind === type.id ? 'active' : ''}`}
                    id={`import-tab-${type.id}`}
                    key={type.id}
                    type="button"
                    role="tab"
                    aria-selected={importKind === type.id}
                    aria-controls="import-form"
                    onClick={() => {
                      setImportKind(type.id)
                      setImportError('')
                      setImportResult(null)
                    }}
                  >
                    {type.label}
                  </button>
                ))}
              </div>

              <form
                id="import-form"
                className="import-form"
                role="tabpanel"
                aria-labelledby={`import-tab-${importKind}`}
                tabIndex={0}
                onSubmit={uploadFile}
              >
                <div
                  className={`drop-zone ${isDragging ? 'dragging' : ''} ${selectedFile ? 'has-file' : ''}`}
                  onDragOver={(event) => {
                    event.preventDefault()
                    setIsDragging(true)
                  }}
                  onDragLeave={(event) => {
                    if (!event.currentTarget.contains(event.relatedTarget)) setIsDragging(false)
                  }}
                  onDrop={handleDrop}
                >
                  <input
                    id="data-file"
                    className="file-input"
                    type="file"
                    accept=".csv,.xlsx,.xls"
                    onChange={(event) => {
                      selectImportFile(event.target.files[0])
                      event.target.value = ''
                    }}
                    disabled={isUploading}
                  />
                  <label className="drop-zone-content" htmlFor="data-file">
                    <span className="drop-icon" aria-hidden="true">
                      <svg viewBox="0 0 24 24" fill="none"><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 14v5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>
                    </span>
                    {selectedFile ? (
                      <>
                        <strong>{selectedFile.name}</strong>
                        <span>{(selectedFile.size / 1024).toFixed(1)} KB · Click to choose another file</span>
                      </>
                    ) : (
                      <>
                        <strong>Drop your file here, or <span>browse</span></strong>
                        <span>CSV or Excel file · Max 20 MB</span>
                      </>
                    )}
                  </label>
                </div>

                <p className="import-help">
                  {IMPORT_TYPES.find((type) => type.id === importKind)?.description}. We’ll match common column names and flag rows that need review.
                </p>

                {importError && <p className="import-message import-error" role="alert">{importError}</p>}
                {importResult && (
                  <div className="import-message import-success" role="status">
                    <strong>Import complete</strong>
                    <span>{importResult.imported} rows imported · {importResult.quarantined} rows need review</span>
                    {importResult.fuzzy_matched > 0 && <span>{importResult.fuzzy_matched} equipment records matched by name</span>}
                    <span className="matched-columns">
                      Matched columns: {Object.keys(importResult.columns_matched || {}).length
                        ? Object.keys(importResult.columns_matched).join(', ')
                        : 'none'}
                    </span>
                  </div>
                )}

                <div className="import-actions">
                  {selectedFile && (
                    <button className="clear-file-button" type="button" onClick={() => selectImportFile(null)} disabled={isUploading}>
                      Remove file
                    </button>
                  )}
                  <button className="upload-button" type="submit" disabled={!selectedFile || isUploading}>
                    {isUploading ? 'Importing…' : 'Import data'}
                    {!isUploading && <span aria-hidden="true">→</span>}
                  </button>
                </div>
              </form>
            </article>

            <aside className="panel current-data-panel">
              <div className="panel-heading">
                <div>
                  <h2>Current data</h2>
                  <p className="panel-subtitle">Your portfolio right now</p>
                </div>
              </div>
              <div className="current-data-stats">
                <div><span>Sites</span><strong>{portfolio?.sites ?? '—'}</strong></div>
                <div><span>Equipment</span><strong>{portfolio?.assets ?? '—'}</strong></div>
                <div><span>Work orders</span><strong>{portfolio ? Object.values(portfolio.work_orders_by_status || {}).reduce((sum, count) => sum + count, 0) : '—'}</strong></div>
              </div>
              <div className="current-equipment-heading">
                <span>Equipment currently tracked</span>
                <button type="button" onClick={() => setActiveTab('overview')}>View all</button>
              </div>
              {equipment.length ? (
                <div className="current-equipment-list">
                  {equipment.slice(0, 5).map((item) => (
                    <div className="current-equipment-row" key={item.asset_id}>
                      <span className="current-equipment-icon"><MetricIcon type="equipment" /></span>
                      <span className="current-equipment-name"><strong>{item.asset_id}</strong><small>{item.site_name || item.site_id}</small></span>
                      <span className={`risk-badge risk-${item.tier || 'unknown'}`}><span />{item.tier || 'Unknown'}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="current-empty">
                  {error ? 'Start the backend to load your current portfolio.' : loading ? 'Loading portfolio data…' : 'No equipment data yet.'}
                </p>
              )}
              <div className="current-data-footer">
                <span>New uploads refresh the dashboard automatically.</span>
                <button type="button" onClick={refreshDashboard} disabled={loading}>
                  {loading ? 'Updating…' : 'Refresh data'}
                </button>
              </div>
            </aside>
          </section>
        )}

        <footer className="dashboard-footer">
          <span>PenguinRE <span className="footer-dot">·</span> Preventing operational failures for the 90% that get ignored</span>
        </footer>
      </main>
    </div>
  )
}

export default App

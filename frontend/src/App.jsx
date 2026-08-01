import React, { useState, useEffect } from 'react';

const API_BASE = 'http://localhost:8000';

function App() {
  const [activeTab, setActiveTab] = useState('finder');
  const [searchQuery, setSearchQuery] = useState('');
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analyses, setAnalyses] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [selectedAnalysis, setSelectedAnalysis] = useState(null);
  const [matches, setMatches] = useState([]);
  const [runLogs, setRunLogs] = useState([]);
  
  // Leads Manager State
  const [stats, setStats] = useState({ total: 0, pending: 0, crawled: 0, synthesized: 0, failed: 0 });
  const [leads, setLeads] = useState([]);
  const [isCrawling, setIsCrawling] = useState(false);
  const [leadsPage, setLeadsPage] = useState(1);
  const [leadsLimit, setLeadsLimit] = useState(50);
  const [leadsStatus, setLeadsStatus] = useState('all');
  const [leadsSearch, setLeadsSearch] = useState('');
  const [totalLeadsCount, setTotalLeadsCount] = useState(0);

  // JLCPCB Hub State
  const [jlcStatus, setJlcStatus] = useState({
    status: 'not_downloaded',
    downloaded_bytes: 0,
    total_bytes: 0,
    progress_percent: 0.0,
    speed_mbps: 0.0,
    eta_seconds: 0,
    error: null
  });
  const [jlcResults, setJlcResults] = useState([]);
  const [jlcTotalCount, setJlcTotalCount] = useState(0);
  const [jlcPage, setJlcPage] = useState(1);
  const [jlcLimit] = useState(25);
  const [jlcFilters, setJlcFilters] = useState({
    keyword: '',
    manufacturer: '',
    category: '',
    minStock: 10000,
    hsn: '85423200'
  });
  const [isQueryingJlc, setIsQueryingJlc] = useState(false);
  const [selectedParts, setSelectedParts] = useState({});
  const [isSyncingJlc, setIsSyncingJlc] = useState(false);
  const [syncFeedback, setSyncFeedback] = useState('');

  // Fetch JLCPCB SQLite Database status
  const fetchJlcStatus = async () => {
    try {
      const res = await fetch(`${API_BASE}/jlcpcb/status`);
      const data = await res.json();
      setJlcStatus(data);
    } catch (e) {
      console.error('Failed to fetch JLCPCB status', e);
    }
  };

  // Trigger JLCPCB database download
  const handleJlcDownload = async () => {
    try {
      setJlcStatus(prev => ({ ...prev, status: 'downloading', progress_percent: 0.0 }));
      await fetch(`${API_BASE}/jlcpcb/download`, { method: 'POST' });
      fetchJlcStatus();
    } catch (e) {
      console.error('Failed to trigger download', e);
    }
  };

  // Run parametric query
  const queryJlcDb = async (page = jlcPage) => {
    setIsQueryingJlc(true);
    setSyncFeedback('');
    try {
      const offset = (page - 1) * jlcLimit;
      const res = await fetch(`${API_BASE}/jlcpcb/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          keyword: jlcFilters.keyword,
          manufacturer: jlcFilters.manufacturer,
          category: jlcFilters.category,
          min_stock: Number(jlcFilters.minStock),
          limit: jlcLimit,
          offset: offset
        })
      });
      if (res.status === 404) {
        setJlcResults([]);
        setJlcTotalCount(0);
        return;
      }
      const data = await res.json();
      if (data.success) {
        setJlcResults(data.components || []);
        setJlcTotalCount(data.total_matches || 0);
        setSelectedParts({});
      }
    } catch (e) {
      console.error('Query failed', e);
    } finally {
      setIsQueryingJlc(false);
    }
  };

  // Sync selected parts to MySQL
  const handleJlcImport = async () => {
    const toImport = jlcResults.filter(p => selectedParts[p.part_number]);
    if (toImport.length === 0) {
      alert('Please select at least one component to import.');
      return;
    }

    setIsSyncingJlc(true);
    setSyncFeedback('Ingesting components and mapping supplier nodes...');
    try {
      const res = await fetch(`${API_BASE}/jlcpcb/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          components: toImport,
          hsn: jlcFilters.hsn
        })
      });
      const data = await res.json();
      if (data.success) {
        setSyncFeedback(`Successfully imported ${data.imported_count} components into the MySQL schema!`);
        fetchAnalyses();
        fetchStats();
        fetchLeads();
      } else {
        setSyncFeedback(`Import failed: ${data.detail || 'Internal server error'}`);
      }
    } catch (e) {
      setSyncFeedback(`Import failed: ${e.message}`);
    } finally {
      setIsSyncingJlc(false);
    }
  };

  // Poll status when downloading
  useEffect(() => {
    fetchJlcStatus();
    let interval;
    if (jlcStatus.status === 'downloading') {
      interval = setInterval(() => {
        fetchJlcStatus();
      }, 2000);
    } else {
      interval = setInterval(() => {
        fetchJlcStatus();
      }, 5000);
    }
    return () => clearInterval(interval);
  }, [jlcStatus.status]);

  // Run query when pagination changes
  useEffect(() => {
    if (jlcStatus.status === 'ready') {
      queryJlcDb(jlcPage);
    }
  }, [jlcPage]);

  // Load analyses on mount
  useEffect(() => {
    fetchAnalyses();
    fetchStats();
  }, []);

  // Fetch details when selected analysis changes
  useEffect(() => {
    if (selectedId) {
      fetchAnalysisDetail(selectedId);
      fetchMatches(selectedId);
    } else {
      setSelectedAnalysis(null);
      setMatches([]);
    }
  }, [selectedId]);

  const fetchAnalyses = async () => {
    try {
      const res = await fetch(`${API_BASE}/analyses`);
      const data = await res.json();
      setAnalyses(data);
      if (data.length > 0 && !selectedId) {
        setSelectedId(data[0].id);
      }
    } catch (e) {
      console.error('Failed to fetch analyses', e);
    }
  };

  const fetchAnalysisDetail = async (id) => {
    try {
      const res = await fetch(`${API_BASE}/analyses/${id}`);
      const data = await res.json();
      setSelectedAnalysis(data);
    } catch (e) {
      console.error('Failed to fetch analysis details', e);
    }
  };

  const fetchMatches = async (id) => {
    try {
      const res = await fetch(`${API_BASE}/analyses/${id}/matches`);
      const data = await res.json();
      setMatches(data.matches || []);
    } catch (e) {
      console.error('Failed to fetch matches', e);
    }
  };

  const fetchStats = async () => {
    try {
      const res = await fetch(`${API_BASE}/stats`);
      const data = await res.json();
      setStats(data);
    } catch (e) {
      console.error('Failed to fetch stats', e);
    }
  };

  const fetchLeads = async (page = leadsPage, limit = leadsLimit, status = leadsStatus, search = leadsSearch) => {
    try {
      const skip = (page - 1) * limit;
      let url = `${API_BASE}/db-leads?skip=${skip}&limit=${limit}`;
      if (status && status !== 'all') url += `&status=${status}`;
      if (search) url += `&search=${encodeURIComponent(search)}`;
      
      const res = await fetch(url);
      const data = await res.json();
      setLeads(data.leads || []);
      setTotalLeadsCount(data.total || 0);
    } catch (e) {
      console.error('Failed to fetch leads', e);
    }
  };

  // Re-fetch leads when pagination or filters change
  useEffect(() => {
    fetchLeads(leadsPage, leadsLimit, leadsStatus, leadsSearch);
  }, [leadsPage, leadsLimit, leadsStatus, leadsSearch]);

  const handleAnalyze = async (e) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;

    setIsAnalyzing(true);
    setRunLogs(['[Client] Initiating multi-agent analysis for component finder...']);
    setSelectedId(null);
    
    try {
      const res = await fetch(`${API_BASE}/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ component_desc: searchQuery, refinements: 1 })
      });
      const data = await res.json();
      
      if (data.logs) {
        setRunLogs(data.logs);
      }
      
      await fetchAnalyses();
      if (data.id || (data.report && data.report.id)) {
        const newId = data.id || data.report.id;
        setSelectedId(newId);
      }
      fetchStats();
      fetchLeads();
    } catch (err) {
      setRunLogs((prev) => [...prev, `[Error] Run failed: ${err.message}`]);
    } finally {
      setIsAnalyzing(false);
    }
  };

  const handleDeleteAnalysis = async (id) => {
    if (!confirm('Are you sure you want to delete this analysis and all associated matches?')) return;
    try {
      await fetch(`${API_BASE}/analyses/${id}`, { method: 'DELETE' });
      setSelectedId(null);
      fetchAnalyses();
      fetchStats();
      fetchLeads();
    } catch (e) {
      console.error('Failed to delete analysis', e);
    }
  };

  const handleRematch = async (id) => {
    try {
      await fetch(`${API_BASE}/analyses/${id}/rematch`, { method: 'POST' });
      fetchMatches(id);
    } catch (e) {
      console.error('Failed to rematch', e);
    }
  };

  const triggerCrawl = async () => {
    setIsCrawling(true);
    try {
      await fetch(`${API_BASE}/crawl-step`, { method: 'POST' });
      fetchStats();
      fetchLeads();
    } catch (e) {
      console.error('Crawl failed', e);
    } finally {
      setIsCrawling(false);
    }
  };

  // Convert raw json objects to display format
  const formatJSON = (val) => {
    if (!val) return 'N/A';
    if (typeof val === 'object') return JSON.stringify(val, null, 2);
    try {
      return JSON.stringify(JSON.parse(val), null, 2);
    } catch (e) {
      return String(val);
    }
  };

  const renderSpecVal = (v) => {
    if (v === null || v === undefined) return 'N/A';
    if (typeof v === 'object') {
      if ('value' in v) {
        return v.value !== null && v.value !== undefined ? String(v.value) : 'N/A';
      }
      return JSON.stringify(v);
    }
    return String(v);
  };

  const renderSpecStatus = (v) => {
    if (v && typeof v === 'object' && 'status' in v && v.status) {
      return ` (${v.status})`;
    }
    return '';
  };

  return (
    <div className="app-container">
      {/* Sidebar Navigation */}
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon">AG</div>
          <span className="brand-title">AntiGravity B2B</span>
        </div>
        <ul className="nav-links">
          <li 
            className={`nav-item ${activeTab === 'finder' ? 'active' : ''}`}
            onClick={() => setActiveTab('finder')}
          >
            🔍 Component Finder
          </li>
          <li 
            className={`nav-item ${activeTab === 'leads' ? 'active' : ''}`}
            onClick={() => setActiveTab('leads')}
          >
            📋 Leads Manager
          </li>
          <li 
            className={`nav-item ${activeTab === 'jlcpcb' ? 'active' : ''}`}
            onClick={() => setActiveTab('jlcpcb')}
          >
            🗄️ JLCPCB Parts Hub
          </li>
        </ul>
      </aside>

      {/* Main Content Area */}
      <main className="main-content">
        {activeTab === 'finder' && (
          <div>
            <div className="view-header">
              <h1 className="view-title">Component Finder & Target Alignment</h1>
            </div>

            {/* Analysis Input */}
            <form onSubmit={handleAnalyze} className="search-box">
              <input 
                type="text" 
                className="search-input" 
                placeholder="Enter component name or description (e.g. Samsung K3LKBKB0BM LPDDR5 16GB)..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                disabled={isAnalyzing}
              />
              <button className="btn" type="submit" disabled={isAnalyzing}>
                {isAnalyzing ? 'Analyzing...' : 'Find Matches'}
              </button>
            </form>

            {/* Logs Window */}
            {isAnalyzing && (
              <div className="glass-card" style={{ marginBottom: '24px' }}>
                <h3>Pipeline Logs</h3>
                <div className="terminal">
                  {runLogs.map((log, idx) => (
                    <div key={idx} className="terminal-line">{log}</div>
                  ))}
                </div>
              </div>
            )}

            {/* Main Panel Layout */}
            <div className="pipeline-layout">
              {/* Left Column: Analyses List */}
              <div className="glass-card">
                <h3 style={{ marginBottom: '16px' }}>Saved Analyses</h3>
                <div className="runs-list">
                  {analyses.length === 0 ? (
                    <div style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>No saved runs. Run an analysis above.</div>
                  ) : (
                    analyses.map((run) => (
                      <div 
                        key={run.id} 
                        className={`run-item ${selectedId === run.id ? 'active' : ''}`}
                        onClick={() => setSelectedId(run.id)}
                      >
                        <div className="run-title">{run.component_name}</div>
                        <div className="run-meta">
                          {run.manufacturer || 'Unknown Manufacturer'} • {new Date(run.analyzed_at).toLocaleDateString()}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>

              {/* Right Column: Detailed View */}
              <div className="details-panel">
                {selectedAnalysis ? (
                  <>
                    {/* Header Controls */}
                    <div className="glass-card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <div>
                        <h2>{selectedAnalysis.component_name}</h2>
                        <div style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginTop: '4px' }}>
                          Type: {selectedAnalysis.component_type || 'Unknown'} | Part Number: {selectedAnalysis.part_number || 'N/A'}
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: '12px' }}>
                        <button className="btn btn-secondary" onClick={() => handleRematch(selectedId)}>
                          🔄 Rematch
                        </button>
                        <button className="btn" style={{ background: 'var(--color-danger)', color: '#fff' }} onClick={() => handleDeleteAnalysis(selectedId)}>
                          🗑️ Delete
                        </button>
                      </div>
                    </div>

                    {/* Specifications Card */}
                    <div className="glass-card">
                      <h3>Extracted Specifications</h3>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px', marginTop: '12px' }}>
                        {/* Key Parameters */}
                        {selectedAnalysis.specs?.key_parameters && (
                          <div style={{ background: 'rgba(255,255,255,0.01)', padding: '12px', borderRadius: '8px' }}>
                            <h4 style={{ color: 'var(--color-primary)', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '4px', marginBottom: '8px' }}>Key Parameters</h4>
                            <div className="spec-pills" style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                              {Object.entries(selectedAnalysis.specs.key_parameters).map(([k, v]) => (
                                <div key={k} className="pill" style={{ background: 'rgba(255,255,255,0.03)' }}>
                                  <strong>{k.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase())}:</strong> {renderSpecVal(v)}{renderSpecStatus(v)}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                        
                        {/* Technical Details */}
                        {selectedAnalysis.specs?.technical_details && (
                          <div style={{ background: 'rgba(255,255,255,0.01)', padding: '12px', borderRadius: '8px' }}>
                            <h4 style={{ color: 'var(--color-primary)', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '4px', marginBottom: '8px' }}>Technical Details</h4>
                            <div className="spec-pills" style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                              {Object.entries(selectedAnalysis.specs.technical_details).map(([k, v]) => (
                                <div key={k} className="pill" style={{ background: 'rgba(255,255,255,0.03)' }}>
                                  <strong>{k.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase())}:</strong> {renderSpecVal(v)}{renderSpecStatus(v)}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Currency Signals */}
                        {selectedAnalysis.specs?.currency_signals && (
                          <div style={{ background: 'rgba(255,255,255,0.01)', padding: '12px', borderRadius: '8px' }}>
                            <h4 style={{ color: 'var(--color-primary)', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '4px', marginBottom: '8px' }}>Currency & Lifecycle</h4>
                            <div className="spec-pills" style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                              {Object.entries(selectedAnalysis.specs.currency_signals).map(([k, v]) => (
                                <div key={k} className="pill" style={{ background: 'rgba(255,255,255,0.03)' }}>
                                  <strong>{k.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase())}:</strong> {renderSpecVal(v)}{renderSpecStatus(v)}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Alternatives */}
                      {selectedAnalysis.specs?.standard_alternatives && selectedAnalysis.specs.standard_alternatives.length > 0 && (
                        <div style={{ marginTop: '16px', background: 'rgba(255,255,255,0.01)', padding: '12px', borderRadius: '8px' }}>
                          <h4 style={{ color: 'var(--color-primary)', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '4px', marginBottom: '8px' }}>Standard Alternatives</h4>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                            {selectedAnalysis.specs.standard_alternatives.map((alt, i) => (
                              <span key={i} className="pill" style={{ background: 'rgba(255,255,255,0.05)' }}>{alt}</span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>

                    {/* Matching Target Markets */}
                    <div className="glass-card">
                      <h3>Downstream Target Applications</h3>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '16px', marginTop: '16px' }}>
                        {selectedAnalysis.applications && selectedAnalysis.applications.map((app, idx) => (
                          <div key={idx} className="glass-card" style={{ padding: '16px', background: 'rgba(255,255,255,0.01)' }}>
                            <div className="pill pill-accent" style={{ display: 'inline-block', marginBottom: '8px' }}>
                              HSN {app.product_hsn || app.downstream_finished_product_hsn}
                            </div>
                            <h4 style={{ color: '#fff', marginBottom: '4px' }}>{app.target_product_family || app.hardware_system_board}</h4>
                            {app.subsystem_class && (
                              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '4px' }}>
                                <strong>Subsystem:</strong> {app.subsystem_class}
                              </div>
                            )}
                            {app.buyer_industry_code && (
                              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '4px' }}>
                                <strong>Industry NIC:</strong> {app.buyer_industry_code}
                              </div>
                            )}
                            {app.confidence && (
                              <div style={{ fontSize: '0.8rem', marginTop: '6px' }}>
                                <span style={{ 
                                  fontSize: '0.75rem', 
                                  padding: '2px 6px',
                                  borderRadius: '4px',
                                  background: app.confidence === 'verified_via_web_evidence' ? 'rgba(76, 175, 80, 0.15)' : 'rgba(255, 193, 7, 0.15)',
                                  color: app.confidence === 'verified_via_web_evidence' ? '#4caf50' : '#ffc107',
                                  border: 'none',
                                  display: 'inline-block'
                                }}>
                                  {app.confidence === 'verified_via_web_evidence' ? 'Verified via Web Evidence' : 'Engineering Inference'}
                                </span>
                              </div>
                            )}
                            {app.technical_fit_defense && (
                              <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '6px' }}>{app.technical_fit_defense}</p>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* Match List Table */}
                    <div className="glass-card">
                      <h3>Matched Buyer Leads ({matches.length})</h3>
                      <div className="table-container">
                        {matches.length === 0 ? (
                          <div style={{ padding: '16px 0', color: 'var(--text-muted)' }}>No database leads matching this component's HSN targets.</div>
                        ) : (
                          <table className="leads-table">
                             <thead>
                              <tr>
                                <th>Company Name</th>
                                <th>State</th>
                                <th>Matched HSNs</th>
                                <th>Matched NICs</th>
                                <th>Website</th>
                                <th>Contact details</th>
                              </tr>
                            </thead>
                            <tbody>
                              {matches.map((match) => (
                                <tr key={match.match_id}>
                                  <td style={{ color: '#fff', fontWeight: '600', maxWidth: '300px' }}>
                                    <div>{match.company_name}</div>
                                    {match.company_description && match.company_description !== 'N/A' && (
                                      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 'normal', marginTop: '4px', whiteSpace: 'normal' }}>
                                        {match.company_description.length > 120 ? match.company_description.substring(0, 120) + '...' : match.company_description}
                                      </div>
                                    )}
                                  </td>
                                  <td>{match.state_code || 'N/A'}</td>
                                  <td>
                                    {match.target_hsn_markets && match.target_hsn_markets.length > 0 ? (
                                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', maxWidth: '180px' }}>
                                        {match.target_hsn_markets.map(hsn => (
                                          <span key={hsn} className="pill" style={{ fontSize: '0.75rem', padding: '2px 6px', background: 'rgba(255,255,255,0.08)' }}>{hsn}</span>
                                        ))}
                                      </div>
                                    ) : 'N/A'}
                                  </td>
                                  <td>
                                    {match.industry_nic_codes && match.industry_nic_codes.length > 0 ? (
                                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', maxWidth: '180px' }}>
                                        {match.industry_nic_codes.map(nic => (
                                          <span key={nic} className="pill" style={{ fontSize: '0.75rem', padding: '2px 6px', background: 'rgba(0, 188, 212, 0.15)', color: 'var(--color-primary)' }}>{nic}</span>
                                        ))}
                                      </div>
                                    ) : 'N/A'}
                                  </td>
                                  <td>
                                    {match.website && match.website !== 'N/A' ? (
                                      <a href={match.website} target="_blank" rel="noreferrer" style={{ color: 'var(--color-primary)' }}>
                                        {match.website}
                                      </a>
                                    ) : 'N/A'}
                                  </td>
                                  <td>
                                    <div style={{ fontSize: '0.85rem' }}>
                                      {match.emails && match.emails.length > 0 && <div>📧 {match.emails.join(', ')}</div>}
                                      {match.phones && match.phones.length > 0 && <div>📞 {match.phones.join(', ')}</div>}
                                      {!match.emails?.length && !match.phones?.length && <span style={{ color: 'var(--text-muted)' }}>No contacts</span>}
                                    </div>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        )}
                      </div>
                    </div>

                    {/* Markdown Report */}
                    <div className="glass-card">
                      <h3>Engineering Analysis Report</h3>
                      <div className="markdown-body" style={{ marginTop: '16px', whiteSpace: 'pre-wrap' }}>
                        {selectedAnalysis.report}
                      </div>
                    </div>
                  </>
                ) : (
                  <div className="glass-card" style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)' }}>
                    Select an analysis on the left or search above to load results.
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {activeTab === 'leads' && (
          <div>
            <div className="view-header">
              <h1 className="view-title">Leads Database Manager</h1>
              <div style={{ display: 'flex', gap: '12px' }}>
                <button className="btn" onClick={triggerCrawl} disabled={isCrawling}>
                  {isCrawling ? 'Crawling...' : '⚡ Crawl Next Leads'}
                </button>
              </div>
            </div>

            {/* Statistics Widgets */}
            <div className="dashboard-grid">
              <div className="glass-card stat-card">
                <div className="stat-label">Total Leads</div>
                <div className="stat-val">{stats.total}</div>
              </div>
              <div className="glass-card stat-card">
                <div className="stat-label">Pending Ingestion</div>
                <div className="stat-val" style={{ color: 'var(--color-warning)' }}>{stats.pending}</div>
              </div>
              <div className="glass-card stat-card">
                <div className="stat-label">Crawled</div>
                <div className="stat-val" style={{ color: 'var(--color-secondary)' }}>{stats.crawled}</div>
              </div>
              <div className="glass-card stat-card">
                <div className="stat-label">Synthesized (Active)</div>
                <div className="stat-val" style={{ color: 'var(--color-success)' }}>{stats.synthesized}</div>
              </div>
            </div>

            {/* Leads Table */}
            <div className="glass-card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px', flexWrap: 'wrap', gap: '12px' }}>
                <h3 style={{ margin: 0 }}>Ingested Lead Pipeline</h3>
                
                {/* Search & Filter Controls */}
                <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
                  <input 
                    type="text" 
                    placeholder="Search company name..." 
                    value={leadsSearch}
                    onChange={(e) => { setLeadsSearch(e.target.value); setLeadsPage(1); }}
                    style={{ 
                      padding: '8px 12px', 
                      background: 'rgba(255,255,255,0.05)', 
                      border: '1px solid rgba(255,255,255,0.1)', 
                      borderRadius: '6px', 
                      color: '#fff',
                      fontSize: '0.85rem',
                      width: '200px'
                    }}
                  />
                  
                  <select 
                    value={leadsStatus} 
                    onChange={(e) => { setLeadsStatus(e.target.value); setLeadsPage(1); }}
                    style={{ 
                      padding: '8px 12px', 
                      background: 'rgba(255,255,255,0.05)', 
                      border: '1px solid rgba(255,255,255,0.1)', 
                      borderRadius: '6px', 
                      color: '#fff',
                      fontSize: '0.85rem'
                    }}
                  >
                    <option value="all" style={{ background: '#1e1e2f', color: '#fff' }}>All Statuses</option>
                    <option value="pending" style={{ background: '#1e1e2f', color: '#fff' }}>Pending</option>
                    <option value="crawled" style={{ background: '#1e1e2f', color: '#fff' }}>Crawled</option>
                    <option value="synthesized" style={{ background: '#1e1e2f', color: '#fff' }}>Synthesized</option>
                    <option value="failed" style={{ background: '#1e1e2f', color: '#fff' }}>Failed</option>
                  </select>
                  
                  <select 
                    value={leadsLimit} 
                    onChange={(e) => { setLeadsLimit(Number(e.target.value)); setLeadsPage(1); }}
                    style={{ 
                      padding: '8px 12px', 
                      background: 'rgba(255,255,255,0.05)', 
                      border: '1px solid rgba(255,255,255,0.1)', 
                      borderRadius: '6px', 
                      color: '#fff',
                      fontSize: '0.85rem'
                    }}
                  >
                    <option value={10} style={{ background: '#1e1e2f', color: '#fff' }}>10 per page</option>
                    <option value={25} style={{ background: '#1e1e2f', color: '#fff' }}>25 per page</option>
                    <option value={50} style={{ background: '#1e1e2f', color: '#fff' }}>50 per page</option>
                    <option value={100} style={{ background: '#1e1e2f', color: '#fff' }}>100 per page</option>
                  </select>
                </div>
              </div>

              <div className="table-container">
                <table className="leads-table">
                  <thead>
                    <tr>
                      <th>CIN / ID</th>
                      <th>Company Name</th>
                      <th>State</th>
                      <th>Website</th>
                      <th>Contact Details</th>
                      <th>Status</th>
                      <th>Offerings / Industry</th>
                    </tr>
                  </thead>
                  <tbody>
                    {leads.map((lead) => (
                      <tr key={lead.cin_number}>
                        <td>{lead.cin_number}</td>
                        <td style={{ color: '#fff', fontWeight: '600' }}>{lead.company_name}</td>
                        <td>{lead.state_code || 'N/A'}</td>
                        <td>
                          {lead.website && lead.website !== 'N/A' ? (
                            <a href={lead.website} target="_blank" rel="noreferrer" style={{ color: 'var(--color-primary)' }}>
                              {lead.website}
                            </a>
                          ) : 'N/A'}
                        </td>
                        <td>
                          <div style={{ fontSize: '0.85rem' }}>
                            {lead.emails && lead.emails.length > 0 && <div>📧 {lead.emails.join(', ')}</div>}
                            {lead.phones && lead.phones.length > 0 && <div>📞 {lead.phones.join(', ')}</div>}
                            {!lead.emails?.length && !lead.phones?.length && <span style={{ color: 'var(--text-muted)' }}>No contacts</span>}
                          </div>
                        </td>
                        <td>
                          <span className={`badge badge-${lead.crawl_status}`}>
                            {lead.crawl_status}
                          </span>
                        </td>
                        <td>
                          <div style={{ fontSize: '0.85rem', maxWidth: '300px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {lead.offerings && lead.offerings.length > 0 
                              ? lead.offerings.map(o => (typeof o === 'object' && o !== null ? o.name : o)).join(', ') 
                              : 'N/A'}
                          </div>
                        </td>
                      </tr>
                    ))}
                    {leads.length === 0 && (
                      <tr>
                        <td colSpan="7" style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '24px' }}>
                          No leads found matching the filters.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              {/* Pagination Controls */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '16px', flexWrap: 'wrap', gap: '12px' }}>
                <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                  Showing {leads.length > 0 ? (leadsPage - 1) * leadsLimit + 1 : 0} to {Math.min(leadsPage * leadsLimit, totalLeadsCount)} of {totalLeadsCount} leads
                </div>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <button 
                    className="btn" 
                    disabled={leadsPage === 1}
                    onClick={() => setLeadsPage(prev => Math.max(prev - 1, 1))}
                    style={{ padding: '6px 12px', fontSize: '0.85rem', background: 'rgba(255,255,255,0.05)', color: '#fff', border: '1px solid rgba(255,255,255,0.1)' }}
                  >
                    ◀ Previous
                  </button>
                  <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)', padding: '0 8px' }}>
                    Page {leadsPage} of {Math.ceil(totalLeadsCount / leadsLimit) || 1}
                  </span>
                  <button 
                    className="btn" 
                    disabled={leadsPage >= Math.ceil(totalLeadsCount / leadsLimit)}
                    onClick={() => setLeadsPage(prev => prev + 1)}
                    style={{ padding: '6px 12px', fontSize: '0.85rem', background: 'rgba(255,255,255,0.05)', color: '#fff', border: '1px solid rgba(255,255,255,0.1)' }}
                  >
                    Next ▶
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'jlcpcb' && (
          <div>
            <div className="view-header">
              <h1 className="view-title">JLCPCB Global Parts Data Hub</h1>
            </div>

            {/* Database Status Panel */}
            <div className="glass-card" style={{ marginBottom: '24px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
                <div>
                  <h3 style={{ marginBottom: '4px' }}>Local SQLite Database Ledger</h3>
                  <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>
                    A localized mirror of the JLCPCB parts list for high-volume database matching.
                  </p>
                </div>
                <div>
                  {jlcStatus.status === 'ready' ? (
                    <span className="badge badge-synthesized" style={{ fontSize: '0.9rem', padding: '8px 16px', background: 'rgba(16, 185, 129, 0.15)', color: 'var(--color-success)' }}>
                      Ready: {(jlcStatus.downloaded_bytes / (1024 * 1024)).toFixed(1)} MB
                    </span>
                  ) : jlcStatus.status === 'downloading' ? (
                    <span className="badge badge-crawled" style={{ fontSize: '0.9rem', padding: '8px 16px', background: 'rgba(99, 102, 241, 0.15)', color: 'var(--color-secondary)' }}>
                      Downloading... {jlcStatus.progress_percent}% ({jlcStatus.speed_mbps} Mbps)
                    </span>
                  ) : (
                    <button className="btn" onClick={handleJlcDownload}>
                      📥 Download 1 GB SQLite Mirror
                    </button>
                  )}
                </div>
              </div>

              {jlcStatus.status === 'downloading' && (
                <div style={{ marginTop: '16px' }}>
                  <div style={{ width: '100%', height: '8px', background: 'rgba(255,255,255,0.05)', borderRadius: '4px', overflow: 'hidden' }}>
                    <div style={{ width: `${jlcStatus.progress_percent}%`, height: '100%', background: 'linear-gradient(90deg, var(--color-primary), var(--color-secondary))', transition: 'width 0.5s ease-out' }}></div>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '6px' }}>
                    <span>Downloaded: {(jlcStatus.downloaded_bytes / (1024 * 1024)).toFixed(1)} MB / {(jlcStatus.total_bytes / (1024 * 1024)).toFixed(1)} MB</span>
                    <span>ETA: {jlcStatus.eta_seconds} seconds</span>
                  </div>
                </div>
              )}

              {jlcStatus.error && (
                <div className="glass-card" style={{ marginTop: '16px', background: 'rgba(239, 68, 68, 0.05)', borderColor: 'rgba(239, 68, 68, 0.2)' }}>
                  <span style={{ color: 'var(--color-danger)' }}>Error: {jlcStatus.error}</span>
                </div>
              )}
            </div>

            {jlcStatus.status === 'ready' && (
              <>
                {/* Parametric Filters Card */}
                <div className="glass-card" style={{ marginBottom: '24px' }}>
                  <h3 style={{ marginBottom: '16px' }}>Parametric Query Builder</h3>
                  <form onSubmit={(e) => { e.preventDefault(); setJlcPage(1); queryJlcDb(1); }} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
                    <div>
                      <label style={{ fontSize: '0.8rem', color: 'var(--text-muted)', display: 'block', marginBottom: '6px' }}>Keyword / Description</label>
                      <input 
                        type="text" 
                        placeholder="e.g. STC8G, STM32F..." 
                        value={jlcFilters.keyword}
                        onChange={(e) => setJlcFilters(prev => ({ ...prev, keyword: e.target.value }))}
                        style={{ width: '100%', padding: '8px 12px', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '6px', color: '#fff' }}
                      />
                    </div>
                    <div>
                      <label style={{ fontSize: '0.8rem', color: 'var(--text-muted)', display: 'block', marginBottom: '6px' }}>Manufacturer</label>
                      <input 
                        type="text" 
                        placeholder="e.g. STMicroelectronics..." 
                        value={jlcFilters.manufacturer}
                        onChange={(e) => setJlcFilters(prev => ({ ...prev, manufacturer: e.target.value }))}
                        style={{ width: '100%', padding: '8px 12px', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '6px', color: '#fff' }}
                      />
                    </div>
                    <div>
                      <label style={{ fontSize: '0.8rem', color: 'var(--text-muted)', display: 'block', marginBottom: '6px' }}>Category</label>
                      <input 
                        type="text" 
                        placeholder="e.g. Microcontroller..." 
                        value={jlcFilters.category}
                        onChange={(e) => setJlcFilters(prev => ({ ...prev, category: e.target.value }))}
                        style={{ width: '100%', padding: '8px 12px', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '6px', color: '#fff' }}
                      />
                    </div>
                    <div>
                      <label style={{ fontSize: '0.8rem', color: 'var(--text-muted)', display: 'block', marginBottom: '6px' }}>Min Stock ({jlcFilters.minStock.toLocaleString()})</label>
                      <input 
                        type="range" 
                        min="0" 
                        max="100000" 
                        step="5000"
                        value={jlcFilters.minStock}
                        onChange={(e) => setJlcFilters(prev => ({ ...prev, minStock: Number(e.target.value) }))}
                        style={{ width: '100%', marginTop: '12px' }}
                      />
                    </div>
                    <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                      <button className="btn" type="submit" style={{ width: '100%' }} disabled={isQueryingJlc}>
                        {isQueryingJlc ? 'Searching...' : '🔍 Search Mirror'}
                      </button>
                    </div>
                  </form>
                </div>

                {/* Query Results & Sync Controls */}
                <div className="glass-card">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px', flexWrap: 'wrap', gap: '12px' }}>
                    <div>
                      <h3 style={{ margin: 0 }}>Components Ledger ({jlcTotalCount.toLocaleString()} matches)</h3>
                      <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '2px' }}>
                        Select components to map into the relational database graph.
                      </p>
                    </div>
                    
                    <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Map to HSN:</span>
                        <input 
                          type="text" 
                          value={jlcFilters.hsn}
                          onChange={(e) => setJlcFilters(prev => ({ ...prev, hsn: e.target.value }))}
                          style={{ width: '100px', padding: '6px 10px', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '6px', color: '#fff', fontSize: '0.85rem' }}
                        />
                      </div>
                      
                      <button className="btn" onClick={handleJlcImport} disabled={isSyncingJlc || Object.values(selectedParts).filter(Boolean).length === 0}>
                        {isSyncingJlc ? 'Syncing...' : `📥 Ingest Selected (${Object.values(selectedParts).filter(Boolean).length})`}
                      </button>
                    </div>
                  </div>

                  {syncFeedback && (
                    <div className="glass-card" style={{ marginBottom: '16px', background: syncFeedback.includes('Successfully') ? 'rgba(16, 185, 129, 0.05)' : 'rgba(255,255,255,0.03)', borderColor: syncFeedback.includes('Successfully') ? 'rgba(16, 185, 129, 0.2)' : 'rgba(255,255,255,0.1)', padding: '12px 16px' }}>
                      <span style={{ color: syncFeedback.includes('Successfully') ? 'var(--color-success)' : '#fff', fontSize: '0.9rem' }}>{syncFeedback}</span>
                    </div>
                  )}

                  <div className="table-container">
                    <table className="leads-table">
                      <thead>
                        <tr>
                          <th style={{ width: '40px', textAlign: 'center' }}>
                            <input 
                              type="checkbox" 
                              onChange={(e) => {
                                const checked = e.target.checked;
                                const newSel = {};
                                jlcResults.forEach(p => { newSel[p.part_number] = checked; });
                                setSelectedParts(newSel);
                              }}
                              checked={jlcResults.length > 0 && jlcResults.every(p => selectedParts[p.part_number])}
                            />
                          </th>
                          <th>LCSC Part Number</th>
                          <th>Manufacturer</th>
                          <th>Category</th>
                          <th>Package</th>
                          <th>Stock Count</th>
                          <th>Price / Description</th>
                        </tr>
                      </thead>
                      <tbody>
                        {jlcResults.map((part) => (
                          <tr key={part.part_number}>
                            <td style={{ textAlign: 'center' }}>
                              <input 
                                type="checkbox" 
                                checked={!!selectedParts[part.part_number]} 
                                onChange={(e) => {
                                  setSelectedParts(prev => ({ ...prev, [part.part_number]: e.target.checked }));
                                }}
                              />
                            </td>
                            <td style={{ color: 'var(--color-primary)', fontWeight: '600' }}>{part.part_number}</td>
                            <td style={{ color: '#fff' }}>{part.manufacturer}</td>
                            <td>{part.second_category || part.first_category || 'N/A'}</td>
                            <td><span className="pill" style={{ background: 'rgba(255,255,255,0.05)', fontSize: '0.8rem' }}>{part.package || 'N/A'}</span></td>
                            <td style={{ color: 'var(--color-success)', fontWeight: '600' }}>{(part.stock || 0).toLocaleString()}</td>
                            <td style={{ maxWidth: '300px' }}>
                              <div style={{ fontSize: '0.85rem', color: '#fff', fontWeight: '500' }}>Price: {part.price || 'N/A'}</div>
                              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: '2px' }} title={part.description}>
                                {part.description || 'N/A'}
                              </div>
                            </td>
                          </tr>
                        ))}
                        {jlcResults.length === 0 && (
                          <tr>
                            <td colSpan="7" style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '40px' }}>
                              {isQueryingJlc ? 'Searching parts list...' : 'No components found. Refine your query filters.'}
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>

                  {/* Pagination */}
                  {jlcTotalCount > jlcLimit && (
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '16px', flexWrap: 'wrap', gap: '12px' }}>
                      <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                        Showing {(jlcPage - 1) * jlcLimit + 1} to {Math.min(jlcPage * jlcLimit, jlcTotalCount)} of {jlcTotalCount.toLocaleString()} parts
                      </div>
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        <button 
                          className="btn" 
                          disabled={jlcPage === 1}
                          onClick={() => setJlcPage(prev => Math.max(prev - 1, 1))}
                          style={{ padding: '6px 12px', fontSize: '0.85rem', background: 'rgba(255,255,255,0.05)', color: '#fff', border: '1px solid rgba(255,255,255,0.1)' }}
                        >
                          ◀ Previous
                        </button>
                        <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)', padding: '0 8px' }}>
                          Page {jlcPage} of {Math.ceil(jlcTotalCount / jlcLimit) || 1}
                        </span>
                        <button 
                          className="btn" 
                          disabled={jlcPage >= Math.ceil(jlcTotalCount / jlcLimit)}
                          onClick={() => setJlcPage(prev => prev + 1)}
                          style={{ padding: '6px 12px', fontSize: '0.85rem', background: 'rgba(255,255,255,0.05)', color: '#fff', border: '1px solid rgba(255,255,255,0.1)' }}
                        >
                          Next ▶
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

export default App;

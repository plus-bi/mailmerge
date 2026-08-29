import React, { useEffect, useState, useRef } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

type Profile = {
  id: string;
  name: string;
  smtp_host: string;
  smtp_port: number;
  daily_cap: number;
  delay_seconds: number;
};

type Campaign = {
  id: string;
  name: string;
  purpose: string;
  profile_id: string | null;
  from_name: string;
  from_address: string;
  reply_to: string | null;
  subject_template: string;
  body_mode: string;
  body_template: string;
  state: string;
  delay_seconds: number | null;
  working_hours_enabled: boolean;
  working_hours_start: number;
  working_hours_end: number;
  working_hours_timezone: string;
  consent_acknowledged: boolean;
  suppression_synced: boolean;
  unsubscribe_base_url: string | null;
  scheduled_at: string | null;
};

type Recipient = {
  id: string;
  email: string;
  values: Record<string, any>;
  valid: boolean;
  included: boolean;
  validation_error: string | null;
  suppressed: boolean;
  status: string;
};

type PreviewData = {
  recipient_id: string;
  email: string;
  subject: string;
  html: string;
  text: string;
  values: Record<string, any>;
  missing_variables: string[];
};

type PreflightResult = {
  ok: boolean;
  errors: string[];
  previews: PreviewData[];
  excluded: number;
  attachments: { name: string; size: number }[];
};

const token = new URLSearchParams(location.hash.slice(1)).get('token') || localStorage.getItem('mailmerge-token') || '';
if (token) localStorage.setItem('mailmerge-token', token);

const api = async (path: string, init: RequestInit = {}) => {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    ...(init.headers as Record<string, string> || {}),
  };
  if (!(init.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }
  const res = await fetch('/api/v1' + path, { ...init, headers });
  if (!res.ok) {
    const errorText = await res.text();
    let detail = errorText;
    try {
      const parsed = JSON.parse(errorText);
      detail = parsed.detail || parsed.message || errorText;
      if (Array.isArray(detail)) detail = detail.join('\n');
    } catch {}
    throw new Error(detail);
  }
  return res.json();
};

function App() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Campaign | null>(null);
  const [recipients, setRecipients] = useState<Recipient[]>([]);
  const [activeTab, setActiveTab] = useState<'template' | 'recipients' | 'preview' | 'send'>('template');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Form State
  const [form, setForm] = useState<Partial<Campaign>>({});
  const [jsonInput, setJsonInput] = useState('');
  const [testEmailAddress, setTestEmailAddress] = useState('');
  const [selectedPreviewRecipientId, setSelectedPreviewRecipientId] = useState<string>('');
  const [previewContent, setPreviewContent] = useState<PreviewData | null>(null);
  const [preflightData, setPreflightData] = useState<PreflightResult | null>(null);
  const [eventCounts, setEventCounts] = useState<Record<string, number>>({});

  const eventSourceRef = useRef<EventSource | null>(null);

  const notify = (msg: string, isError = false) => {
    if (isError) {
      setError(msg);
      setSuccess(null);
    } else {
      setSuccess(msg);
      setError(null);
    }
    setTimeout(() => {
      setError(null);
      setSuccess(null);
    }, 6000);
  };

  const loadCampaigns = async () => {
    try {
      const data = await api('/campaigns');
      setCampaigns(data);
      if (!selectedId && data.length > 0) {
        setSelectedId(data[0].id);
      }
    } catch (e: any) {
      notify(e.message, true);
    }
  };

  const loadProfiles = async () => {
    try {
      const data = await api('/profiles');
      setProfiles(data);
    } catch (e: any) {
      notify(e.message, true);
    }
  };

  const loadSelectedCampaign = async (id: string) => {
    try {
      const camp: Campaign = await api(`/campaigns/${id}`);
      setSelected(camp);
      setForm(camp);
      loadRecipients(id);
    } catch (e: any) {
      notify(e.message, true);
    }
  };

  const loadRecipients = async (id: string) => {
    try {
      const data: Recipient[] = await api(`/campaigns/${id}/recipients`);
      setRecipients(data);
      if (data.length > 0 && !selectedPreviewRecipientId) {
        setSelectedPreviewRecipientId(data[0].id);
      }
    } catch (e: any) {
      notify(e.message, true);
    }
  };

  useEffect(() => {
    if (token) {
      void loadCampaigns();
      void loadProfiles();
    }
  }, []);

  useEffect(() => {
    if (selectedId) {
      void loadSelectedCampaign(selectedId);
      setupSSE(selectedId);
    }
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, [selectedId]);

  const setupSSE = (campaignId: string) => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }
    const es = new EventSource(`/api/v1/campaigns/${campaignId}/events`);
    es.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.counts) setEventCounts(payload.counts);
      } catch {}
    };
    eventSourceRef.current = es;
  };

  // Load preview when recipient selection or tab changes
  useEffect(() => {
    if (activeTab === 'preview' && selectedId && selectedPreviewRecipientId) {
      api(`/campaigns/${selectedId}/preview/${selectedPreviewRecipientId}`)
        .then(setPreviewContent)
        .catch((e) => notify(`Preview failed: ${e.message}`, true));
    }
  }, [activeTab, selectedId, selectedPreviewRecipientId]);

  const handleCreateCampaign = async () => {
    const name = prompt('Campaign Name:');
    if (!name) return;
    try {
      const newCamp = await api('/campaigns', {
        method: 'POST',
        body: JSON.stringify({
          name,
          profile_id: profiles[0]?.id || null,
          subject_template: 'Hello {{ first_name }}',
          body_template: 'Hi {{ first_name }},\n\nWe are excited to reach out!\n\nBest,\nTeam',
          body_mode: 'markdown',
          working_hours_enabled: false,
          working_hours_start: 9,
          working_hours_end: 17,
          working_hours_timezone: 'UTC',
        }),
      });
      await loadCampaigns();
      setSelectedId(newCamp.id);
      notify('Campaign created.');
    } catch (e: any) {
      notify(e.message, true);
    }
  };

  const handleSaveCampaign = async () => {
    if (!selected) return;
    try {
      const updated = await api(`/campaigns/${selected.id}`, {
        method: 'PUT',
        body: JSON.stringify(form),
      });
      setSelected(updated);
      setForm(updated);
      await loadCampaigns();
      notify('Campaign saved successfully.');
    } catch (e: any) {
      notify(e.message, true);
    }
  };

  const handleImportJson = async () => {
    if (!selected) return;
    try {
      let parsed;
      try {
        parsed = JSON.parse(jsonInput);
      } catch {
        throw new Error('Invalid JSON format. Please ensure valid JSON array or object.');
      }
      const result = await api(`/campaigns/${selected.id}/recipients`, {
        method: 'POST',
        body: JSON.stringify(parsed),
      });
      await loadRecipients(selected.id);
      notify(`Imported ${result.imported} recipients (${result.valid} valid, ${result.duplicates} duplicates).`);
    } catch (e: any) {
      notify(e.message, true);
    }
  };

  const handleRunPreflight = async () => {
    if (!selected) return;
    try {
      const result: PreflightResult = await api(`/campaigns/${selected.id}/preflight`, {
        method: 'POST',
      });
      setPreflightData(result);
      if (result.ok) {
        notify(`Preflight passed! ${result.previews.length} messages ready for dispatch.`);
      } else {
        notify(`Preflight failed with ${result.errors.length} issue(s).`, true);
      }
    } catch (e: any) {
      notify(e.message, true);
    }
  };

  const handleSendTestEmail = async () => {
    if (!selected || !testEmailAddress) {
      notify('Please enter a test email address.', true);
      return;
    }
    try {
      await api(`/campaigns/${selected.id}/test-email`, {
        method: 'POST',
        body: JSON.stringify({
          recipient_email: testEmailAddress,
          sample_recipient_id: selectedPreviewRecipientId || null,
        }),
      });
      notify(`Test email successfully sent to ${testEmailAddress}!`);
    } catch (e: any) {
      notify(e.message, true);
    }
  };

  const handleSyncSuppressions = async () => {
    if (!selected) return;
    try {
      const res = await api(`/campaigns/${selected.id}/suppression/sync`, { method: 'POST' });
      notify(`Sync completed: ${res.synced_events} events processed.`);
      loadRecipients(selected.id);
    } catch (e: any) {
      notify(e.message, true);
    }
  };

  const handleScheduleCampaign = async () => {
    if (!selected) return;
    try {
      await api(`/campaigns/${selected.id}/schedule`, {
        method: 'POST',
        body: JSON.stringify({
          scheduled_at: new Date().toISOString(),
          confirm_guardrail_override: false,
        }),
      });
      notify('Campaign scheduled and launched!');
      loadSelectedCampaign(selected.id);
      loadCampaigns();
    } catch (e: any) {
      notify(e.message, true);
    }
  };

  const handleControlCampaign = async (action: string) => {
    if (!selected) return;
    try {
      await api(`/campaigns/${selected.id}/${action}`, { method: 'POST' });
      notify(`Campaign state set to ${action}.`);
      loadSelectedCampaign(selected.id);
      loadCampaigns();
    } catch (e: any) {
      notify(e.message, true);
    }
  };

  const loadSampleJson = () => {
    setJsonInput(
      JSON.stringify(
        [
          { email: 'alex@example.com', first_name: 'Alex', company: 'Acme Corp', role: 'CTO' },
          { email: 'sarah@example.com', first_name: 'Sarah', company: 'Globex Inc', role: 'VP Engineering' },
          { email: 'jordan@example.com', first_name: 'Jordan', company: 'Soylent Ltd', role: 'Product Lead' },
        ],
        null,
        2
      )
    );
  };

  if (!token) {
    return (
      <main>
        <h1>Local Mail Merge</h1>
        <p>
          Please provide a session token via <code>#token=...</code> in the URL.
        </p>
      </main>
    );
  }

  const totalRecipients = recipients.length;
  const sentCount = eventCounts['sent'] || 0;
  const progressPercent = totalRecipients > 0 ? Math.round((sentCount / totalRecipients) * 100) : 0;

  return (
    <main>
      <header>
        <div>
          <h1>Local Mail Merge</h1>
          <p>Privacy-first bulk email delivery engine</p>
        </div>
        <div style={{ display: 'flex', gap: '10px' }}>
          <button onClick={handleCreateCampaign}>+ New Campaign</button>
        </div>
      </header>

      {error && <div className="toast-error">{error}</div>}
      {success && <div className="toast-success">{success}</div>}

      <div className="layout">
        <aside>
          <div style={{ fontWeight: 600, fontSize: '0.85rem', color: '#5e6b62', padding: '4px 8px' }}>CAMPAIGNS</div>
          {campaigns.map((c) => (
            <button
              key={c.id}
              className={`campaign-item ${selectedId === c.id ? 'active' : ''}`}
              onClick={() => setSelectedId(c.id)}
            >
              <div className="title">{c.name}</div>
              <div className="meta">
                <span>{c.purpose}</span>
                <span className={`badge badge-${c.state}`}>{c.state}</span>
              </div>
            </button>
          ))}
          {campaigns.length === 0 && <p style={{ padding: '8px', color: '#778' }}>No campaigns yet.</p>}
        </aside>

        <section className="content-area">
          {selected ? (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                <div>
                  <h2 style={{ margin: 0 }}>{selected.name}</h2>
                  <span className={`badge badge-${selected.state}`} style={{ marginTop: '4px' }}>
                    {selected.state}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: '8px' }}>
                  {selected.state === 'draft' && (
                    <button onClick={handleScheduleCampaign}>🚀 Launch Campaign</button>
                  )}
                  {selected.state === 'sending' && (
                    <button className="secondary" onClick={() => handleControlCampaign('pause')}>
                      ⏸ Pause
                    </button>
                  )}
                  {selected.state === 'paused' && (
                    <button onClick={() => handleControlCampaign('resume')}>▶ Resume</button>
                  )}
                  {(selected.state === 'sending' || selected.state === 'scheduled') && (
                    <button className="danger" onClick={() => handleControlCampaign('cancel')}>
                      ✕ Cancel
                    </button>
                  )}
                </div>
              </div>

              {/* Progress bar during delivery */}
              {(selected.state === 'sending' || selected.state === 'completed' || selected.state === 'paused') && (
                <div className="card" style={{ marginBottom: '20px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', fontWeight: 600 }}>
                    <span>Dispatch Progress</span>
                    <span>
                      {sentCount} / {totalRecipients} ({progressPercent}%)
                    </span>
                  </div>
                  <div className="progress-bar-container">
                    <div className="progress-fill" style={{ width: `${progressPercent}%` }}></div>
                  </div>
                </div>
              )}

              {/* Navigation Tabs */}
              <div className="tabs">
                <button
                  className={`tab-btn ${activeTab === 'template' ? 'active' : ''}`}
                  onClick={() => setActiveTab('template')}
                >
                  📝 Setup & Template
                </button>
                <button
                  className={`tab-btn ${activeTab === 'recipients' ? 'active' : ''}`}
                  onClick={() => setActiveTab('recipients')}
                >
                  👥 Recipients ({recipients.length})
                </button>
                <button
                  className={`tab-btn ${activeTab === 'preview' ? 'active' : ''}`}
                  onClick={() => setActiveTab('preview')}
                >
                  👁 Live Previews
                </button>
                <button
                  className={`tab-btn ${activeTab === 'send' ? 'active' : ''}`}
                  onClick={() => setActiveTab('send')}
                >
                  🚀 Preflight & Send
                </button>
              </div>

              {/* TAB 1: Setup & Template */}
              {activeTab === 'template' && (
                <div>
                  <div className="form-grid">
                    <div className="form-group">
                      <label>Campaign Name</label>
                      <input
                        type="text"
                        value={form.name || ''}
                        onChange={(e) => setForm({ ...form, name: e.target.value })}
                      />
                    </div>
                    <div className="form-group">
                      <label>Sender Profile</label>
                      <select
                        value={form.profile_id || ''}
                        onChange={(e) => setForm({ ...form, profile_id: e.target.value })}
                      >
                        <option value="">Select a profile...</option>
                        {profiles.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name} ({p.smtp_host}:{p.smtp_port})
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="form-group">
                      <label>From Name</label>
                      <input
                        type="text"
                        value={form.from_name || ''}
                        onChange={(e) => setForm({ ...form, from_name: e.target.value })}
                      />
                    </div>
                    <div className="form-group">
                      <label>From Address</label>
                      <input
                        type="email"
                        value={form.from_address || ''}
                        onChange={(e) => setForm({ ...form, from_address: e.target.value })}
                      />
                    </div>
                    <div className="form-group">
                      <label>Reply-To Address</label>
                      <input
                        type="email"
                        placeholder="e.g. replies@yourdomain.com"
                        value={form.reply_to || ''}
                        onChange={(e) => setForm({ ...form, reply_to: e.target.value })}
                      />
                    </div>
                    <div className="form-group">
                      <label>Body Mode</label>
                      <select
                        value={form.body_mode || 'markdown'}
                        onChange={(e) => setForm({ ...form, body_mode: e.target.value })}
                      >
                        <option value="markdown">Markdown</option>
                        <option value="html">Raw HTML</option>
                      </select>
                    </div>
                  </div>

                  <div className="form-group">
                    <label>Subject Template (Jinja2)</label>
                    <input
                      type="text"
                      placeholder="e.g. Invitation for {{ first_name }} - {{ company }}"
                      value={form.subject_template || ''}
                      onChange={(e) => setForm({ ...form, subject_template: e.target.value })}
                    />
                  </div>

                  <div className="form-group">
                    <label>Body Template (Jinja2)</label>
                    <textarea
                      rows={8}
                      className="code"
                      placeholder="e.g. Hi {{ first_name }},&#10;&#10;We would love to discuss {{ project_name }} with you."
                      value={form.body_template || ''}
                      onChange={(e) => setForm({ ...form, body_template: e.target.value })}
                    />
                  </div>

                  {/* Delivery Pacing & Working Hours */}
                  <div className="card">
                    <h3 style={{ margin: '0 0 12px', fontSize: '1rem' }}>⏱ Delivery Pacing & Working Hours</h3>
                    <div className="form-grid">
                      <div className="form-group">
                        <label>Delay Between Emails (Seconds)</label>
                        <input
                          type="number"
                          placeholder="e.g. 144 (spreads 200 emails over 8 hours)"
                          value={form.delay_seconds ?? ''}
                          onChange={(e) =>
                            setForm({
                              ...form,
                              delay_seconds: e.target.value ? parseInt(e.target.value) : null,
                            })
                          }
                        />
                      </div>
                      <div className="form-group">
                        <label>Timezone</label>
                        <input
                          type="text"
                          value={form.working_hours_timezone || 'UTC'}
                          onChange={(e) => setForm({ ...form, working_hours_timezone: e.target.value })}
                        />
                      </div>
                      <div className="form-group full">
                        <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                          <input
                            type="checkbox"
                            checked={form.working_hours_enabled || false}
                            onChange={(e) => setForm({ ...form, working_hours_enabled: e.target.checked })}
                          />
                          Restricted to Working Hours (Monday – Friday only)
                        </label>
                      </div>
                      {form.working_hours_enabled && (
                        <>
                          <div className="form-group">
                            <label>Start Hour (0–23)</label>
                            <input
                              type="number"
                              min={0}
                              max={23}
                              value={form.working_hours_start ?? 9}
                              onChange={(e) => setForm({ ...form, working_hours_start: parseInt(e.target.value) })}
                            />
                          </div>
                          <div className="form-group">
                            <label>End Hour (0–23)</label>
                            <input
                              type="number"
                              min={0}
                              max={23}
                              value={form.working_hours_end ?? 17}
                              onChange={(e) => setForm({ ...form, working_hours_end: parseInt(e.target.value) })}
                            />
                          </div>
                        </>
                      )}
                    </div>
                  </div>

                  <button onClick={handleSaveCampaign}>💾 Save Campaign Configuration</button>
                </div>
              )}

              {/* TAB 2: JSON Recipient Import */}
              {activeTab === 'recipients' && (
                <div>
                  <div className="card">
                    <h3 style={{ margin: '0 0 8px', fontSize: '1rem' }}>📥 Batch JSON Recipient Ingestion</h3>
                    <p style={{ margin: '0 0 12px', fontSize: '0.85rem', color: '#5e6b62' }}>
                      Paste a JSON array of recipients with their custom template variables, or upload a JSON file. All template variables are strictly validated.
                    </p>
                    <textarea
                      rows={7}
                      className="code"
                      placeholder='[&#10;  {"email": "alice@example.com", "first_name": "Alice", "company": "Acme"},&#10;  {"email": "bob@example.com", "first_name": "Bob", "company": "Globex"}&#10;]'
                      value={jsonInput}
                      onChange={(e) => setJsonInput(e.target.value)}
                    />
                    <div style={{ display: 'flex', gap: '10px', marginTop: '12px' }}>
                      <button onClick={handleImportJson}>Import JSON Recipients</button>
                      <button className="secondary" onClick={loadSampleJson}>
                        Load Example JSON
                      </button>
                    </div>
                  </div>

                  <div className="table-wrapper">
                    <table>
                      <thead>
                        <tr>
                          <th>Email</th>
                          <th>Status</th>
                          <th>Validity</th>
                          <th>Payload Variables (`values`)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {recipients.map((r) => (
                          <tr key={r.id}>
                            <td><strong>{r.email}</strong></td>
                            <td>
                              <span className={`badge badge-${r.status}`}>{r.status}</span>
                            </td>
                            <td>
                              {r.valid ? (
                                <span className="badge badge-valid">Valid</span>
                              ) : (
                                <span className="badge badge-invalid" title={r.validation_error || 'Invalid'}>
                                  {r.validation_error || 'Invalid'}
                                </span>
                              )}
                            </td>
                            <td>
                              <code>{JSON.stringify(r.values)}</code>
                            </td>
                          </tr>
                        ))}
                        {recipients.length === 0 && (
                          <tr>
                            <td colSpan={4} style={{ textAlign: 'center', color: '#888' }}>
                              No recipients imported yet.
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* TAB 3: Live Previews */}
              {activeTab === 'preview' && (
                <div>
                  <div className="form-group" style={{ maxWidth: '400px', marginBottom: '20px' }}>
                    <label>Select Recipient to Preview</label>
                    <select
                      value={selectedPreviewRecipientId}
                      onChange={(e) => setSelectedPreviewRecipientId(e.target.value)}
                    >
                      {recipients.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.email} {!r.valid ? '(Invalid / Missing Vars)' : ''}
                        </option>
                      ))}
                    </select>
                  </div>

                  {previewContent ? (
                    <div className="preview-container">
                      <div>
                        <h4>Recipient Values</h4>
                        <pre style={{ background: '#f4f6f3', padding: '12px', borderRadius: '6px', fontSize: '0.85rem' }}>
                          {JSON.stringify(previewContent.values, null, 2)}
                        </pre>
                        {previewContent.missing_variables && previewContent.missing_variables.length > 0 && (
                          <div className="toast-error">
                            ⚠️ Missing required template variables: {previewContent.missing_variables.join(', ')}
                          </div>
                        )}
                      </div>
                      <div>
                        <div style={{ marginBottom: '12px' }}>
                          <label style={{ display: 'block', fontSize: '0.8rem', color: '#5e6b62' }}>SUBJECT</label>
                          <div style={{ fontWeight: 600, fontSize: '1.1rem' }}>{previewContent.subject}</div>
                        </div>
                        <label style={{ display: 'block', fontSize: '0.8rem', color: '#5e6b62', marginBottom: '6px' }}>
                          RENDERED HTML OUTPUT
                        </label>
                        <div
                          className="preview-body"
                          dangerouslySetInnerHTML={{ __html: previewContent.html }}
                        />
                      </div>
                    </div>
                  ) : (
                    <p>Select a recipient to inspect their fully personalized rendered email.</p>
                  )}
                </div>
              )}

              {/* TAB 4: Preflight, Test Email, & Send */}
              {activeTab === 'send' && (
                <div>
                  <div className="card">
                    <h3 style={{ margin: '0 0 12px', fontSize: '1rem' }}>🔍 Preflight & Template Variable Verification</h3>
                    <p style={{ margin: '0 0 12px', fontSize: '0.85rem', color: '#5e6b62' }}>
                      Validates that all recipients have populated variables, checks message size limits, and verifies profile readiness.
                    </p>
                    <button onClick={handleRunPreflight}>Run Full Preflight Check</button>

                    {preflightData && (
                      <div style={{ marginTop: '16px' }}>
                        {preflightData.ok ? (
                          <div className="toast-success">
                            ✓ Preflight Check Passed: {preflightData.previews.length} ready recipients.
                          </div>
                        ) : (
                          <div className="toast-error">
                            <strong>Validation Issues:</strong>
                            <ul style={{ margin: '6px 0 0', paddingLeft: '20px' }}>
                              {preflightData.errors.map((err, idx) => (
                                <li key={idx}>{err}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  <div className="card">
                    <h3 style={{ margin: '0 0 12px', fontSize: '1rem' }}>✉️ Send Test Email</h3>
                    <p style={{ margin: '0 0 12px', fontSize: '0.85rem', color: '#5e6b62' }}>
                      Send an exact rendered test email to your own inbox to inspect styling and headers before launching to recipients.
                    </p>
                    <div style={{ display: 'flex', gap: '10px', maxWidth: '500px' }}>
                      <input
                        type="email"
                        placeholder="your-email@example.com"
                        value={testEmailAddress}
                        onChange={(e) => setTestEmailAddress(e.target.value)}
                        style={{ flex: 1 }}
                      />
                      <button onClick={handleSendTestEmail}>Send Test Email</button>
                    </div>
                  </div>

                  <div className="card">
                    <h3 style={{ margin: '0 0 12px', fontSize: '1rem' }}>🔄 Unsubscribe Suppression Sync</h3>
                    <p style={{ margin: '0 0 12px', fontSize: '0.85rem', color: '#5e6b62' }}>
                      Synchronize recent opt-out events recorded by the unsubscribe service to update the suppression list.
                    </p>
                    <button className="secondary" onClick={handleSyncSuppressions}>
                      Sync Unsubscribe List Now
                    </button>
                  </div>
                </div>
              )}
            </>
          ) : (
            <p>Select a campaign from the sidebar or click "+ New Campaign".</p>
          )}
        </section>
      </div>

      <footer>Local Mail Merge · Privacy-First & Lightweight Email Dispatcher</footer>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<App />);

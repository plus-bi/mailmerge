import React, { useEffect, useState, useRef } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ClerkProvider,
  Show,
  SignInButton,
  SignUpButton,
  UserButton,
  useAuth,
} from '@clerk/react';
import './style.css';

type Profile = {
  id: string;
  name: string;
  from_name: string | null;
  from_address: string | null;
  smtp_host: string;
  smtp_port: number;
  security: 'starttls' | 'tls' | 'none';
  verify_tls: boolean;
  username: string | null;
  auth_type: string;
  daily_cap: number;
  delay_seconds: number;
  max_message_bytes: number;
  reply_to: string | null;
  list_unsubscribe?: string | null;
  list_unsubscribe_one_click?: boolean;
  working_hours_enabled: boolean;
  working_hours_start: number;
  working_hours_end: number;
  working_hours_timezone: string;
  imap_host: string | null;
  imap_port: number | null;
  imap_security: 'starttls' | 'tls' | 'none' | null;
};

type ProfileDraft = Omit<Profile, 'id'> & { password: string };

const blankProfile = (): ProfileDraft => ({
  name: '',
  from_name: '',
  from_address: '',
  smtp_host: '',
  smtp_port: 587,
  security: 'starttls',
  verify_tls: true,
  username: '',
  auth_type: 'password',
  password: '',
  daily_cap: 250,
  delay_seconds: 2,
  max_message_bytes: 20000000,
  reply_to: '',
  working_hours_enabled: false,
  working_hours_start: 9,
  working_hours_end: 17,
  working_hours_timezone: 'UTC',
  imap_host: '',
  imap_port: null,
  imap_security: null,
});

const profileToDraft = (profile: Profile): ProfileDraft => ({
  name: profile.name,
  from_name: profile.from_name ?? '',
  from_address: profile.from_address ?? '',
  smtp_host: profile.smtp_host,
  smtp_port: profile.smtp_port,
  security: profile.security,
  verify_tls: profile.verify_tls,
  username: profile.username ?? '',
  auth_type: profile.auth_type,
  password: '',
  daily_cap: profile.daily_cap,
  delay_seconds: profile.delay_seconds,
  max_message_bytes: profile.max_message_bytes,
  reply_to: profile.reply_to ?? '',
  working_hours_enabled: profile.working_hours_enabled,
  working_hours_start: profile.working_hours_start,
  working_hours_end: profile.working_hours_end,
  working_hours_timezone: profile.working_hours_timezone,
  imap_host: profile.imap_host ?? '',
  imap_port: profile.imap_port,
  imap_security: profile.imap_security,
});

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
  list_unsubscribe_enabled: boolean;
  unsubscribe_base_url: string | null;
  scheduled_at: string | null;
};

type CampaignStatus = {
  id: string;
  name: string;
  state: string;
  scheduled_at: string;
  counts: Record<string, number>;
  total: number;
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

type UnsubscribeConfig = {
  signing_secret_configured: boolean;
  domain: string | null;
  default_base_url: string;
};

function Dashboard() {
  const { getToken } = useAuth();

  const api = async (path: string, init: RequestInit = {}) => {
    const clerkToken = await getToken();
    const headers: Record<string, string> = {
      ...(clerkToken ? { Authorization: `Bearer ${clerkToken}` } : {}),
      ...((init.headers as Record<string, string>) || {}),
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
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Campaign | null>(null);
  const [recipients, setRecipients] = useState<Recipient[]>([]);
  const [campaignStatuses, setCampaignStatuses] = useState<CampaignStatus[]>([]);
  const [statusLoading, setStatusLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<'template' | 'recipients' | 'preview' | 'send' | 'status'>('template');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [unsubConfig, setUnsubConfig] = useState<UnsubscribeConfig | null>(null);

  // Form & Ingestion State
  const [form, setForm] = useState<Partial<Campaign>>({});
  const [jsonInput, setJsonInput] = useState('');
  const [uploadedFileName, setUploadedFileName] = useState<string | null>(null);
  const [uploadedFileSize, setUploadedFileSize] = useState<number | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [testEmailAddress, setTestEmailAddress] = useState('');
  const [selectedPreviewRecipientId, setSelectedPreviewRecipientId] = useState<string>('');
  const [previewContent, setPreviewContent] = useState<PreviewData | null>(null);
  const [preflightData, setPreflightData] = useState<PreflightResult | null>(null);
  const [eventCounts, setEventCounts] = useState<Record<string, number>>({});
  const [profileManagerOpen, setProfileManagerOpen] = useState(false);
  const [editingProfileId, setEditingProfileId] = useState<string | null>(null);
  const [profileForm, setProfileForm] = useState<ProfileDraft>(blankProfile());
  const [profileBusy, setProfileBusy] = useState(false);
  const [profileTesting, setProfileTesting] = useState(false);
  const profileFileInputRef = useRef<HTMLInputElement | null>(null);

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

  const loadCampaignStatuses = async () => {
    setStatusLoading(true);
    try {
      const data: CampaignStatus[] = await api('/campaigns/status');
      setCampaignStatuses(data);
    } catch (e: any) {
      notify(e.message, true);
    } finally {
      setStatusLoading(false);
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

  const loadUnsubConfig = async () => {
    try {
      const data = await api('/unsubscribe-config');
      setUnsubConfig(data);
    } catch {}
  };

  const openNewProfile = () => {
    setEditingProfileId(null);
    setProfileForm(blankProfile());
    setProfileManagerOpen(true);
  };

  const editProfile = (profile: Profile) => {
    setEditingProfileId(profile.id);
    setProfileForm(profileToDraft(profile));
  };

  const buildProfilePayload = () => {
    const { password, ...profileValues } = profileForm;
    return {
      ...profileValues,
      name: profileForm.name.trim(),
      from_name: profileForm.from_name?.trim() || null,
      from_address: profileForm.from_address?.trim() || null,
      smtp_host: profileForm.smtp_host.trim(),
      username: profileForm.username?.trim() || null,
      reply_to: profileForm.reply_to?.trim() || null,
      imap_host: profileForm.imap_host?.trim() || null,
      imap_port: profileForm.imap_host ? profileForm.imap_port : null,
      imap_security: profileForm.imap_host ? profileForm.imap_security : null,
      password: profileForm.auth_type === 'password' ? password || null : null,
      access_token: profileForm.auth_type === 'xoauth2' ? password || null : null,
    };
  };

  const handleSaveProfile = async () => {
    setProfileBusy(true);
    try {
      const payload = buildProfilePayload();
      const saved: Profile = await api(editingProfileId ? `/profiles/${editingProfileId}` : '/profiles', {
        method: editingProfileId ? 'PUT' : 'POST',
        body: JSON.stringify(payload),
      });
      await api('/profile-config', { method: 'PUT' });
      await loadProfiles();
      setEditingProfileId(saved.id);
      setProfileForm(profileToDraft(saved));
      notify(`Sender profile "${saved.name}" saved to profiles.toml.`);
    } catch (e: any) {
      notify(e.message, true);
    } finally {
      setProfileBusy(false);
    }
  };

  const handleTestProfileConnection = async () => {
    setProfileTesting(true);
    try {
      const result = await api('/profiles/test-connection', {
        method: 'POST',
        body: JSON.stringify({ ...buildProfilePayload(), profile_id: editingProfileId }),
      });
      notify(result.message);
    } catch (e: any) {
      notify(e.message, true);
    } finally {
      setProfileTesting(false);
    }
  };

  const handleLoadProfileFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setProfileBusy(true);
    try {
      const content = await file.text();
      const loaded: Profile[] = await api('/profile-config', {
        method: 'POST',
        body: JSON.stringify({ content }),
      });
      await loadProfiles();
      if (loaded[0]) editProfile(loaded[0]);
      notify(`Loaded ${loaded.length} sender profile${loaded.length === 1 ? '' : 's'} from ${file.name}.`);
    } catch (e: any) {
      notify(e.message, true);
    } finally {
      setProfileBusy(false);
      event.target.value = '';
    }
  };

  const handleDownloadProfileFile = async () => {
    setProfileBusy(true);
    try {
      const result = await api('/profile-config');
      const blob = new Blob([result.content], { type: 'application/toml;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = result.filename || 'profiles.toml';
      link.click();
      URL.revokeObjectURL(url);
      notify('Downloaded the sender profile TOML file. Credentials are not included.');
    } catch (e: any) {
      notify(e.message, true);
    } finally {
      setProfileBusy(false);
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
    void loadCampaigns();
    void loadProfiles();
    void loadUnsubConfig();
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

  useEffect(() => {
    if (activeTab !== 'status') return;
    void loadCampaignStatuses();
    const timer = window.setInterval(() => void loadCampaignStatuses(), 5000);
    return () => window.clearInterval(timer);
  }, [activeTab]);

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
          body_template: 'Hi {{ first_name }},\n\nWe are excited to reach out!\n\nIf you prefer not to receive these emails, you can [unsubscribe here]({{ unsubscribe_url }}).\n\nBest,\nTeam',
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



  const handleDeleteCampaign = async (campaignToDelete?: Campaign) => {
    const target = campaignToDelete || selected;
    if (!target) return;
    if (target.state === 'sending') {
      notify('Cannot delete a campaign while it is actively sending. Please pause or cancel it first.', true);
      return;
    }
    const confirmed = window.confirm(
      `Are you sure you want to delete campaign "${target.name}"? This action cannot be undone.`
    );
    if (!confirmed) return;

    try {
      await api(`/campaigns/${target.id}`, { method: 'DELETE' });
      notify(`Campaign "${target.name}" has been deleted.`);
      const remaining = campaigns.filter((c) => c.id !== target.id);
      setCampaigns(remaining);
      if (selectedId === target.id) {
        setSelectedId(remaining[0]?.id || null);
        if (remaining.length === 0) {
          setSelected(null);
          setRecipients([]);
        }
      }
      await loadCampaigns();
    } catch (e: any) {
      notify(e.message, true);
    }
  };

  const handleDuplicateCampaign = async () => {
    if (!selected) return;
    try {
      const duplicate: Campaign = await api(`/campaigns/${selected.id}/duplicate`, { method: 'POST' });
      await loadCampaigns();
      setSelectedId(duplicate.id);
      setActiveTab('template');
      notify(`Created draft campaign "${duplicate.name}".`);
    } catch (e: any) {
      notify(e.message, true);
    }
  };

  const processFile = async (file: File) => {
    try {
      const text = await file.text();
      setJsonInput(text);
      setUploadedFileName(file.name);
      setUploadedFileSize(file.size);
      notify(`Loaded file "${file.name}" (${(file.size / 1024).toFixed(1)} KB)`);
    } catch (err: any) {
      notify(`Failed to read file: ${err.message}`, true);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      void processFile(file);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) {
      void processFile(file);
    }
  };

  const handleImportJson = async () => {
    if (!selected) return;
    try {
      const trimmed = jsonInput.trim();
      if (!trimmed) {
        throw new Error('Please upload a file or paste JSON/JSONLines recipient data.');
      }
      let parsed;
      try {
        parsed = JSON.parse(trimmed);
      } catch {
        // Fallback: parse as JSONLines (NDJSON)
        const lines = trimmed.split('\n').map((l) => l.trim()).filter(Boolean);
        if (lines.length === 0) {
          throw new Error('Recipient data is empty.');
        }
        parsed = lines.map((line, idx) => {
          try {
            return JSON.parse(line);
          } catch (err: any) {
            throw new Error(`Syntax error on line ${idx + 1} of JSONLines: ${err.message}`);
          }
        });
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
      loadCampaignStatuses();
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
    setUploadedFileName(null);
    setUploadedFileSize(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
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

  const loadSampleJsonlines = () => {
    setUploadedFileName(null);
    setUploadedFileSize(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
    setJsonInput(
      [
        JSON.stringify({ email: 'alex@example.com', first_name: 'Alex', company: 'Acme Corp', role: 'CTO' }),
        JSON.stringify({ email: 'sarah@example.com', first_name: 'Sarah', company: 'Globex Inc', role: 'VP Engineering' }),
        JSON.stringify({ email: 'jordan@example.com', first_name: 'Jordan', company: 'Soylent Ltd', role: 'Product Lead' }),
      ].join('\n')
    );
  };

  const totalRecipients = recipients.length;
  const sentCount = eventCounts['sent'] || 0;
  const progressPercent =
    totalRecipients > 0 ? Math.round((sentCount / totalRecipients) * 100) : 0;

  return (
    <main>
      <header>
        <div>
          <h1>Local Mail Merge</h1>
          <p>Privacy-first bulk email delivery engine</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          <button onClick={handleCreateCampaign}>+ New Campaign</button>
          <button className="secondary" onClick={openNewProfile}>+ New Profile</button>
          <UserButton />
        </div>
      </header>

      {error && <div className="toast-error">{error}</div>}
      {success && <div className="toast-success">{success}</div>}

      {profileManagerOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setProfileManagerOpen(false)}>
          <section
            className="profile-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="profile-manager-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="profile-modal-header">
              <div>
                <h2 id="profile-manager-title">Sender profiles</h2>
                <p>Manage SMTP accounts and their sending guardrails.</p>
              </div>
              <button className="icon-button secondary" aria-label="Close profile manager" onClick={() => setProfileManagerOpen(false)}>✕</button>
            </div>

            {error && <div className="profile-modal-notice error">{error}</div>}
            {success && <div className="profile-modal-notice success">{success}</div>}

            <div className="profile-toolbar">
              <button className="secondary" onClick={() => profileFileInputRef.current?.click()} disabled={profileBusy}>
                ↑ Load TOML
              </button>
              <input ref={profileFileInputRef} type="file" accept=".toml,application/toml,text/plain" hidden onChange={handleLoadProfileFile} />
              <button className="secondary" onClick={handleDownloadProfileFile} disabled={profileBusy || profiles.length === 0}>
                ↓ Download TOML
              </button>
              <span className="profile-toolbar-note">Secrets stay in the OS keychain and are never exported.</span>
            </div>

            <div className="profile-manager-layout">
              <nav className="profile-list" aria-label="Sender profiles">
                <button
                  className={`profile-list-item ${editingProfileId === null ? 'active' : ''}`}
                  onClick={() => {
                    setEditingProfileId(null);
                    setProfileForm(blankProfile());
                  }}
                >
                  <strong>＋ New profile</strong>
                  <span>Configure another sender</span>
                </button>
                {profiles.map((profile) => (
                  <button
                    key={profile.id}
                    className={`profile-list-item ${editingProfileId === profile.id ? 'active' : ''}`}
                    onClick={() => editProfile(profile)}
                  >
                    <strong>{profile.name}</strong>
                    <span>{profile.smtp_host}:{profile.smtp_port}</span>
                  </button>
                ))}
              </nav>

              <form
                className="profile-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void handleSaveProfile();
                }}
              >
                <div className="profile-form-title">
                  <div>
                    <h3>{editingProfileId ? 'Edit sender profile' : 'Create sender profile'}</h3>
                    <p>{editingProfileId ? 'A blank password keeps the stored credential.' : 'Enter the SMTP details supplied by your email provider.'}</p>
                  </div>
                </div>

                <fieldset>
                  <legend>Sender Identity</legend>
                  <div className="form-grid compact">
                    <div className="form-group">
                      <label htmlFor="profile-from-name">Sender Name</label>
                      <input id="profile-from-name" value={profileForm.from_name || ''} onChange={(e) => setProfileForm({ ...profileForm, from_name: e.target.value })} placeholder="Alex Smith" />
                    </div>
                    <div className="form-group">
                      <label htmlFor="profile-from-address">Sender Email</label>
                      <input id="profile-from-address" type="email" value={profileForm.from_address || ''} onChange={(e) => setProfileForm({ ...profileForm, from_address: e.target.value })} placeholder="alex@example.com" />
                    </div>
                  </div>
                </fieldset>

                <fieldset>
                  <legend>SMTP connection</legend>
                  <div className="form-grid compact">
                    <div className="form-group full">
                      <label htmlFor="profile-name">Profile name</label>
                      <input id="profile-name" required value={profileForm.name} onChange={(e) => setProfileForm({ ...profileForm, name: e.target.value })} placeholder="Company SMTP" />
                    </div>
                    <div className="form-group">
                      <label htmlFor="profile-smtp-host">SMTP host</label>
                      <input id="profile-smtp-host" required value={profileForm.smtp_host} onChange={(e) => setProfileForm({ ...profileForm, smtp_host: e.target.value })} placeholder="smtp.example.com" />
                    </div>
                    <div className="form-group">
                      <label htmlFor="profile-smtp-port">Port</label>
                      <input id="profile-smtp-port" required type="number" min={1} max={65535} value={profileForm.smtp_port} onChange={(e) => setProfileForm({ ...profileForm, smtp_port: Number(e.target.value) })} />
                    </div>
                    <div className="form-group">
                      <label htmlFor="profile-security">Security</label>
                      <select id="profile-security" value={profileForm.security} onChange={(e) => setProfileForm({ ...profileForm, security: e.target.value as ProfileDraft['security'] })}>
                        <option value="starttls">STARTTLS</option>
                        <option value="tls">TLS / SSL</option>
                        <option value="none">None</option>
                      </select>
                    </div>
                    <div className="form-group checkbox-field">
                      <label><input type="checkbox" checked={profileForm.verify_tls} onChange={(e) => setProfileForm({ ...profileForm, verify_tls: e.target.checked })} /> Verify TLS certificate</label>
                    </div>
                    <div className="form-group">
                      <label htmlFor="profile-username">Username</label>
                      <input id="profile-username" value={profileForm.username || ''} onChange={(e) => setProfileForm({ ...profileForm, username: e.target.value })} autoComplete="username" />
                    </div>
                    <div className="form-group">
                      <label htmlFor="profile-auth-type">Authentication</label>
                      <select id="profile-auth-type" value={profileForm.auth_type} onChange={(e) => setProfileForm({ ...profileForm, auth_type: e.target.value, password: '' })}>
                        <option value="password">Password / app password</option>
                        <option value="xoauth2">OAuth 2 access token</option>
                      </select>
                    </div>
                    <div className="form-group full">
                      <label htmlFor="profile-password">{profileForm.auth_type === 'xoauth2' ? 'Access token' : 'Password or app password'}</label>
                      <input id="profile-password" type="password" value={profileForm.password} onChange={(e) => setProfileForm({ ...profileForm, password: e.target.value })} autoComplete="new-password" placeholder={editingProfileId ? 'Leave blank to keep current' : 'Stored in OS keychain'} />
                    </div>
                  </div>
                </fieldset>

                <fieldset>
                  <legend>Defaults and guardrails</legend>
                  <div className="form-grid compact">
                    <div className="form-group">
                      <label htmlFor="profile-reply-to">Default Reply-To</label>
                      <input id="profile-reply-to" type="email" value={profileForm.reply_to || ''} onChange={(e) => setProfileForm({ ...profileForm, reply_to: e.target.value })} placeholder="replies@example.com" />
                    </div>
                    <div className="form-group">
                      <label htmlFor="profile-daily-cap">Daily cap</label>
                      <input id="profile-daily-cap" type="number" min={1} value={profileForm.daily_cap} onChange={(e) => setProfileForm({ ...profileForm, daily_cap: Number(e.target.value) })} />
                    </div>
                    <div className="form-group">
                      <label htmlFor="profile-delay">Delay between messages (seconds)</label>
                      <input id="profile-delay" type="number" min={0} value={profileForm.delay_seconds} onChange={(e) => setProfileForm({ ...profileForm, delay_seconds: Number(e.target.value) })} />
                    </div>
                    <div className="form-group">
                      <label htmlFor="profile-size">Maximum message size (bytes)</label>
                      <input id="profile-size" type="number" min={1024} value={profileForm.max_message_bytes} onChange={(e) => setProfileForm({ ...profileForm, max_message_bytes: Number(e.target.value) })} />
                    </div>
                  </div>
                </fieldset>

                <details className="advanced-profile-settings">
                  <summary>Advanced settings</summary>
                  <div className="form-grid compact">
                    <div className="form-group">
                      <label htmlFor="profile-imap-host">IMAP host</label>
                      <input id="profile-imap-host" value={profileForm.imap_host || ''} onChange={(e) => setProfileForm({ ...profileForm, imap_host: e.target.value })} placeholder="imap.example.com" />
                    </div>
                    <div className="form-group">
                      <label htmlFor="profile-imap-port">IMAP port</label>
                      <input id="profile-imap-port" type="number" min={1} max={65535} value={profileForm.imap_port ?? ''} onChange={(e) => setProfileForm({ ...profileForm, imap_port: e.target.value ? Number(e.target.value) : null })} />
                    </div>
                    <div className="form-group">
                      <label htmlFor="profile-imap-security">IMAP security</label>
                      <select id="profile-imap-security" value={profileForm.imap_security || ''} onChange={(e) => setProfileForm({ ...profileForm, imap_security: (e.target.value || null) as ProfileDraft['imap_security'] })}>
                        <option value="">Not configured</option>
                        <option value="starttls">STARTTLS</option>
                        <option value="tls">TLS / SSL</option>
                        <option value="none">None</option>
                      </select>
                    </div>
                    <div className="form-group">
                      <label htmlFor="profile-timezone">Working-hours timezone</label>
                      <input id="profile-timezone" value={profileForm.working_hours_timezone} onChange={(e) => setProfileForm({ ...profileForm, working_hours_timezone: e.target.value })} placeholder="Europe/Berlin" />
                    </div>
                    <div className="form-group full checkbox-field">
                      <label><input type="checkbox" checked={profileForm.working_hours_enabled} onChange={(e) => setProfileForm({ ...profileForm, working_hours_enabled: e.target.checked })} /> Restrict sending to weekdays and working hours</label>
                    </div>
                    {profileForm.working_hours_enabled && (
                      <>
                        <div className="form-group">
                          <label htmlFor="profile-start-hour">Start hour</label>
                          <input id="profile-start-hour" type="number" min={0} max={23} value={profileForm.working_hours_start} onChange={(e) => setProfileForm({ ...profileForm, working_hours_start: Number(e.target.value) })} />
                        </div>
                        <div className="form-group">
                          <label htmlFor="profile-end-hour">End hour</label>
                          <input id="profile-end-hour" type="number" min={0} max={23} value={profileForm.working_hours_end} onChange={(e) => setProfileForm({ ...profileForm, working_hours_end: Number(e.target.value) })} />
                        </div>
                      </>
                    )}
                  </div>
                </details>

                <div className="profile-form-actions">
                  <span>Saving also updates the managed TOML configuration.</span>
                  {editingProfileId && selected && (
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => {
                        setForm({ ...form, profile_id: editingProfileId });
                        setProfileManagerOpen(false);
                        notify('Sender profile selected. Save the campaign to keep this selection.');
                      }}
                    >
                      Use in campaign
                    </button>
                  )}
                  <button type="button" className="secondary" onClick={handleTestProfileConnection} disabled={profileBusy || profileTesting}>
                    {profileTesting ? 'Testing…' : 'Test connection'}
                  </button>
                  <button type="submit" disabled={profileBusy || profileTesting}>{profileBusy ? 'Saving…' : 'Save profile'}</button>
                </div>
              </form>
            </div>
          </section>
        </div>
      )}

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
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
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
                  <button className="secondary" onClick={handleDuplicateCampaign}>
                    ⧉ Duplicate
                  </button>
                  {selected.state !== 'sending' && (
                    <button
                      className="secondary"
                      style={{ color: '#dc3545', borderColor: '#f5c2c7' }}
                      title="Delete this campaign"
                      onClick={() => handleDeleteCampaign(selected)}
                    >
                      🗑 Delete
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
                <button
                  className={`tab-btn ${activeTab === 'status' ? 'active' : ''}`}
                  onClick={() => setActiveTab('status')}
                >
                  📊 Campaign Status
                </button>
              </div>

              {/* TAB 1: Setup & Template */}
              {activeTab === 'template' && (
                <div>
                  <div className="form-grid">
                    <div className="form-group full">
                      <label>Campaign ID</label>
                      <div style={{ display: 'flex', gap: '8px' }}>
                        <input
                          type="text"
                          readOnly
                          value={selected.id}
                          style={{ background: '#f4f6f3', fontFamily: 'monospace', fontSize: '0.85rem' }}
                        />
                        <button
                          type="button"
                          className="secondary"
                          style={{ padding: '0 14px', whiteSpace: 'nowrap' }}
                          onClick={() => {
                            navigator.clipboard.writeText(selected.id);
                            notify('Campaign ID copied to clipboard.');
                          }}
                        >
                          📋 Copy ID
                        </button>
                      </div>
                    </div>
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
                        onChange={(e) => {
                          const selectedPid = e.target.value;
                          const prof = profiles.find((p) => p.id === selectedPid);
                          setForm((prev) => ({
                            ...prev,
                            profile_id: selectedPid,
                            from_name: prev.from_name || (prof?.from_name ?? ''),
                            from_address: prev.from_address || (prof?.from_address ?? ''),
                            reply_to: prev.reply_to || (prof?.reply_to ?? ''),
                          }));
                        }}
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

                  {/* List-Unsubscribe Header Configuration */}
                  <div className="card">
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px', flexWrap: 'wrap', gap: '8px' }}>
                      <h3 style={{ margin: 0, fontSize: '1rem' }}>🔕 RFC 8058 One-Click Unsubscribe (List-Unsubscribe)</h3>
                      {unsubConfig?.signing_secret_configured ? (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', background: '#e6f4ed', color: '#176b45', padding: '3px 8px', borderRadius: '4px', fontSize: '0.78rem', fontWeight: 600 }}>
                          🔒 UNSUBSCRIBE_SIGNING_SECRET active (.env)
                        </span>
                      ) : (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', background: '#fff3cd', color: '#856404', padding: '3px 8px', borderRadius: '4px', fontSize: '0.78rem' }}>
                          ⚠️ UNSUBSCRIBE_SIGNING_SECRET not set in .env
                        </span>
                      )}
                    </div>
                    <p style={{ margin: '0 0 12px', fontSize: '0.85rem', color: '#5e6b62' }}>
                      Base URL is fixed to <code>https://mailmerge.plus.bi</code>. When enabled, the backend dynamically generates an HMAC-SHA256 signature for each recipient using campaign <em>"{selected.name}"</em> and injects <code>List-Unsubscribe</code> &amp; <code>List-Unsubscribe-Post</code> headers.
                    </p>
                    <div className="form-group full checkbox-field" style={{ margin: 0 }}>
                      <label style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontWeight: 600 }}>
                        <input
                          type="checkbox"
                          checked={form.list_unsubscribe_enabled ?? false}
                          onChange={(e) => setForm({ ...form, list_unsubscribe_enabled: e.target.checked })}
                        />
                        Enable RFC 8058 One-Click Unsubscribe
                      </label>
                    </div>
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

                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '20px' }}>
                    <button onClick={handleSaveCampaign}>💾 Save Campaign Configuration</button>
                    {selected.state !== 'sending' && (
                      <button
                        className="danger"
                        style={{ padding: '8px 14px' }}
                        onClick={() => handleDeleteCampaign(selected)}
                      >
                        🗑 Delete Campaign
                      </button>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 2: JSON / JSONLines Recipient Import */}
              {activeTab === 'recipients' && (
                <div>
                  <div className="card">
                    <h3 style={{ margin: '0 0 8px', fontSize: '1.05rem' }}>📥 Batch Recipient Ingestion (JSON / JSONLines)</h3>
                    <p style={{ margin: '0 0 16px', fontSize: '0.85rem', color: '#5e6b62' }}>
                      Upload a <code>.json</code> or <code>.jsonl</code> / <code>.ndjson</code> file, drag &amp; drop it below, or paste your recipient records directly. All template variables are strictly validated.
                    </p>

                    {/* Drag & Drop Zone */}
                    <div
                      className={`dropzone ${isDragging ? 'dropzone-active' : ''}`}
                      onDragOver={(e) => {
                        e.preventDefault();
                        setIsDragging(true);
                      }}
                      onDragLeave={() => setIsDragging(false)}
                      onDrop={handleDrop}
                      onClick={() => fileInputRef.current?.click()}
                    >
                      <input
                        type="file"
                        ref={fileInputRef}
                        style={{ display: 'none' }}
                        accept=".json,.jsonl,.ndjson,application/json,application/x-ndjson,text/plain"
                        onChange={handleFileChange}
                      />
                      <div style={{ fontSize: '1.8rem', marginBottom: '6px' }}>📄</div>
                      <div style={{ fontWeight: 600, fontSize: '0.95rem' }}>
                        Click to upload or drag &amp; drop a file
                      </div>
                      <div style={{ fontSize: '0.8rem', color: '#5e6b62', marginTop: '4px' }}>
                        Supports <code>.json</code> (array or object) and <code>.jsonl</code> / <code>.ndjson</code> (one JSON object per line)
                      </div>
                    </div>

                    {uploadedFileName && (
                      <div className="file-info-badge">
                        <span>
                          📎 <strong>{uploadedFileName}</strong> (
                          {uploadedFileSize ? (uploadedFileSize / 1024).toFixed(1) : 0} KB)
                        </span>
                        <button
                          className="secondary"
                          style={{ padding: '2px 8px', fontSize: '0.75rem' }}
                          onClick={(e) => {
                            e.stopPropagation();
                            setUploadedFileName(null);
                            setUploadedFileSize(null);
                            setJsonInput('');
                            if (fileInputRef.current) fileInputRef.current.value = '';
                          }}
                        >
                          Clear File
                        </button>
                      </div>
                    )}

                    <div style={{ marginTop: '14px' }}>
                      <label style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
                        <span>Edit / Paste JSON or JSONLines Content:</span>
                        {jsonInput && (
                          <span style={{ fontSize: '0.8rem', color: '#5e6b62', fontWeight: 'normal' }}>
                            {jsonInput.split('\n').filter((l) => l.trim()).length} line(s)
                          </span>
                        )}
                      </label>
                      <textarea
                        rows={10}
                        className="code"
                        style={{ width: '100%', boxSizing: 'border-box' }}
                        placeholder={`// Option A: JSON Array\n[\n  {"email": "alice@example.com", "first_name": "Alice", "company": "Acme"},\n  {"email": "bob@example.com", "first_name": "Bob", "company": "Globex"}\n]\n\n// Option B: JSONLines (.jsonl)\n{"email": "alice@example.com", "first_name": "Alice", "company": "Acme"}\n{"email": "bob@example.com", "first_name": "Bob", "company": "Globex"}`}
                        value={jsonInput}
                        onChange={(e) => setJsonInput(e.target.value)}
                      />
                    </div>

                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', marginTop: '14px' }}>
                      <button onClick={handleImportJson}>📥 Import Recipients</button>
                      <button className="secondary" onClick={loadSampleJson}>
                        Load Example JSON
                      </button>
                      <button className="secondary" onClick={loadSampleJsonlines}>
                        Load Example JSONLines
                      </button>
                      {jsonInput && (
                        <button
                          className="secondary"
                          onClick={() => {
                            setJsonInput('');
                            setUploadedFileName(null);
                            setUploadedFileSize(null);
                            if (fileInputRef.current) fileInputRef.current.value = '';
                          }}
                        >
                          Clear
                        </button>
                      )}
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
                          {JSON.stringify(
                            Object.fromEntries(
                              Object.entries(previewContent.values).filter(([key]) => key !== 'unsubscribe_url')
                            ),
                            null,
                            2
                          )}
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

                  <div className="card">
                    <h3 style={{ margin: '0 0 12px', fontSize: '1rem' }}>🚀 Launch Campaign</h3>
                    <p style={{ margin: '0 0 12px', fontSize: '0.85rem', color: '#5e6b62' }}>
                      Runs preflight validation again, then schedules the campaign for immediate delivery.
                    </p>
                    {selected.state === 'draft' ? (
                      <button onClick={handleScheduleCampaign}>🚀 Launch Campaign</button>
                    ) : (
                      <p style={{ margin: 0, fontSize: '0.85rem', color: '#5e6b62' }}>
                        This campaign is currently <strong>{selected.state}</strong>. Duplicate it to start a new campaign from these settings.
                      </p>
                    )}
                  </div>
                </div>
              )}

              {/* TAB 5: Launched campaign status */}
              {activeTab === 'status' && (
                <div>
                  <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                      <h3 style={{ margin: '0 0 6px', fontSize: '1.05rem' }}>Launched Campaigns</h3>
                      <p style={{ margin: 0, fontSize: '0.85rem', color: '#5e6b62' }}>
                        Delivery status refreshes automatically every five seconds.
                      </p>
                    </div>
                    <button className="secondary" onClick={loadCampaignStatuses} disabled={statusLoading}>
                      {statusLoading ? 'Refreshing…' : '↻ Refresh'}
                    </button>
                  </div>

                  <div className="table-wrapper">
                    <table>
                      <thead>
                        <tr>
                          <th>Campaign</th>
                          <th>Launched</th>
                          <th>State</th>
                          <th>Progress</th>
                          <th>Sent</th>
                          <th>Failed</th>
                          <th></th>
                        </tr>
                      </thead>
                      <tbody>
                        {campaignStatuses.map((campaign) => {
                          const sent = campaign.counts.sent || 0;
                          const failed = campaign.counts.failed || 0;
                          const processed = sent + failed;
                          return (
                            <tr key={campaign.id}>
                              <td><strong>{campaign.name}</strong></td>
                              <td>{new Date(campaign.scheduled_at).toLocaleString()}</td>
                              <td><span className={`badge badge-${campaign.state}`}>{campaign.state}</span></td>
                              <td>{processed} / {campaign.total}</td>
                              <td>{sent}</td>
                              <td>{failed}</td>
                              <td>
                                <button
                                  className="secondary"
                                  style={{ padding: '4px 10px', fontSize: '0.8rem' }}
                                  onClick={() => {
                                    setSelectedId(campaign.id);
                                    setActiveTab('recipients');
                                  }}
                                >
                                  View
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                        {!statusLoading && campaignStatuses.length === 0 && (
                          <tr>
                            <td colSpan={7} style={{ textAlign: 'center', color: '#888' }}>
                              No campaigns have been launched yet.
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
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

function AuthLanding() {
  return (
    <div className="auth-container">
      <div className="auth-card">
        <div style={{ fontSize: '2.5rem', marginBottom: '8px' }}>📬</div>
        <h1>Local Mail Merge</h1>
        <p>Sign in to manage your campaigns, templates, and email delivery.</p>
        <div className="auth-actions">
          <SignInButton mode="modal">
            <button style={{ width: '100%', padding: '12px', fontSize: '1rem' }}>
              Sign In
            </button>
          </SignInButton>
          <SignUpButton mode="modal">
            <button className="secondary" style={{ width: '100%', padding: '12px', fontSize: '1rem' }}>
              Create an Account
            </button>
          </SignUpButton>
        </div>
      </div>
    </div>
  );
}

const PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY || '';

function App() {
  if (!PUBLISHABLE_KEY) {
    return (
      <div className="auth-container">
        <div className="auth-card">
          <h2>Clerk Configuration Missing</h2>
          <p>Please ensure <code>VITE_CLERK_PUBLISHABLE_KEY</code> is set in your environment.</p>
        </div>
      </div>
    );
  }

  return (
    <ClerkProvider publishableKey={PUBLISHABLE_KEY} afterSignOutUrl="/">
      <Show when="signed-out">
        <AuthLanding />
      </Show>
      <Show when="signed-in">
        <Dashboard />
      </Show>
    </ClerkProvider>
  );
}

createRoot(document.getElementById('root')!).render(<App />);

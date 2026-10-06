// State
let allMessages = [];
let systemStatus = null;
let currentFilter = 'ALL';
let searchQuery = '';
let subRemainingSeconds = 0;
let countdownTimer = null;
let ws = null;

// DOM Elements
const elTunnelLink = document.getElementById('tunnelUrlLink');
const elTunnelSubtext = document.getElementById('tunnelSubtext');
const elBtnCopyTunnel = document.getElementById('btnCopyTunnel');
const elSubCountdown = document.getElementById('subCountdown');
const elSubProgressFill = document.getElementById('subProgressFill');
const elSubIdText = document.getElementById('subIdText');
const elBtnRenewSub = document.getElementById('btnRenewSub');
const elBtnRecreateSub = document.getElementById('btnRecreateSub');
const elTargetName = document.getElementById('targetName');
const elValTotal = document.getElementById('valTotal');
const elValClient = document.getElementById('valClient');
const elValPm = document.getElementById('valPm');
const elValDev = document.getElementById('valDev');
const elMessageList = document.getElementById('messageList');
const elFeedEmptyState = document.getElementById('feedEmptyState');
const elFeedCountBadge = document.getElementById('feedCountBadge');
const elFeedSearch = document.getElementById('feedSearch');
const elRoleFilterPills = document.getElementById('roleFilterPills');
const elBtnRefresh = document.getElementById('btnRefresh');

// Simulator Modal Elements
const elSimulatorModal = document.getElementById('simulatorModal');
const elBtnOpenSimulator = document.getElementById('btnOpenSimulator');
const elBtnCloseSimulator = document.getElementById('btnCloseSimulator');
const elBtnCancelSim = document.getElementById('btnCancelSim');
const elBtnSubmitSim = document.getElementById('btnSubmitSim');
const elSimMessageText = document.getElementById('simMessageText');

// Payload Modal Elements
const elPayloadModal = document.getElementById('payloadModal');
const elBtnClosePayload = document.getElementById('btnClosePayload');
const elBtnClosePayloadBtn = document.getElementById('btnClosePayloadBtn');
const elPayloadCode = document.getElementById('payloadCode');
const elBtnCopyPayload = document.getElementById('btnCopyPayload');

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
  setupEventListeners();
  initWebSocket();
  await loadTeamMembers();
  await fetchStatus();
  await fetchMessages();

  // Periodic status poll as background sync (30s)
  setInterval(fetchStatus, 30000);
});

// Setup Events
function setupEventListeners() {
  const elBtnSyncMessages = document.getElementById('btnSyncMessages');
  if (elBtnSyncMessages) {
    elBtnSyncMessages.addEventListener('click', async () => {
      try {
        elBtnSyncMessages.disabled = true;
        elBtnSyncMessages.innerText = 'Syncing...';
        const res = await fetch('/api/messages/sync', { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
          const stats = data.result || {};
          showToast(`Synced from Teams! (${stats.new || 0} new, ${stats.updated || 0} updated)`, 'success');
          await fetchMessages();
          await fetchStatus();
        } else {
          showToast(data.detail || 'Sync failed', 'error');
        }
      } catch (err) {
        showToast('Sync network error', 'error');
      } finally {
        elBtnSyncMessages.disabled = false;
        elBtnSyncMessages.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Sync Teams`;
      }
    });
  }

  elBtnRefresh.addEventListener('click', () => {
    fetchStatus();
    fetchMessages();
    showToast('Dashboard refreshed', 'info');
  });

  elBtnCopyTunnel.addEventListener('click', () => {
    const url = elTunnelLink.getAttribute('href');
    if (url && url !== '#') {
      navigator.clipboard.writeText(url);
      showToast('Tunnel URL copied to clipboard!', 'success');
    }
  });

  elBtnRenewSub.addEventListener('click', async () => {
    try {
      elBtnRenewSub.disabled = true;
      elBtnRenewSub.innerText = 'Renewing...';
      const res = await fetch('/api/subscription/renew', { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        showToast('Subscription renewed successfully (+60m)!', 'success');
        fetchStatus();
      } else {
        showToast(data.detail || 'Failed to renew', 'error');
      }
    } catch (err) {
      showToast('Network error while renewing', 'error');
    } finally {
      elBtnRenewSub.disabled = false;
      elBtnRenewSub.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg> Renew (+60m)`;
    }
  });

  if (elBtnRecreateSub) {
    elBtnRecreateSub.addEventListener('click', async () => {
      try {
        elBtnRecreateSub.disabled = true;
        elBtnRecreateSub.innerText = 'Creating...';
        const res = await fetch('/api/subscription/create', { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
          showToast('Graph subscription ensured & active!', 'success');
          fetchStatus();
        } else {
          showToast(data.detail || 'Failed to create subscription', 'error');
        }
      } catch (err) {
        showToast('Network error', 'error');
      } finally {
        elBtnRecreateSub.disabled = false;
        elBtnRecreateSub.innerText = 'Re-create';
      }
    });
  }

  // Filter Pills
  elRoleFilterPills.addEventListener('click', (e) => {
    const pill = e.target.closest('.pill');
    if (!pill) return;
    document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    currentFilter = pill.dataset.filter;
    renderMessages();
  });

  // Search input
  elFeedSearch.addEventListener('input', (e) => {
    searchQuery = e.target.value.toLowerCase().trim();
    renderMessages();
  });

  // Simulator Modal
  elBtnOpenSimulator.addEventListener('click', () => elSimulatorModal.classList.add('active'));
  elBtnCloseSimulator.addEventListener('click', () => elSimulatorModal.classList.remove('active'));
  elBtnCancelSim.addEventListener('click', () => elSimulatorModal.classList.remove('active'));

  // Sample chips in simulator
  document.querySelectorAll('.chip-sample').forEach(chip => {
    chip.addEventListener('click', () => {
      elSimMessageText.value = chip.dataset.text;
    });
  });

  elBtnSubmitSim.addEventListener('click', async () => {
    const text = elSimMessageText.value.trim();
    if (!text) {
      showToast('Please type a message', 'error');
      return;
    }
    const role = document.querySelector('input[name="simRole"]:checked').value;

    try {
      elBtnSubmitSim.disabled = true;
      elBtnSubmitSim.innerText = 'Sending...';
      const res = await fetch('/api/test/simulate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role, text })
      });
      if (res.ok) {
        showToast(`Simulated message sent from ${role}!`, 'success');
        elSimMessageText.value = '';
        elSimulatorModal.classList.remove('active');
        fetchMessages();
        fetchStatus();
      } else {
        showToast('Simulation failed', 'error');
      }
    } catch (err) {
      showToast('Error sending simulation', 'error');
    } finally {
      elBtnSubmitSim.disabled = false;
      elBtnSubmitSim.innerText = 'Send Test Message';
    }
  });

  // Payload modal close
  elBtnClosePayload.addEventListener('click', () => elPayloadModal.classList.remove('active'));
  elBtnClosePayloadBtn.addEventListener('click', () => elPayloadModal.classList.remove('active'));
  elBtnCopyPayload.addEventListener('click', () => {
    navigator.clipboard.writeText(elPayloadCode.innerText);
    showToast('JSON payload copied!', 'success');
  });

  // Test Jira Ticket button
  const elBtnTestJira = document.getElementById('btnTestJira');
  if (elBtnTestJira) {
    elBtnTestJira.addEventListener('click', async () => {
      try {
        elBtnTestJira.disabled = true;
        elBtnTestJira.innerText = 'Creating...';
        const res = await fetch('/api/jira/test-ticket', { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.success) {
          showToast(`✅ Jira ticket created: ${data.key}!`, 'success');
          fetchStatus();
        } else {
          showToast(`Error: ${data.detail || data.error || 'Failed to create ticket'}`, 'error');
        }
      } catch (err) {
        showToast('Network error testing Jira ticket', 'error');
      } finally {
        elBtnTestJira.disabled = false;
        elBtnTestJira.innerText = '⚡ Test Ticket';
      }
    });
  }

  // Header Test Follow-Up button
  const elBtnHeaderTestFollowup = document.getElementById('btnHeaderTestFollowup');
  if (elBtnHeaderTestFollowup) {
    elBtnHeaderTestFollowup.addEventListener('click', async () => {
      try {
        elBtnHeaderTestFollowup.disabled = true;
        elBtnHeaderTestFollowup.innerHTML = '<span class="status-dot"></span> Sending...';
        const res = await fetch('/api/test/simulate-pm-followup', { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.success) {
          const method = data.result?.teams?.method || 'delivered';
          showToast(`⏰ Test Follow-Up sent to PM (${method}) for message #${data.message_id}!`, 'success');
          await fetchMessages();
        } else {
          showToast(data.detail || data.error || 'Failed to trigger follow-up', 'error');
        }
      } catch (err) {
        showToast('Network error triggering follow-up', 'error');
      } finally {
        elBtnHeaderTestFollowup.disabled = false;
        elBtnHeaderTestFollowup.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg><span>Test Follow-Up</span>';
      }
    });
  }

  // Test Teams Webhook button
  const elBtnTestWebhook = document.getElementById('btnTestWebhook');
  if (elBtnTestWebhook) {
    elBtnTestWebhook.addEventListener('click', async () => {
      try {
        elBtnTestWebhook.disabled = true;
        elBtnTestWebhook.innerText = 'Sending...';
        const res = await fetch('/api/teams/test-webhook', { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.success) {
          showToast('✅ Test card posted to Teams group chat!', 'success');
        } else {
          showToast(`Error: ${data.detail || data.error || 'Failed to post to Teams'}`, 'error');
        }
      } catch (err) {
        showToast('Network error calling test webhook', 'error');
      } finally {
        elBtnTestWebhook.disabled = false;
        elBtnTestWebhook.innerText = '⚡ Test Card';
      }
    });
  }

  // Test Email buttons (header & pipeline step 6)
  const triggerEmailTest = async (btn) => {
    try {
      if (btn) {
        btn.disabled = true;
        btn.innerText = 'Sending...';
      }
      const res = await fetch('/api/test/test-email', { method: 'POST' });
      const data = await res.json();
      if (res.ok && data.result?.success) {
        showToast(`✉️ Test alert email sent to ${data.to} via ${data.result.method || 'SMTP'}!`, 'success');
      } else {
        const errMsg = data.result?.error || data.detail || 'Email send failed';
        showToast(`Email error: ${errMsg}`, 'error');
      }
    } catch (err) {
      showToast('Network error sending test email', 'error');
    } finally {
      if (btn) {
        btn.disabled = false;
        if (btn.id === 'btnHeaderTestEmail') {
          btn.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg><span>Test Email</span>';
        } else {
          btn.innerText = '✉️ Test Email';
        }
      }
    }
  };

  const elBtnHeaderTestEmail = document.getElementById('btnHeaderTestEmail');
  if (elBtnHeaderTestEmail) {
    elBtnHeaderTestEmail.addEventListener('click', () => triggerEmailTest(elBtnHeaderTestEmail));
  }

  const elBtnTestEmail = document.getElementById('btnTestEmail');
  if (elBtnTestEmail) {
    elBtnTestEmail.addEventListener('click', () => triggerEmailTest(elBtnTestEmail));
  }
}

// Fetch Status
async function fetchStatus() {
  try {
    const res = await fetch('/api/status');
    if (!res.ok) return;
    const data = await res.json();
    systemStatus = data;
    renderStatus(data);
  } catch (err) {
    console.error('Failed to fetch status:', err);
  }
}

// Render Status UI
function renderStatus(data) {
  if (data.target && data.target.title) {
    elTargetName.innerText = data.target.title;
  }

  // Tunnel
  if (data.tunnel && data.tunnel.active && data.tunnel.url) {
    elTunnelLink.href = data.tunnel.url;
    elTunnelLink.innerText = data.tunnel.url;
    elTunnelSubtext.innerText = 'Connected via ngrok forwarding to :3000';
  } else {
    elTunnelLink.href = '#';
    elTunnelLink.innerText = (data.tunnel && data.tunnel.url) ? `Tunnel Offline: ${data.tunnel.url}` : 'No active tunnel detected';
    elTunnelSubtext.innerText = 'Start ngrok on port 3000 to enable Microsoft Graph webhooks';
  }

  // Subscription
  const sub = data.subscription || {};
  if (sub.active) {
    elSubIdText.innerText = `ID: ${sub.id || 'N/A'}`;
    subRemainingSeconds = sub.remainingSeconds || 0;
    startCountdownTimer();
  } else {
    elSubCountdown.innerText = 'No active subscription';
    elSubProgressFill.style.width = '0%';
    elSubIdText.innerText = 'Click "Re-create" to activate Graph webhooks';
    clearInterval(countdownTimer);
  }

  // Metrics
  if (data.metrics) {
    elValTotal.innerText = data.metrics.total || 0;
    elValClient.innerText = data.metrics.client || 0;
    elValPm.innerText = data.metrics.pm || 0;
    elValDev.innerText = data.metrics.developer || 0;
  }

  // Update Dynamic Role Labels from Member.xlsx
  if (data.roles) {
    const pmName = data.roles.pm?.name?.replace(' (PM)', '') || 'PM';
    const devName = data.roles.developer?.name?.replace(' (Developer)', '') || 'Developer';
    const clientName = data.roles.client?.name?.replace(' (Client)', '') || 'Client';

    const elPmLabel = document.getElementById('metricPmLabel');
    if (elPmLabel) elPmLabel.innerText = `PM (${pmName})`;

    const elDevLabel = document.getElementById('metricDevLabel');
    if (elDevLabel) elDevLabel.innerText = `Developer (${devName})`;

    const elSimPm = document.getElementById('simPillPm');
    if (elSimPm) elSimPm.innerText = `PM (${pmName})`;

    const elSimClient = document.getElementById('simPillClient');
    if (elSimClient) elSimClient.innerText = `Client (${clientName})`;

    const elSimDev = document.getElementById('simPillDev');
    if (elSimDev) elSimDev.innerText = `Developer (${devName})`;
  }

  // Update auto-renew info subtext
  const elSubAutoInfo = document.getElementById('subAutoRenewInfo');
  if (elSubAutoInfo && sub.autoRenewThresholdText) {
    elSubAutoInfo.innerText = `• Auto-renews at <${sub.autoRenewThresholdText} (every 30s)`;
  }

  // Phase 4: Jira Automation Card
  const p4 = data.pipeline?.phase4;
  const elPhase4 = document.getElementById('phase4Card');
  const elJiraTag = document.getElementById('jiraStatusTag');
  const elJiraDesc = document.getElementById('jiraStatusDesc');
  const elBtnTestJiraEl = document.getElementById('btnTestJira');

  if (p4 && p4.configured) {
    if (elPhase4) elPhase4.className = 'pipe-step step-active';
    if (elJiraTag) {
      elJiraTag.className = 'status-tag status-active';
      elJiraTag.innerText = `READY (${p4.projectKey || 'ACTIVE'})`;
    }
    if (elJiraDesc) elJiraDesc.innerText = `Connected: ${p4.baseUrl || 'Jira Cloud'}`;
    if (elBtnTestJiraEl) elBtnTestJiraEl.style.display = 'inline-block';
  } else {
    if (elPhase4) elPhase4.className = 'pipe-step step-pending';
    if (elJiraTag) {
      elJiraTag.className = 'status-tag status-pending';
      elJiraTag.innerText = 'PENDING JIRA KEYS';
    }
    if (elJiraDesc) elJiraDesc.innerText = 'Set JIRA credentials in .env to enable';
    if (elBtnTestJiraEl) elBtnTestJiraEl.style.display = 'none';
  }

  // Phase 5: Teams Webhook Card
  const p5 = data.pipeline?.phase5;
  const elPhase5 = document.getElementById('phase5Card');
  const elWebhookTag = document.getElementById('teamsWebhookTag');
  const elWebhookDesc = document.getElementById('teamsWebhookDesc');
  const elBtnTestWebhookEl = document.getElementById('btnTestWebhook');

  if (p5 && p5.webhookConfigured) {
    if (elPhase5) elPhase5.className = 'pipe-step step-active';
    if (elWebhookTag) {
      elWebhookTag.className = 'status-tag status-active';
      elWebhookTag.innerText = 'CONNECTED';
    }
    if (elWebhookDesc) elWebhookDesc.innerText = 'Teams Workflow Webhook Active';
    if (elBtnTestWebhookEl) elBtnTestWebhookEl.style.display = 'inline-block';
  } else {
    if (elPhase5) elPhase5.className = 'pipe-step step-pending';
    if (elWebhookTag) {
      elWebhookTag.className = 'status-tag status-pending';
      elWebhookTag.innerText = 'WEBHOOK READY';
    }
    if (elWebhookDesc) elWebhookDesc.innerText = 'Add TEAMS_WEBHOOK_URL to .env';
    if (elBtnTestWebhookEl) elBtnTestWebhookEl.style.display = 'none';
  }

  // Re-render messages with refreshed roles & metrics
  renderMessages();
}

// Subscription Countdown Timer
function startCountdownTimer() {
  clearInterval(countdownTimer);
  updateCountdownDisplay();

  countdownTimer = setInterval(() => {
    if (subRemainingSeconds > 0) {
      subRemainingSeconds--;
      updateCountdownDisplay();
    } else {
      elSubCountdown.innerText = 'Expired (re-creating...)';
      clearInterval(countdownTimer);
      fetchStatus();
    }
  }, 1000);
}

function updateCountdownDisplay() {
  const m = Math.floor(subRemainingSeconds / 60);
  const s = subRemainingSeconds % 60;
  elSubCountdown.innerText = `${m}m ${s < 10 ? '0' : ''}${s}s remaining`;

  // Max 60m = 3600s
  const pct = Math.min(100, Math.max(0, (subRemainingSeconds / 3600) * 100));
  elSubProgressFill.style.width = `${pct}%`;

  if (subRemainingSeconds < 600) {
    elSubProgressFill.style.background = '#EF4444'; // Red if under 10m
  } else if (subRemainingSeconds < 1500) {
    elSubProgressFill.style.background = '#F59E0B'; // Amber under 25m
  } else {
    elSubProgressFill.style.background = 'linear-gradient(90deg, #8B5CF6, #06B6D4)';
  }
}

// Fetch Messages
async function fetchMessages() {
  try {
    const res = await fetch('/api/messages?limit=100');
    if (!res.ok) return;
    const data = await res.json();
    allMessages = data.messages || [];
    renderMessages();
  } catch (err) {
    console.error('Failed to fetch messages:', err);
  }
}

window.teamMembersList = [];

async function loadTeamMembers() {
  try {
    const res = await fetch('/api/admin/members');
    if (res.ok) {
      const data = await res.json();
      if (data.members) {
        window.teamMembersList = data.members;
        renderDirectorySidebar();
      }
    }
  } catch (e) {
    console.debug('Failed to load members from Member.xlsx:', e);
  }
}

function renderDirectorySidebar() {
  const container = document.getElementById('sidebarRoleList');
  if (!container || !window.teamMembersList.length) return;

  const roleColors = {
    CLIENT: 'role-avatar-client',
    PM: 'role-avatar-pm',
    DEVELOPER: 'role-avatar-dev',
    ADMIN: 'role-avatar-admin',
  };

  const rolePillColors = {
    CLIENT: 'role-client',
    PM: 'role-pm',
    DEVELOPER: 'role-dev',
    ADMIN: 'role-admin',
  };

  container.innerHTML = window.teamMembersList.map(m => {
    const role = (m.role || 'DEVELOPER').toUpperCase();
    const initials = (m.display_name || 'U')
      .split(' ')
      .filter(Boolean)
      .map(p => p[0])
      .slice(0, 2)
      .join('')
      .toUpperCase();

    const shortId = m.user_id ? `${m.user_id.slice(0, 8)}...` : (m.email || 'No ID');
    const avatarClass = roleColors[role] || 'role-avatar-dev';
    const pillClass = rolePillColors[role] || 'role-dev';

    return `
      <div class="role-row">
        <div class="role-avatar ${avatarClass}">${initials}</div>
        <div class="role-details">
          <span class="role-name">${escapeHtml(m.display_name)}</span>
          <span class="role-type ${pillClass}">${role}${m.can_approve ? ' (Approver)' : ''}</span>
          <span class="role-id font-mono">${escapeHtml(shortId)}</span>
        </div>
      </div>
    `;
  }).join('');
}

function getMemberByUserIdOrName(userId, displayName) {
  const normId = (userId || '').toLowerCase().trim();
  const normName = (displayName || '').toLowerCase().trim();
  const list = window.teamMembersList || [];

  if (normId) {
    const f = list.find(m => (m.user_id || '').toLowerCase().trim() === normId);
    if (f) return f;
  }
  if (normName) {
    const f = list.find(m => (m.display_name || '').toLowerCase().trim() === normName);
    if (f) return f;
    const first = normName.split(' ')[0];
    if (first) {
      const pf = list.find(m => (m.display_name || '').toLowerCase().trim().includes(first));
      if (pf) return pf;
    }
  }
  return null;
}

// Identify Role from User ID or Display Name dynamically using Member.xlsx
function getRoleForUserId(userId, displayName) {
  const m = getMemberByUserIdOrName(userId, displayName);
  if (m && m.role) return m.role.toUpperCase();

  const normId = (userId || '').toLowerCase().trim();
  if (systemStatus && systemStatus.roles) {
    if (normId && systemStatus.roles.client && systemStatus.roles.client.id && systemStatus.roles.client.id.toLowerCase() === normId) return 'CLIENT';
    if (normId && systemStatus.roles.pm && systemStatus.roles.pm.id && systemStatus.roles.pm.id.toLowerCase() === normId) return 'PM';
    if (normId && systemStatus.roles.developer && systemStatus.roles.developer.id && systemStatus.roles.developer.id.toLowerCase() === normId) return 'DEVELOPER';
  }

  return 'UNKNOWN';
}

// Render Messages
function renderMessages() {
  const filtered = allMessages.filter(msg => {
    const role = getRoleForUserId(msg.sender_user_id, msg.sender_display_name);
    if (currentFilter !== 'ALL' && role !== currentFilter) return false;

    if (searchQuery) {
      const textMatch = (msg.message_text || '').toLowerCase().includes(searchQuery);
      const nameMatch = (msg.sender_display_name || '').toLowerCase().includes(searchQuery);
      return textMatch || nameMatch;
    }
    return true;
  });

  elFeedCountBadge.innerText = `${filtered.length} of ${allMessages.length} messages`;

  if (filtered.length === 0) {
    elMessageList.innerHTML = '';
    elMessageList.appendChild(elFeedEmptyState);
    elFeedEmptyState.style.display = 'flex';
    return;
  }

  elFeedEmptyState.style.display = 'none';
  elMessageList.innerHTML = '';

  filtered.forEach(msg => {
    const role = getRoleForUserId(msg.sender_user_id, msg.sender_display_name);
    const card = createMessageCard(msg, role);
    elMessageList.appendChild(card);
  });
}

function createMessageCard(msg, role) {
  const card = document.createElement('div');
  card.className = 'message-card';

  const roleClass = role === 'CLIENT' ? 'role-client' : (role === 'PM' ? 'role-pm' : (role === 'DEVELOPER' ? 'role-dev' : 'role-unknown'));
  const avatarClass = role === 'CLIENT' ? 'user-avatar-client' : (role === 'PM' ? 'user-avatar-pm' : (role === 'DEVELOPER' ? 'user-avatar-dev' : ''));

  const initials = (msg.sender_display_name || 'U')
    .split(' ')
    .filter(Boolean)
    .map(p => p[0])
    .slice(0, 2)
    .join('')
    .toUpperCase();

  const formattedTime = msg.received_at || msg.created_at || 'Just now';

  // Parse reactions
  let reactions = [];
  try {
    if (typeof msg.reactions === 'string') {
      reactions = JSON.parse(msg.reactions);
    } else if (Array.isArray(msg.reactions)) {
      reactions = msg.reactions;
    }
  } catch (e) {
    reactions = [];
  }

  // Check if PM approved via Member.xlsx permissions
  // ONLY 'Admission tickets' (🎟️) or 'Ticket' (🎫) count as approval
  function isTicketEmoji(type) {
    if (!type) return false;
    const t = String(type).toLowerCase().trim();
    if (t.includes('🎟') || t.includes('🎫')) return true;
    const cleaned = t.replace(/[-_]/g, ' ').replace(/^:+|:+$/g, '').trim();
    return ['admission ticket', 'admission tickets', 'ticket', 'tickets'].includes(cleaned);
  }

  const pmId = (systemStatus?.roles?.pm?.id || '').toLowerCase().trim();
  const clientId = (systemStatus?.roles?.client?.id || '').toLowerCase().trim();

  const pmApproved = reactions.some(r => {
    const isPos = isTicketEmoji(r.reactionType);
    if (!isPos) return false;

    const m = getMemberByUserIdOrName(r.userId, r.displayName);
    if (m) {
      if ((m.role || '').toUpperCase() === 'PM' || m.can_approve) return true;
      if ((m.role || '').toUpperCase() === 'CLIENT') return true;
    }
    const uId = (r.userId || '').toLowerCase().trim();
    if (pmId && uId === pmId) return true;
    if (clientId && uId === clientId) return true;
    return false;
  });

  // Build reaction badges HTML - ONLY if reactions exist
  let reactionsHtml = '';
  if (reactions.length > 0) {
    reactionsHtml = `
      <div class="reactions-strip">
        ${reactions.map(r => {
          const m = getMemberByUserIdOrName(r.userId, r.displayName);
          const isPm = m ? ((m.role || '').toUpperCase() === 'PM' || m.can_approve) : false;
          let emoji = r.reactionType || '🎟️';
          if (isTicketEmoji(r.reactionType)) {
            emoji = (String(r.reactionType).includes('🎫') || String(r.reactionType).toLowerCase().includes('ticket')) && !String(r.reactionType).toLowerCase().includes('admission') ? '🎫' : '🎟️';
          } else if (r.reactionType === 'like' || r.reactionType === '👍') {
            emoji = '👍';
          } else if (r.reactionType === 'heart' || r.reactionType === '❤️') {
            emoji = '❤️';
          }
          const userName = r.displayName ? ` <span class="reaction-user">(${escapeHtml(r.displayName)})</span>` : '';
          return `<span class="reaction-badge ${isPm ? 'reaction-pm' : ''}" title="${isPm ? 'Approved Reaction' : 'Reaction'}">${emoji}${userName}</span>`;
        }).join('')}
        ${pmApproved ? '<span class="badge-approved">✓ APPROVED (PM)</span>' : ''}
      </div>
    `;
  }

  // Jira Ticket Status / Triage Status
  let statusHtml = '';
  if (msg.jira_issue_key) {
    statusHtml = `
      <div class="jira-created-strip">
        <span class="jira-created-icon">🎟️</span>
        <span class="jira-created-label">Jira Issue:</span>
        <a href="${escapeHtml(msg.jira_issue_url || '#')}" target="_blank" class="jira-ticket-link-badge">
          ${escapeHtml(msg.jira_issue_key)} ↗
        </a>
      </div>
    `;
  } else if (msg.confirmation_status === 'AWAITING_FINAL_CONFIRMATION' || (!msg.jira_issue_key && msg.ai_ticket)) {
    const reminderBadge = msg.reminder_sent_at ? `<span class="badge-reminder-sent" title="15m SLA follow-up sent to PM">⏰ 15m Escalated</span>` : '';
    statusHtml = `
      <div class="pending-triage-strip">
        <span class="pending-triage-label">📋 Awaiting PM Confirmation</span>
        ${reminderBadge}
        <button class="btn-triage-approve btn-approve-card" data-id="${escapeHtml(msg.message_id)}">⚡ Approve in Jira</button>
        <button class="btn-triage-decline btn-decline-card" data-id="${escapeHtml(msg.message_id)}">❌ Decline</button>
        <button class="btn-triage-reminder btn-reminder-card" data-id="${escapeHtml(msg.message_id)}" title="Trigger 15m PM follow-up reminder now (Teams & Outlook)">⏰ Follow-up PM</button>
      </div>
    `;
  } else if (msg.confirmation_status === 'DECLINED') {
    statusHtml = `
      <div class="declined-strip">
        <span>❌ Ticket Creation Declined by PM</span>
      </div>
    `;
  }

  card.innerHTML = `
    <div class="card-top">
      <div class="card-user">
        <div class="user-avatar ${avatarClass}">${initials}</div>
        <div class="user-info-col">
          <span class="user-name">${escapeHtml(msg.sender_display_name || 'Unknown User')}</span>
          <span class="role-tag ${roleClass}">${role}</span>
        </div>
      </div>
      <div class="card-top-right">
        <span class="card-time">${escapeHtml(formattedTime)}</span>
        <button class="btn-inspect-subtle btn-inspect" title="Inspect Message Details" data-id="${msg.id}">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></svg>
        </button>
      </div>
    </div>

    <div class="card-body">${escapeHtml(msg.message_text || '')}</div>
    ${reactionsHtml}
    ${statusHtml}
  `;

  // Attach card triage button listeners
  const btnApprove = card.querySelector('.btn-approve-card');
  if (btnApprove) {
    btnApprove.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        btnApprove.disabled = true;
        btnApprove.innerText = 'Creating...';
        const res = await fetch(`/api/jira/confirm-approval/${msg.message_id}`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
          showToast(`🎉 Jira Ticket Created: ${data.key}!`, 'success');
          await fetchMessages();
        } else {
          showToast(data.detail || 'Approval failed', 'error');
        }
      } catch (err) {
        showToast('Approval network error', 'error');
      } finally {
        btnApprove.disabled = false;
        btnApprove.innerText = '⚡ Approve in Jira';
      }
    });
  }

  const btnDecline = card.querySelector('.btn-decline-card');
  if (btnDecline) {
    btnDecline.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        btnDecline.disabled = true;
        btnDecline.innerText = 'Declining...';
        const res = await fetch(`/api/jira/decline-issue/${msg.message_id}`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
          showToast('Ticket creation declined', 'info');
          await fetchMessages();
        } else {
          showToast(data.detail || 'Decline failed', 'error');
        }
      } catch (err) {
        showToast('Decline network error', 'error');
      } finally {
        btnDecline.disabled = false;
        btnDecline.innerText = '❌ Decline';
      }
    });
  }

  const btnReminder = card.querySelector('.btn-reminder-card');
  if (btnReminder) {
    btnReminder.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        btnReminder.disabled = true;
        btnReminder.innerText = 'Escalating...';
        const res = await fetch(`/api/messages/${msg.message_id}/send-reminder`, { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.success) {
          const method = data.teams?.method || 'delivered';
          showToast(`⏰ Follow-up sent to PM (${data.pm?.name || 'Santosh Yadav'}) via ${method}!`, 'success');
          await fetchMessages();
        } else {
          showToast(data.detail || data.reason || data.error || 'Failed to send reminder', 'error');
        }
      } catch (err) {
        showToast('Escalation network error', 'error');
      } finally {
        btnReminder.disabled = false;
        btnReminder.innerText = '⏰ Follow-up PM';
      }
    });
  }

  // Attach inspect button listener
  const btnInspect = card.querySelector('.btn-inspect');
  if (btnInspect) {
    btnInspect.addEventListener('click', () => {
      openPayloadModal(msg);
    });
  }

  return card;
}

function openPayloadModal(msg) {
  elPayloadCode.innerText = JSON.stringify(msg, null, 2);

  const actionsBar = document.getElementById('payloadActionsBar');
  if (actionsBar) {
    actionsBar.innerHTML = '';

    if (msg.jira_issue_key) {
      const linkJira = document.createElement('a');
      linkJira.className = 'btn btn-primary';
      linkJira.style.fontSize = '12px';
      linkJira.style.padding = '5px 12px';
      linkJira.style.textDecoration = 'none';
      linkJira.href = msg.jira_issue_url || '#';
      linkJira.target = '_blank';
      linkJira.innerHTML = `🎟️ Open ${escapeHtml(msg.jira_issue_key)} in Jira ↗`;
      actionsBar.appendChild(linkJira);
    } else {
      if (msg.confirmation_status === 'AWAITING_FINAL_CONFIRMATION') {
        const btnConfirm = document.createElement('button');
        btnConfirm.className = 'btn btn-primary';
        btnConfirm.style.fontSize = '12px';
        btnConfirm.style.padding = '5px 12px';
        btnConfirm.innerHTML = '⚡ Approve & Create in Jira';
        btnConfirm.addEventListener('click', async () => {
          try {
            btnConfirm.disabled = true;
            btnConfirm.innerText = 'Creating in Jira...';
            const res = await fetch(`/api/jira/confirm-approval/${msg.message_id}`, { method: 'POST' });
            const data = await res.json();
            if (res.ok) {
              showToast(`🎉 Jira Ticket Created: ${data.key}!`, 'success');
              elPayloadModal.classList.remove('active');
              await fetchMessages();
            } else {
              showToast(data.detail || 'Creation failed', 'error');
            }
          } catch (err) {
            showToast('Creation network error', 'error');
          } finally {
            btnConfirm.disabled = false;
            btnConfirm.innerText = '⚡ Approve & Create in Jira';
          }
        });
        actionsBar.appendChild(btnConfirm);

        const btnDecl = document.createElement('button');
        btnDecl.className = 'btn btn-secondary';
        btnDecl.style.fontSize = '12px';
        btnDecl.style.padding = '5px 12px';
        btnDecl.style.color = '#F87171';
        btnDecl.innerHTML = '❌ Decline Ticket Creation';
        btnDecl.addEventListener('click', async () => {
          try {
            btnDecl.disabled = true;
            btnDecl.innerText = 'Declining...';
            const res = await fetch(`/api/jira/decline-approval/${msg.message_id}`, { method: 'POST' });
            const data = await res.json();
            if (res.ok) {
              showToast('❌ Ticket creation declined', 'info');
              elPayloadModal.classList.remove('active');
              await fetchMessages();
            } else {
              showToast(data.detail || 'Decline failed', 'error');
            }
          } catch (err) {
            showToast('Decline network error', 'error');
          } finally {
            btnDecl.disabled = false;
            btnDecl.innerText = '❌ Decline Ticket Creation';
          }
        });
        actionsBar.appendChild(btnDecl);
      }

      const btnPm = document.createElement('button');
      btnPm.className = 'btn btn-secondary';
      btnPm.style.fontSize = '12px';
      btnPm.style.padding = '5px 12px';
      btnPm.innerHTML = '🎟️ Simulate PM 🎟️ Reaction';
      btnPm.addEventListener('click', async () => {
        try {
          btnPm.disabled = true;
          btnPm.innerText = 'Reacting...';
          const res = await fetch(`/api/test/simulate-pm-approval/${msg.message_id}`, { method: 'POST' });
          const data = await res.json();
          if (res.ok) {
            showToast('✓ PM reacted with 🎟️ in Teams!', 'success');
            if (data.ticket && data.ticket.key) {
              showToast(`🎉 Jira Ticket: ${data.ticket.key}!`, 'success');
            } else if (data.ticket && data.ticket.status === 'AWAITING_FINAL_CONFIRMATION') {
              showToast(`📋 Issue triage card sent to Teams (${data.ticket.issues_count} issue(s))!`, 'info');
            }
            elPayloadModal.classList.remove('active');
            await fetchMessages();
          } else {
            showToast(data.detail || 'Reaction failed', 'error');
          }
        } catch (err) {
          showToast('Network error', 'error');
        } finally {
          btnPm.disabled = false;
          btnPm.innerText = '🎟️ Simulate PM 🎟️ Reaction';
        }
      });
      actionsBar.appendChild(btnPm);

      const btnDis = document.createElement('button');
      btnDis.className = 'btn btn-secondary';
      btnDis.style.fontSize = '12px';
      btnDis.style.padding = '5px 12px';
      btnDis.innerHTML = '❌ Simulate PM ❌ Reaction';
      btnDis.addEventListener('click', async () => {
        try {
          btnDis.disabled = true;
          btnDis.innerText = 'Reacting...';
          const res = await fetch(`/api/test/simulate-pm-disapproval/${msg.message_id}`, { method: 'POST' });
          const data = await res.json();
          if (res.ok) {
            showToast('✓ PM reacted with ❌ in Teams!', 'info');
            elPayloadModal.classList.remove('active');
            await fetchMessages();
          } else {
            showToast(data.detail || 'Reaction failed', 'error');
          }
        } catch (err) {
          showToast('Network error', 'error');
        } finally {
          btnDis.disabled = false;
          btnDis.innerText = '❌ Simulate PM ❌ Reaction';
        }
      });
      actionsBar.appendChild(btnDis);

      const btnCreate = document.createElement('button');
      btnCreate.className = 'btn btn-secondary';
      btnCreate.style.fontSize = '12px';
      btnCreate.style.padding = '5px 12px';
      btnCreate.innerHTML = '🚀 Force Create in Jira';
      btnCreate.addEventListener('click', async () => {
        try {
          btnCreate.disabled = true;
          btnCreate.innerText = 'Creating...';
          const res = await fetch(`/api/jira/create-from-message/${msg.message_id}`, { method: 'POST' });
          const data = await res.json();
          if (res.ok) {
            showToast(`🎉 Jira Ticket Created: ${data.key}!`, 'success');
            if (data.url) window.open(data.url, '_blank');
            elPayloadModal.classList.remove('active');
            await fetchMessages();
          } else {
            showToast(data.detail || data.error || 'Failed to create Jira ticket', 'error');
          }
        } catch (err) {
          showToast('Jira API error', 'error');
        } finally {
          btnCreate.disabled = false;
          btnCreate.innerText = '🚀 Force Create in Jira';
        }
      });
      actionsBar.appendChild(btnCreate);

      const btnFollowup = document.createElement('button');
      btnFollowup.className = 'btn btn-secondary';
      btnFollowup.style.fontSize = '12px';
      btnFollowup.style.padding = '5px 12px';
      btnFollowup.innerHTML = '⏰ Test Follow-Up Message';
      btnFollowup.title = 'Send compact one-line PM follow-up reminder with @mention in Teams';
      btnFollowup.addEventListener('click', async () => {
        try {
          btnFollowup.disabled = true;
          btnFollowup.innerText = 'Sending...';
          const res = await fetch(`/api/test/simulate-pm-followup/${msg.message_id}`, { method: 'POST' });
          const data = await res.json();
          if (res.ok && data.success) {
            const method = data.result?.teams?.method || 'delivered';
            showToast(`⏰ Test Follow-Up sent to PM (${method})!`, 'success');
            elPayloadModal.classList.remove('active');
            await fetchMessages();
          } else {
            showToast(data.detail || data.reason || data.error || 'Failed to send follow-up', 'error');
          }
        } catch (err) {
          showToast('Follow-up network error', 'error');
        } finally {
          btnFollowup.disabled = false;
          btnFollowup.innerText = '⏰ Test Follow-Up Message';
        }
      });
      actionsBar.appendChild(btnFollowup);
    }
  }

  elPayloadModal.classList.add('active');
}

// WebSocket Connection
function initWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  try {
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log('Dashboard WebSocket connected');
      document.getElementById('connectionStatus').className = 'status-pill status-pill-online';
      document.getElementById('connectionText').innerText = 'SYSTEM ONLINE';
    };

    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);

        // PM SLA Follow-up Reminder Sent
        if (payload.type === 'PM_REMINDER_SENT') {
          showToast(`⏰ 15m SLA escalation sent to PM (${payload.pmName}) via Teams & Outlook!`, 'info');
          fetchMessages();
          return;
        }

        // Subscription auto-renewed
        if (payload.type === 'subscription_renewed') {
          showToast(payload.message || 'Graph subscription auto-renewed (+58m)!', 'success');
          subRemainingSeconds = payload.remainingSeconds || 3480;
          startCountdownTimer();
          fetchStatus();
          return;
        }

        // New message received (not a duplicate / sync loop)
        if (payload.type === 'NEW_MESSAGE' && payload.stored && !payload.duplicate) {
          const sender = payload.message?.sender?.displayName || 'User';
          showToast(`New Teams message received from ${sender}!`, 'info');
          fetchMessages();
          fetchStatus();
          return;
        }

        // Message updated (reaction added or message edited)
        if (payload.type === 'MESSAGE_UPDATED') {
          const reactions = payload.message?.reactions || [];
          let approverTitle = 'PM';
          const pmApproved = reactions.some(r => {
            const isTicket = isTicketEmoji(r.reactionType);
            if (!isTicket) return false;
            const m = getMemberByUserIdOrName(r.userId, r.displayName);
            const isPm = m ? ((m.role || '').toUpperCase() === 'PM' || m.can_approve) : false;
            if (isPm) {
              approverTitle = `PM ${m?.display_name || 'Approver'}`;
              return true;
            }
            if (m && (m.role || '').toUpperCase() === 'CLIENT') {
              approverTitle = `${m.display_name} (Acting PM)`;
              return true;
            }
            return false;
          });

          if (pmApproved) {
            showToast(`✓ ${approverTitle} approved ticket with 🎟️/🎫 in Teams!`, 'success');
          }

          // Refresh list and metrics
          fetchMessages();
          fetchStatus();
          return;
        }

        if (payload.type === 'JIRA_TICKET_CREATED') {
          showToast(`🎉 Jira Ticket Created: ${payload.issueKey}!`, 'success');
          fetchMessages();
          fetchStatus();
          return;
        }

        if (payload.type === 'STATUS_UPDATE') {
          fetchStatus();
          return;
        }

        // Fallback: refresh if a message payload is present (e.g., simulated messages without explicit type)
        if (payload.message) {
          fetchMessages();
          fetchStatus();
        }
      } catch (err) {
        console.error('Error parsing WS message:', err);
      }
    };

    ws.onclose = () => {
      console.log('WebSocket disconnected, reconnecting in 5s...');
      document.getElementById('connectionStatus').className = 'status-pill';
      document.getElementById('connectionText').innerText = 'RECONNECTING';
      setTimeout(initWebSocket, 5000);
    };

    ws.onerror = (err) => {
      console.error('WebSocket error:', err);
      ws.close();
    };
  } catch (err) {
    console.error('Could not initialize WebSocket:', err);
  }
}

// Toast Notifications with Debouncing and Stack Limits
const _recentToasts = new Map();

function showToast(message, type = 'info') {
  if (!message) return;
  const now = Date.now();

  // Deduplicate: ignore identical toast messages within 3.5 seconds
  if (_recentToasts.has(message)) {
    const lastTime = _recentToasts.get(message);
    if (now - lastTime < 3500) {
      return;
    }
  }
  _recentToasts.set(message, now);

  const container = document.getElementById('toastContainer');
  if (!container) return;

  // Max 2 active toasts to prevent stacking
  while (container.children.length >= 2) {
    container.removeChild(container.firstChild);
  }

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

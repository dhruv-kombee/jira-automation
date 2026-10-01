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
  await fetchStatus();
  await fetchMessages();

  // Periodic status poll as background sync
  setInterval(fetchStatus, 15000);
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

  // Test Jira Ticket button
  const elBtnTestJira = document.getElementById('btnTestJira');
  if (elBtnTestJira) {
    elBtnTestJira.addEventListener('click', async () => {
      try {
        elBtnTestJira.disabled = true;
        elBtnTestJira.innerText = 'Creating Test Ticket...';
        const res = await fetch('/api/jira/test-ticket', { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
          showToast(`Jira Test Ticket Created: ${data.key}!`, 'success');
          if (data.url) window.open(data.url, '_blank');
          await fetchMessages();
        } else {
          showToast(data.detail || 'Test ticket creation failed', 'error');
        }
      } catch (e) {
        showToast('Jira network error', 'error');
      } finally {
        elBtnTestJira.disabled = false;
        elBtnTestJira.innerText = '⚡ Test Ticket';
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
    elTunnelLink.innerText = 'No active tunnel detected';
    elTunnelSubtext.innerText = 'Start ngrok or check configuration';
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

// Identify Role from User ID or Display Name
function getRoleForUserId(userId, displayName) {
  const normId = (userId || '').toLowerCase().trim();
  const normName = (displayName || '').toLowerCase().trim();

  if (systemStatus && systemStatus.roles) {
    if (normId && systemStatus.roles.client && systemStatus.roles.client.id && systemStatus.roles.client.id.toLowerCase() === normId) return 'CLIENT';
    if (normId && systemStatus.roles.pm && systemStatus.roles.pm.id && systemStatus.roles.pm.id.toLowerCase() === normId) return 'PM';
    if (normId && systemStatus.roles.developer && systemStatus.roles.developer.id && systemStatus.roles.developer.id.toLowerCase() === normId) return 'DEVELOPER';
  }

  // Name-based fallback matching
  if (normName.includes('dhruv')) return 'CLIENT';
  if (normName.includes('santosh')) return 'PM';
  if (normName.includes('musaib') || normName.includes('musain')) return 'DEVELOPER';

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

  // Check if PM (Santosh) approved
  const pmId = (systemStatus?.roles?.pm?.id || '').toLowerCase().trim();
  const pmApproved = reactions.some(r => {
    const uId = (r.userId || '').toLowerCase().trim();
    const type = (r.reactionType || r.displayName || '').toLowerCase();
    return uId === pmId && (type === 'like' || type.includes('like') || type.includes('👍') || type === 'heart');
  });

  // Build reaction badges HTML
  let reactionsHtml = '';
  if (reactions.length > 0) {
    reactionsHtml = `
      <div class="reactions-strip">
        ${reactions.map(r => {
      const isPm = (r.userId || '').toLowerCase().trim() === pmId;
      const emoji = (r.reactionType === 'like' || r.reactionType === '👍' || (r.displayName || '').toLowerCase() === 'like') ? '👍' : (r.reactionType === 'heart' ? '❤️' : (r.reactionType || '👍'));
      return `<span class="reaction-badge ${isPm ? 'reaction-pm' : ''}" title="${isPm ? 'PM Approved (Santosh)' : 'Reaction'}">${emoji}</span>`;
    }).join('')}
        ${pmApproved ? '<span class="badge-approved">✓ APPROVED BY PM (Santosh)</span>' : ''}
      </div>
    `;
  } else if (role === 'CLIENT') {
    reactionsHtml = `
      <div class="reactions-strip">
        <span class="badge-pending-reaction">⏳ Awaiting PM Reaction (👍) in Teams</span>
      </div>
    `;
  }

  // Parse AI-extracted Jira ticket
  let aiTicket = null;
  try {
    if (typeof msg.ai_ticket === 'string') {
      aiTicket = JSON.parse(msg.ai_ticket);
    } else if (msg.ai_ticket && typeof msg.ai_ticket === 'object') {
      aiTicket = msg.ai_ticket;
    }
  } catch (e) {
    aiTicket = null;
  }

  let aiTicketHtml = '';
  if (aiTicket && aiTicket.is_ticket_request) {
    const isBug = (aiTicket.issue_type || '').toLowerCase() === 'bug';
    const typeColor = isBug ? '#EF4444' : '#3B82F6';
    const isHighPrio = ['high', 'highest'].includes((aiTicket.priority || '').toLowerCase());
    const prioColor = isHighPrio ? '#F59E0B' : '#10B981';

    aiTicketHtml = `
      <div class="ai-ticket-box">
        <div class="ai-ticket-header">
          <div class="ai-ticket-title-row">
            <span class="ai-sparkle">✨</span>
            <span class="ai-header-badge">AI EXTRACTED JIRA TICKET</span>
            <span class="ai-type-badge" style="background: ${typeColor}22; color: ${typeColor}; border: 1px solid ${typeColor}55;">${escapeHtml(aiTicket.issue_type || 'Task')}</span>
            <span class="ai-prio-badge" style="background: ${prioColor}22; color: ${prioColor}; border: 1px solid ${prioColor}55;">${escapeHtml(aiTicket.priority || 'Medium')}</span>
          </div>
          <span class="ai-extractor-tag font-mono">${escapeHtml(aiTicket.extractor || 'Gemini')}</span>
        </div>
        <div class="ai-ticket-summary">${escapeHtml(aiTicket.summary || '')}</div>
        <div class="ai-ticket-details">
          ${aiTicket.suggested_assignee ? `<span class="ai-assignee-tag">👤 Assignee: <strong>${escapeHtml(aiTicket.suggested_assignee)}</strong></span>` : ''}
          ${(aiTicket.labels || []).map(l => `<span class="ai-label-pill">#${escapeHtml(l)}</span>`).join('')}
        </div>
      </div>
    `;
  }

  card.innerHTML = `
    <div class="card-top">
      <div class="card-user">
        <div class="user-avatar ${avatarClass}">${initials}</div>
        <div>
          <span class="user-name">${escapeHtml(msg.sender_display_name || 'Unknown User')}</span>
          <span class="role-tag ${roleClass}" style="margin-left: 0.5rem;">${role}</span>
        </div>
      </div>
      <span class="card-time">${formattedTime}</span>
    </div>

    <div class="card-body">${escapeHtml(msg.message_text || '')}</div>
    ${aiTicketHtml}
    ${reactionsHtml}

    <div class="card-footer">
      <div class="card-meta-tags">
        <span class="meta-tag">MSG: ${msg.message_id ? msg.message_id.slice(-6) : 'N/A'}</span>
        ${msg.chat_id ? '<span class="meta-tag">Group Chat</span>' : '<span class="meta-tag">Channel</span>'}
      </div>
      <div class="card-actions">
        ${msg.jira_issue_key ? `<a href="${escapeHtml(msg.jira_issue_url || '#')}" target="_blank" class="jira-ticket-link-badge">🎟️ Jira: ${escapeHtml(msg.jira_issue_key)} ↗</a>` : ''}
        ${!msg.jira_issue_key && aiTicket && aiTicket.is_ticket_request ? `
          <button class="btn-text btn-create-jira" data-mid="${msg.message_id}">🚀 Create in Jira</button>
          <button class="btn-text btn-sim-pm" data-mid="${msg.message_id}">👍 PM Approve</button>
        ` : ''}
        ${!aiTicket ? `<button class="btn-text btn-extract-ai" data-mid="${msg.message_id}">✨ Extract Jira Ticket</button>` : ''}
        <button class="btn-text btn-inspect" data-id="${msg.id}">Inspect Payload</button>
      </div>
    </div>
  `;

  // Attach button listeners
  const btnSimPm = card.querySelector('.btn-sim-pm');
  if (btnSimPm) {
    btnSimPm.addEventListener('click', async () => {
      try {
        btnSimPm.disabled = true;
        btnSimPm.innerText = 'Approving...';
        const res = await fetch(`/api/test/simulate-pm-approval/${btnSimPm.dataset.mid}`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
          showToast('✓ PM Santosh Yadav approved with 👍 in Teams!', 'success');
          if (data.ticket && data.ticket.key) {
            showToast(`🎉 Auto-created Jira Ticket: ${data.ticket.key}!`, 'success');
          }
          await fetchMessages();
        } else {
          showToast(data.detail || 'Approval failed', 'error');
        }
      } catch (err) {
        showToast('Approval network error', 'error');
      } finally {
        btnSimPm.disabled = false;
        btnSimPm.innerText = '👍 PM Approve';
      }
    });
  }

  const btnCreateJira = card.querySelector('.btn-create-jira');
  if (btnCreateJira) {
    btnCreateJira.addEventListener('click', async () => {
      try {
        btnCreateJira.disabled = true;
        btnCreateJira.innerText = 'Creating in Jira...';
        const res = await fetch(`/api/jira/create-from-message/${btnCreateJira.dataset.mid}`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
          showToast(`🎉 Jira Ticket Created: ${data.key}!`, 'success');
          if (data.url) window.open(data.url, '_blank');
          await fetchMessages();
        } else {
          showToast(data.detail || data.error || 'Failed to create Jira ticket', 'error');
        }
      } catch (err) {
        showToast('Jira API error', 'error');
      } finally {
        btnCreateJira.disabled = false;
        btnCreateJira.innerText = '🚀 Create in Jira';
      }
    });
  }

  const btnExtract = card.querySelector('.btn-extract-ai');
  if (btnExtract) {
    btnExtract.addEventListener('click', async () => {
      try {
        btnExtract.disabled = true;
        btnExtract.innerText = 'Extracting...';
        const res = await fetch(`/api/messages/${btnExtract.dataset.mid}/extract-ticket`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
          showToast(`Jira ticket extracted: ${data.aiTicket?.summary || 'Success'}`, 'success');
          await fetchMessages();
        } else {
          showToast(data.detail || 'Extraction failed', 'error');
        }
      } catch (err) {
        showToast('Error extracting ticket', 'error');
      } finally {
        btnExtract.disabled = false;
        btnExtract.innerText = '✨ Extract Jira Ticket';
      }
    });
  }

  card.querySelector('.btn-inspect').addEventListener('click', () => {
    elPayloadCode.innerText = JSON.stringify(msg, null, 2);
    elPayloadModal.classList.add('active');
  });

  return card;
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
          const pmId = (systemStatus?.roles?.pm?.id || '').toLowerCase().trim();
          const pmApproved = reactions.some(r => {
            const uId = (r.userId || '').toLowerCase().trim();
            const type = (r.reactionType || r.displayName || '').toLowerCase();
            return uId === pmId && (type === 'like' || type.includes('like') || type.includes('👍') || type === 'heart');
          });

          if (pmApproved) {
            showToast('✓ PM Santosh Yadav approved ticket with 👍 in Teams!', 'success');
          } else {
            showToast('Message updated in Teams (reaction or edit)', 'info');
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

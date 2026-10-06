/**
 * Management Hub & Settings Console — Client Logic
 */

document.addEventListener('DOMContentLoaded', () => {
  // State
  let configData = null;
  let membersList = [];
  let channelsList = [];
  let hasUnsavedChanges = false;

  // DOM Elements - Navigation & Metrics
  const tabButtons = document.querySelectorAll('.tab-btn');
  const tabContents = document.querySelectorAll('.tab-content');
  const btnTopSave = document.getElementById('btnTopSave');
  const btnFloatingSave = document.getElementById('btnFloatingSave');
  const floatingSaveBar = document.getElementById('floatingSaveBar');
  const toastNotification = document.getElementById('toastNotification');

  const statTotalMembers = document.getElementById('statTotalMembers');
  const statTotalChannels = document.getElementById('statTotalChannels');
  const statJiraProject = document.getElementById('statJiraProject');
  const statAiEngine = document.getElementById('statAiEngine');
  const badgeMemberCount = document.getElementById('badgeMemberCount');
  const badgeChannelCount = document.getElementById('badgeChannelCount');

  // DOM Elements - Members
  const membersTableBody = document.getElementById('membersTableBody');
  const btnOpenAddMemberModal = document.getElementById('btnOpenAddMemberModal');
  const modalMember = document.getElementById('modalMember');
  const modalMemberTitle = document.getElementById('modalMemberTitle');
  const editMemberId = document.getElementById('editMemberId');
  const memberDisplayName = document.getElementById('memberDisplayName');
  const memberRole = document.getElementById('memberRole');
  const memberSpecialty = document.getElementById('memberSpecialty');
  const memberEmail = document.getElementById('memberEmail');
  const memberUserId = document.getElementById('memberUserId');
  const memberCanApprove = document.getElementById('memberCanApprove');
  const btnSaveMember = document.getElementById('btnSaveMember');

  // DOM Elements - Members Import
  const btnOpenImportModal = document.getElementById('btnOpenImportModal');
  const modalImportMembers = document.getElementById('modalImportMembers');
  const importSubtabButtons = document.querySelectorAll('.import-subtab-btn');
  const importTabPanes = document.querySelectorAll('.import-tab-pane');
  const importSheetUrl = document.getElementById('importSheetUrl');
  const btnFetchSheetUrl = document.getElementById('btnFetchSheetUrl');
  const importCsvRaw = document.getElementById('importCsvRaw');
  const btnParseRawCsv = document.getElementById('btnParseRawCsv');
  const fileDropzone = document.getElementById('fileDropzone');
  const memberFileInput = document.getElementById('memberFileInput');
  const selectedFileName = document.getElementById('selectedFileName');
  const btnInspectFile = document.getElementById('btnInspectFile');
  const labelStrategyUpsert = document.getElementById('labelStrategyUpsert');
  const labelStrategyReplace = document.getElementById('labelStrategyReplace');
  const importPreviewArea = document.getElementById('importPreviewArea');
  const previewSummaryText = document.getElementById('previewSummaryText');
  const previewTableBody = document.getElementById('previewTableBody');
  const importErrorMessage = document.getElementById('importErrorMessage');
  const btnConfirmImport = document.getElementById('btnConfirmImport');

  // DOM Elements - Channels
  const channelsTableBody = document.getElementById('channelsTableBody');
  const btnOpenAddChannelModal = document.getElementById('btnOpenAddChannelModal');
  const modalChannel = document.getElementById('modalChannel');
  const modalChannelTitle = document.getElementById('modalChannelTitle');
  const editChannelId = document.getElementById('editChannelId');
  const channelName = document.getElementById('channelName');
  const channelType = document.getElementById('channelType');
  const channelUsage = document.getElementById('channelUsage');
  const channelChatId = document.getElementById('channelChatId');
  const channelTeamId = document.getElementById('channelTeamId');
  const channelChannelId = document.getElementById('channelChannelId');
  const channelWebhookUrl = document.getElementById('channelWebhookUrl');
  const groupChatIdField = document.getElementById('groupChatIdField');
  const groupTeamIdField = document.getElementById('groupTeamIdField');
  const btnSaveChannel = document.getElementById('btnSaveChannel');

  // DOM Elements - Config Fields
  const jiraBaseUrl = document.getElementById('jiraBaseUrl');
  const jiraProjectKey = document.getElementById('jiraProjectKey');
  const jiraEmail = document.getElementById('jiraEmail');
  const jiraApiToken = document.getElementById('jiraApiToken');
  const jiraDefaultIssueType = document.getElementById('jiraDefaultIssueType');
  const btnTestJira = document.getElementById('btnTestJira');
  const jiraTestResult = document.getElementById('jiraTestResult');

  const geminiModel = document.getElementById('geminiModel');
  const geminiApiKey1 = document.getElementById('geminiApiKey1');
  const geminiApiKey2 = document.getElementById('geminiApiKey2');
  const geminiApiKey3 = document.getElementById('geminiApiKey3');
  const btnTestGemini = document.getElementById('btnTestGemini');
  const geminiTestResult = document.getElementById('geminiTestResult');

  const webhookPublicUrl = document.getElementById('webhookPublicUrl');
  const teamsWebhookUrl = document.getElementById('teamsWebhookUrl');
  const allowSelfApproval = document.getElementById('allowSelfApproval');
  const btnTestTeamsWebhook = document.getElementById('btnTestTeamsWebhook');
  const teamsTestResult = document.getElementById('teamsTestResult');

  // DOM Elements - Audit Log
  const auditTableBody = document.getElementById('auditTableBody');
  const btnRefreshAudit = document.getElementById('btnRefreshAudit');

  // =========================================================================
  // Tabs Navigation
  // =========================================================================
  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      tabButtons.forEach(b => b.classList.remove('active'));
      tabContents.forEach(c => c.classList.remove('active'));

      btn.classList.add('active');
      const targetId = btn.getAttribute('data-tab');
      const targetContent = document.getElementById(targetId);
      if (targetContent) {
        targetContent.classList.add('active');
      }

      if (targetId === 'tab-audit') {
        loadAuditLog();
      }
    });
  });

  // =========================================================================
  // Toast Helper
  // =========================================================================
  function showToast(message, type = 'success') {
    toastNotification.textContent = message;
    toastNotification.className = `toast-box show toast-${type}`;
    setTimeout(() => {
      toastNotification.classList.remove('show');
    }, 3800);
  }

  function markUnsaved() {
    hasUnsavedChanges = true;
    floatingSaveBar.classList.add('visible');
  }

  function clearUnsaved() {
    hasUnsavedChanges = false;
    floatingSaveBar.classList.remove('visible');
  }

  // =========================================================================
  // Toggle Secret Visibility
  // =========================================================================
  document.querySelectorAll('.btn-toggle-secret').forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.getAttribute('data-target');
      const input = document.getElementById(targetId);
      if (input) {
        if (input.type === 'password') {
          input.type = 'text';
          btn.style.color = 'var(--accent-primary)';
        } else {
          input.type = 'password';
          btn.style.color = 'var(--text-muted)';
        }
      }
    });
  });

  // Track changes on config form inputs
  const configInputs = [
    jiraBaseUrl, jiraProjectKey, jiraEmail, jiraApiToken, jiraDefaultIssueType,
    geminiModel, geminiApiKey1, geminiApiKey2, geminiApiKey3,
    webhookPublicUrl, teamsWebhookUrl, allowSelfApproval
  ];
  configInputs.forEach(el => {
    if (el) {
      el.addEventListener('input', markUnsaved);
      el.addEventListener('change', markUnsaved);
    }
  });

  // =========================================================================
  // Load Initial Data
  // =========================================================================
  async function loadAllData() {
    await Promise.all([
      loadOverview(),
      loadMembers(),
      loadChannels(),
      loadConfig(),
    ]);
  }

  async function loadOverview() {
    try {
      const res = await fetch('/api/admin/overview');
      const data = await res.json();
      if (data.success) {
        statTotalMembers.textContent = data.team.total;
        statTotalChannels.textContent = data.channels.total;
        statJiraProject.textContent = data.jira.projectKey || 'SCRUM';
        statAiEngine.textContent = data.gemini.model.includes('lite') ? 'Gemini Flash-Lite' : data.gemini.model;

        badgeMemberCount.textContent = data.team.total;
        badgeChannelCount.textContent = data.channels.total;
      }
    } catch (err) {
      console.error('Could not load admin overview:', err);
    }
  }

  // =========================================================================
  // Team Members
  // =========================================================================
  async function loadMembers() {
    try {
      const res = await fetch('/api/admin/members');
      const data = await res.json();
      if (data.success) {
        membersList = data.members || [];
        renderMembers();
      }
    } catch (err) {
      console.error('Could not load members:', err);
      membersTableBody.innerHTML = `<tr><td colspan="7" style="color:var(--accent-rose); text-align:center; padding:1.5rem;">Failed to load members.</td></tr>`;
    }
  }

  function getAvatarClass(role) {
    const r = (role || '').toUpperCase();
    if (r === 'CLIENT') return 'avatar-client';
    if (r === 'PM') return 'avatar-pm';
    if (r === 'ADMIN') return 'avatar-admin';
    return 'avatar-dev';
  }

  function renderMembers() {
    if (!membersList.length) {
      membersTableBody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:2rem; color:var(--text-muted);">No team members found. Click "Add Team Member" above.</td></tr>`;
      return;
    }

    membersTableBody.innerHTML = membersList.map(m => {
      const initial = (m.display_name || 'U').charAt(0).toUpperCase();
      const avatarClass = getAvatarClass(m.role);
      const roleUpper = (m.role || 'DEVELOPER').toUpperCase();

      return `
        <tr data-id="${m.id}">
          <td>
            <div class="user-avatar-cell">
              <div class="user-avatar ${avatarClass}">${initial}</div>
              <div class="user-names-col">
                <span class="user-display-name">${escapeHtml(m.display_name)}</span>
                <span class="user-email-text">${escapeHtml(m.email || 'No email')}</span>
              </div>
            </div>
          </td>
          <td>
            <select class="role-select member-role-dropdown" data-id="${m.id}" title="Change role for this user">
              <option value="CLIENT" ${roleUpper === 'CLIENT' ? 'selected' : ''}>👤 Client</option>
              <option value="PM" ${roleUpper === 'PM' ? 'selected' : ''}>👑 PM (Approver)</option>
              <option value="DEVELOPER" ${roleUpper === 'DEVELOPER' ? 'selected' : ''}>💻 Developer</option>
              <option value="ADMIN" ${roleUpper === 'ADMIN' ? 'selected' : ''}>🛡️ Admin</option>
            </select>
          </td>
          <td>
            <span style="color:${m.specialty ? 'var(--text-main)' : 'var(--text-faint)'}; font-size:0.85rem;">
              ${escapeHtml(m.specialty || 'General')}
            </span>
          </td>
          <td>
            <code style="font-size:0.75rem; color:var(--text-muted); background:rgba(255,255,255,0.05); padding:0.2rem 0.4rem; border-radius:4px;">
              ${m.user_id ? escapeHtml(m.user_id.length > 18 ? m.user_id.substring(0, 15) + '...' : m.user_id) : 'By Name'}
            </code>
          </td>
          <td>
            <label class="switch-container" title="Allows approving tickets with emojis">
              <input type="checkbox" class="switch-input member-approve-toggle" data-id="${m.id}" ${m.can_approve ? 'checked' : ''}>
              <span class="switch-slider"></span>
            </label>
          </td>
          <td>
            <span class="badge-mini ${m.is_active ? 'badge-confirmed' : 'badge-general'}">
              ${m.is_active ? 'Active' : 'Disabled'}
            </span>
          </td>
          <td style="text-align:right;">
            <div style="display:inline-flex; gap:0.4rem;">
              <button class="btn btn-icon btn-edit-member" data-id="${m.id}" title="Edit details">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
              </button>
              <button class="btn btn-icon btn-delete-member" data-id="${m.id}" style="color:var(--accent-rose);" title="Remove member">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              </button>
            </div>
          </td>
        </tr>
      `;
    }).join('');

    // Attach Event Listeners for inline role changes
    document.querySelectorAll('.member-role-dropdown').forEach(select => {
      select.addEventListener('change', async (e) => {
        const id = e.target.getAttribute('data-id');
        const newRole = e.target.value;
        const member = membersList.find(m => String(m.id) === String(id));
        const name = member ? member.display_name : 'Member';

        try {
          const res = await fetch(`/api/admin/members/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role: newRole }),
          });
          const data = await res.json();
          if (data.success) {
            showToast(`Updated ${name}'s role to ${newRole}`);
            loadOverview();
            loadMembers();
          } else {
            showToast(data.detail || 'Could not update role', 'error');
          }
        } catch (err) {
          showToast(`Error updating role: ${err}`, 'error');
        }
      });
    });

    // Attach Event Listeners for inline approval toggle
    document.querySelectorAll('.member-approve-toggle').forEach(chk => {
      chk.addEventListener('change', async (e) => {
        const id = e.target.getAttribute('data-id');
        const canApprove = e.target.checked;
        try {
          await fetch(`/api/admin/members/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ can_approve: canApprove }),
          });
          showToast(`Approval permission updated`);
        } catch (err) {
          showToast(`Error updating permission: ${err}`, 'error');
        }
      });
    });

    // Attach Edit & Delete buttons
    document.querySelectorAll('.btn-edit-member').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-id');
        openEditMemberModal(id);
      });
    });

    document.querySelectorAll('.btn-delete-member').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-id');
        deleteMember(id);
      });
    });
  }

  // Member Modal logic
  btnOpenAddMemberModal.addEventListener('click', () => {
    editMemberId.value = '';
    modalMemberTitle.textContent = 'Add Team Member';
    memberDisplayName.value = '';
    memberRole.value = 'DEVELOPER';
    memberSpecialty.value = '';
    memberEmail.value = '';
    memberUserId.value = '';
    memberCanApprove.checked = false;
    modalMember.classList.add('active');
  });

  function openEditMemberModal(id) {
    const m = membersList.find(item => String(item.id) === String(id));
    if (!m) return;
    editMemberId.value = m.id;
    modalMemberTitle.textContent = `Edit Member: ${m.display_name}`;
    memberDisplayName.value = m.display_name || '';
    memberRole.value = (m.role || 'DEVELOPER').toUpperCase();
    memberSpecialty.value = m.specialty || '';
    memberEmail.value = m.email || '';
    memberUserId.value = m.user_id || '';
    memberCanApprove.checked = Boolean(m.can_approve);
    modalMember.classList.add('active');
  }

  btnSaveMember.addEventListener('click', async () => {
    const name = memberDisplayName.value.trim();
    if (!name) {
      showToast('Please enter the full name', 'error');
      return;
    }

    const payload = {
      display_name: name,
      role: memberRole.value,
      specialty: memberSpecialty.value.trim(),
      email: memberEmail.value.trim(),
      user_id: memberUserId.value.trim(),
      can_approve: memberCanApprove.checked,
    };

    const id = editMemberId.value;
    try {
      let res;
      if (id) {
        res = await fetch(`/api/admin/members/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      } else {
        res = await fetch('/api/admin/members', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }

      const data = await res.json();
      if (data.success) {
        showToast(id ? 'Member updated successfully' : 'Member added successfully');
        modalMember.classList.remove('active');
        loadMembers();
        loadOverview();
      } else {
        showToast(data.detail || 'Failed to save member', 'error');
      }
    } catch (err) {
      showToast(`Save error: ${err}`, 'error');
    }
  });

  async function deleteMember(id) {
    const m = membersList.find(item => String(item.id) === String(id));
    const name = m ? m.display_name : 'this member';
    if (!confirm(`Are you sure you want to remove ${name}?`)) return;

    try {
      const res = await fetch(`/api/admin/members/${id}`, { method: 'DELETE' });
      const data = await res.json();
      if (data.success) {
        showToast(`Removed ${name}`);
        loadMembers();
        loadOverview();
      }
    } catch (err) {
      showToast(`Error deleting member: ${err}`, 'error');
    }
  }

  // =========================================================================
  // Bulk Import Team Members (Spreadsheet / Google Sheets / CSV)
  // =========================================================================
  let parsedImportMembers = [];
  let currentUploadedFile = null;

  function resetImportModal() {
    parsedImportMembers = [];
    currentUploadedFile = null;
    if (importSheetUrl) importSheetUrl.value = '';
    if (importCsvRaw) importCsvRaw.value = '';
    if (memberFileInput) memberFileInput.value = '';
    if (selectedFileName) {
      selectedFileName.textContent = '';
      selectedFileName.style.display = 'none';
    }
    if (btnInspectFile) btnInspectFile.style.display = 'none';
    if (importPreviewArea) importPreviewArea.style.display = 'none';
    if (previewTableBody) previewTableBody.innerHTML = '';
    if (importErrorMessage) {
      importErrorMessage.textContent = '';
      importErrorMessage.style.display = 'none';
    }
    if (btnConfirmImport) {
      btnConfirmImport.disabled = true;
      btnConfirmImport.style.opacity = '0.5';
      btnConfirmImport.style.cursor = 'not-allowed';
      btnConfirmImport.textContent = 'Confirm & Import';
    }
    const upsertRadio = document.querySelector('input[name="importStrategy"][value="upsert"]');
    if (upsertRadio) upsertRadio.checked = true;
    if (labelStrategyUpsert) labelStrategyUpsert.classList.add('selected');
    if (labelStrategyReplace) labelStrategyReplace.classList.remove('selected');
  }

  if (btnOpenImportModal) {
    btnOpenImportModal.addEventListener('click', () => {
      resetImportModal();
      modalImportMembers.classList.add('active');
    });
  }

  // Strategy Radio Toggle Styling
  document.querySelectorAll('input[name="importStrategy"]').forEach(radio => {
    radio.addEventListener('change', (e) => {
      if (e.target.value === 'replace') {
        if (labelStrategyReplace) labelStrategyReplace.classList.add('selected');
        if (labelStrategyUpsert) labelStrategyUpsert.classList.remove('selected');
      } else {
        if (labelStrategyUpsert) labelStrategyUpsert.classList.add('selected');
        if (labelStrategyReplace) labelStrategyReplace.classList.remove('selected');
      }
    });
  });

  // Modal Subtabs (Link / CSV vs File Upload)
  importSubtabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      importSubtabButtons.forEach(b => b.classList.remove('active'));
      importTabPanes.forEach(p => p.classList.remove('active'));

      btn.classList.add('active');
      const targetPane = document.getElementById(btn.getAttribute('data-subtab'));
      if (targetPane) targetPane.classList.add('active');
    });
  });

  // Display error helper
  function showImportError(msg) {
    if (!importErrorMessage) return;
    importErrorMessage.textContent = msg;
    importErrorMessage.style.display = 'block';
    if (importPreviewArea) importPreviewArea.style.display = 'none';
    if (btnConfirmImport) {
      btnConfirmImport.disabled = true;
      btnConfirmImport.style.opacity = '0.5';
      btnConfirmImport.style.cursor = 'not-allowed';
      btnConfirmImport.textContent = 'Confirm & Import';
    }
  }

  function hideImportError() {
    if (!importErrorMessage) return;
    importErrorMessage.textContent = '';
    importErrorMessage.style.display = 'none';
  }

  // Render preview table
  function renderImportPreview(members) {
    parsedImportMembers = members || [];
    hideImportError();

    if (!parsedImportMembers.length) {
      showImportError('No valid member rows could be detected. Please verify your column headers (e.g. Name, Email, Role, Specialty).');
      return;
    }

    if (previewSummaryText) {
      previewSummaryText.textContent = `Found ${parsedImportMembers.length} valid members ready to import`;
    }

    if (previewTableBody) {
      previewTableBody.innerHTML = parsedImportMembers.map((m, idx) => {
        const roleUpper = (m.role || 'DEVELOPER').toUpperCase();
        let roleBadgeClass = 'badge-general';
        let roleIcon = '💻';
        if (roleUpper === 'CLIENT') { roleBadgeClass = 'badge-confirmed'; roleIcon = '👤'; }
        else if (roleUpper === 'PM') { roleBadgeClass = 'badge-reminder'; roleIcon = '👑'; }
        else if (roleUpper === 'ADMIN') { roleBadgeClass = 'badge-triage'; roleIcon = '🛡️'; }

        return `
          <tr>
            <td style="color:var(--text-muted); font-size:0.75rem;">${idx + 1}</td>
            <td><strong>${escapeHtml(m.display_name || '—')}</strong></td>
            <td><code style="color:var(--accent-primary); font-size:0.78rem;">${escapeHtml(m.email || '—')}</code></td>
            <td>
              <span class="badge-mini ${roleBadgeClass}" style="font-size:0.75rem;">
                ${roleIcon} ${roleUpper}
              </span>
            </td>
            <td><span style="font-size:0.8rem; color:var(--text-muted);">${escapeHtml(m.specialty || 'General')}</span></td>
            <td><code style="font-size:0.75rem; color:var(--text-faint);">${escapeHtml(m.user_id ? (m.user_id.length > 15 ? m.user_id.substring(0, 12) + '...' : m.user_id) : '—')}</code></td>
            <td style="text-align:center;">
              <span style="color:${m.can_approve ? '#34d399' : 'var(--text-faint)'}; font-size:0.9rem;">
                ${m.can_approve ? '✓ Yes' : '—'}
              </span>
            </td>
          </tr>
        `;
      }).join('');
    }

    if (importPreviewArea) importPreviewArea.style.display = 'flex';

    if (btnConfirmImport) {
      btnConfirmImport.disabled = false;
      btnConfirmImport.style.opacity = '1';
      btnConfirmImport.style.cursor = 'pointer';
      btnConfirmImport.textContent = `Confirm & Import (${parsedImportMembers.length} members)`;
    }
  }

  // Parse via Link
  if (btnFetchSheetUrl) {
    btnFetchSheetUrl.addEventListener('click', async () => {
      const url = (importSheetUrl.value || '').trim();
      if (!url) {
        showImportError('Please enter a Google Sheets URL or public CSV URL.');
        return;
      }
      hideImportError();
      const origText = btnFetchSheetUrl.innerHTML;
      btnFetchSheetUrl.innerHTML = '<span>⏳ Inspecting...</span>';
      btnFetchSheetUrl.disabled = true;

      try {
        const res = await fetch('/api/admin/members/parse-sheet', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: url }),
        });
        const data = await res.json();
        if (data.success && data.preview) {
          renderImportPreview(data.preview);
        } else {
          showImportError(data.detail || 'Could not parse spreadsheet from the provided URL.');
        }
      } catch (err) {
        showImportError(`Network error inspecting link: ${err.message || err}`);
      } finally {
        btnFetchSheetUrl.innerHTML = origText;
        btnFetchSheetUrl.disabled = false;
      }
    });
  }

  // Parse via Raw CSV
  if (btnParseRawCsv) {
    btnParseRawCsv.addEventListener('click', async () => {
      const csv = (importCsvRaw.value || '').trim();
      if (!csv) {
        showImportError('Please paste CSV text to inspect.');
        return;
      }
      hideImportError();
      const origText = btnParseRawCsv.innerHTML;
      btnParseRawCsv.innerHTML = '<span>⏳ Inspecting...</span>';
      btnParseRawCsv.disabled = true;

      try {
        const res = await fetch('/api/admin/members/parse-sheet', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ csv_text: csv }),
        });
        const data = await res.json();
        if (data.success && data.preview) {
          renderImportPreview(data.preview);
        } else {
          showImportError(data.detail || 'Could not parse the pasted CSV.');
        }
      } catch (err) {
        showImportError(`Error inspecting CSV: ${err.message || err}`);
      } finally {
        btnParseRawCsv.innerHTML = origText;
        btnParseRawCsv.disabled = false;
      }
    });
  }

  // File Dropzone Handling
  if (fileDropzone && memberFileInput) {
    fileDropzone.addEventListener('click', () => {
      memberFileInput.click();
    });

    ['dragenter', 'dragover'].forEach(evt => {
      fileDropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        fileDropzone.classList.add('dragover');
      });
    });

    ['dragleave', 'drop'].forEach(evt => {
      fileDropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        e.stopPropagation();
        fileDropzone.classList.remove('dragover');
      });
    });

    fileDropzone.addEventListener('drop', (e) => {
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
        handleFileSelected(e.dataTransfer.files[0]);
      }
    });

    memberFileInput.addEventListener('change', (e) => {
      if (e.target.files && e.target.files.length) {
        handleFileSelected(e.target.files[0]);
      }
    });
  }

  function handleFileSelected(file) {
    if (!file) return;
    currentUploadedFile = file;
    const sizeKb = Math.round(file.size / 1024);
    if (selectedFileName) {
      selectedFileName.textContent = `📄 Selected: ${file.name} (${sizeKb} KB)`;
      selectedFileName.style.display = 'block';
    }
    if (btnInspectFile) {
      btnInspectFile.style.display = 'inline-flex';
    }
    inspectSelectedFile(file);
  }

  if (btnInspectFile) {
    btnInspectFile.addEventListener('click', () => {
      if (currentUploadedFile) {
        inspectSelectedFile(currentUploadedFile);
      }
    });
  }

  async function inspectSelectedFile(file) {
    hideImportError();
    if (btnInspectFile) {
      btnInspectFile.disabled = true;
      btnInspectFile.innerHTML = '<span>⏳ Inspecting File...</span>';
    }

    try {
      const formData = new FormData();
      formData.append('file', file);

      const res = await fetch('/api/admin/members/parse-file', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (data.success && data.preview) {
        renderImportPreview(data.preview);
      } else {
        showImportError(data.detail || 'Could not parse the selected file.');
      }
    } catch (err) {
      showImportError(`Error reading spreadsheet: ${err.message || err}`);
    } finally {
      if (btnInspectFile) {
        btnInspectFile.disabled = false;
        btnInspectFile.innerHTML = '<span>⚡ Inspect Selected File</span>';
      }
    }
  }

  // Confirm & Import
  if (btnConfirmImport) {
    btnConfirmImport.addEventListener('click', async () => {
      if (!parsedImportMembers.length) {
        showToast('Please inspect a spreadsheet or link first', 'error');
        return;
      }

      const strategyRadio = document.querySelector('input[name="importStrategy"]:checked');
      const strategy = strategyRadio ? strategyRadio.value : 'upsert';

      if (strategy === 'replace') {
        const ok = confirm(`⚠️ CAUTION: "Replace All" will remove all existing team members and replace them with these ${parsedImportMembers.length} imported members.\n\nDo you want to continue?`);
        if (!ok) return;
      }

      const origText = btnConfirmImport.textContent;
      btnConfirmImport.disabled = true;
      btnConfirmImport.textContent = 'Importing...';

      try {
        const res = await fetch('/api/admin/members/import-json', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            members: parsedImportMembers,
            strategy: strategy,
          }),
        });

        const data = await res.json();
        if (data.success) {
          showToast(`Successfully imported ${data.imported_count} new, updated ${data.updated_count} members!`);
          modalImportMembers.classList.remove('active');
          await loadMembers();
          await loadOverview();
        } else {
          showImportError(data.detail || 'Import failed. Please check the spreadsheet data.');
          showToast(data.detail || 'Import failed', 'error');
        }
      } catch (err) {
        showImportError(`Error during import: ${err.message || err}`);
        showToast(`Import error: ${err.message || err}`, 'error');
      } finally {
        btnConfirmImport.disabled = false;
        btnConfirmImport.textContent = origText;
      }
    });
  }

  // =========================================================================
  // Monitored Channels & Chats
  // =========================================================================
  async function loadChannels() {
    try {
      const res = await fetch('/api/admin/channels');
      const data = await res.json();
      if (data.success) {
        channelsList = data.channels || [];
        renderChannels();
      }
    } catch (err) {
      console.error('Could not load channels:', err);
      channelsTableBody.innerHTML = `<tr><td colspan="7" style="color:var(--accent-rose); text-align:center; padding:1.5rem;">Failed to load channels.</td></tr>`;
    }
  }

  function getUsageClass(usage) {
    const u = (usage || '').toUpperCase();
    if (u === 'CLIENT_SUPPORT') return 'usage-client-support';
    if (u === 'PM_APPROVALS') return 'usage-pm-approvals';
    if (u === 'DEV_ALERTS') return 'usage-dev-alerts';
    return 'usage-general';
  }

  function renderChannels() {
    if (!channelsList.length) {
      channelsTableBody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:2rem; color:var(--text-muted);">No monitored channels or chats configured. Click "Add Channel or Chat" above.</td></tr>`;
      return;
    }

    channelsTableBody.innerHTML = channelsList.map(c => {
      const usageClass = getUsageClass(c.usage);
      const isChat = c.type === 'chat';
      const idVal = isChat ? c.chat_id : `${c.team_id || ''} / ${c.channel_id || ''}`;
      const hasWebhook = Boolean(c.webhook_url);

      return `
        <tr data-id="${c.id}">
          <td>
            <div style="display:flex; align-items:center; gap:0.6rem;">
              <span style="font-size:1.15rem;">${isChat ? '💬' : '📢'}</span>
              <div>
                <strong style="color:var(--text-main); font-size:0.95rem;">${escapeHtml(c.name)}</strong>
              </div>
            </div>
          </td>
          <td>
            <span class="badge-mini ${isChat ? 'badge-general' : 'badge-confirmed'}">
              ${isChat ? 'Group Chat' : 'Teams Channel'}
            </span>
          </td>
          <td>
            <select class="role-select channel-usage-dropdown" data-id="${c.id}">
              <option value="CLIENT_SUPPORT" ${c.usage === 'CLIENT_SUPPORT' ? 'selected' : ''}>🎧 Client Support</option>
              <option value="PM_APPROVALS" ${c.usage === 'PM_APPROVALS' ? 'selected' : ''}>👑 PM Approvals</option>
              <option value="DEV_ALERTS" ${c.usage === 'DEV_ALERTS' ? 'selected' : ''}>💻 Dev Escalations</option>
              <option value="GENERAL" ${c.usage === 'GENERAL' ? 'selected' : ''}>📢 General</option>
            </select>
          </td>
          <td>
            <code style="font-size:0.75rem; color:var(--text-muted); background:rgba(255,255,255,0.05); padding:0.2rem 0.4rem; border-radius:4px;">
              ${escapeHtml(idVal.length > 28 ? idVal.substring(0, 25) + '...' : idVal)}
            </code>
          </td>
          <td>
            <span style="font-size:0.8rem; color:${hasWebhook ? 'var(--accent-green)' : 'var(--text-muted)'};">
              ${hasWebhook ? '🟢 Connected' : '⚪ None'}
            </span>
          </td>
          <td>
            <label class="switch-container">
              <input type="checkbox" class="switch-input channel-active-toggle" data-id="${c.id}" ${c.is_active ? 'checked' : ''}>
              <span class="switch-slider"></span>
            </label>
          </td>
          <td style="text-align:right;">
            <div style="display:inline-flex; gap:0.4rem;">
              ${hasWebhook ? `
                <button class="btn btn-icon btn-test-channel-webhook" data-id="${c.id}" title="Test webhook card delivery">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></svg>
                </button>
              ` : ''}
              <button class="btn btn-icon btn-delete-channel" data-id="${c.id}" style="color:var(--accent-rose);" title="Remove channel">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              </button>
            </div>
          </td>
        </tr>
      `;
    }).join('');

    // Inline usage change
    document.querySelectorAll('.channel-usage-dropdown').forEach(sel => {
      sel.addEventListener('change', async (e) => {
        const id = e.target.getAttribute('data-id');
        const newUsage = e.target.value;
        try {
          await fetch(`/api/admin/channels/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ usage: newUsage }),
          });
          showToast(`Channel usage updated to ${newUsage}`);
        } catch (err) {
          showToast(`Error updating usage: ${err}`, 'error');
        }
      });
    });

    // Inline active toggle
    document.querySelectorAll('.channel-active-toggle').forEach(chk => {
      chk.addEventListener('change', async (e) => {
        const id = e.target.getAttribute('data-id');
        const active = e.target.checked;
        try {
          await fetch(`/api/admin/channels/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: active }),
          });
          showToast(`Channel status updated`);
          loadOverview();
        } catch (err) {
          showToast(`Error updating status: ${err}`, 'error');
        }
      });
    });

    // Test webhook button
    document.querySelectorAll('.btn-test-channel-webhook').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.getAttribute('data-id');
        const ch = channelsList.find(c => String(c.id) === String(id));
        if (!ch || !ch.webhook_url) return;
        showToast('Sending test card to channel...');
        try {
          const res = await fetch('/api/admin/test/teams', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ webhook_url: ch.webhook_url }),
          });
          const data = await res.json();
          if (data.success) {
            showToast('Test card delivered to Teams chat!');
          } else {
            showToast(data.error || 'Failed to deliver webhook card', 'error');
          }
        } catch (err) {
          showToast(`Webhook test error: ${err}`, 'error');
        }
      });
    });

    // Delete channel
    document.querySelectorAll('.btn-delete-channel').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.getAttribute('data-id');
        const ch = channelsList.find(c => String(c.id) === String(id));
        const name = ch ? ch.name : 'channel';
        if (!confirm(`Remove monitored channel '${name}'?`)) return;

        try {
          const res = await fetch(`/api/admin/channels/${id}`, { method: 'DELETE' });
          const data = await res.json();
          if (data.success) {
            showToast(`Channel '${name}' removed`);
            loadChannels();
            loadOverview();
          }
        } catch (err) {
          showToast(`Error deleting channel: ${err}`, 'error');
        }
      });
    });
  }

  // Channel Modal logic
  btnOpenAddChannelModal.addEventListener('click', () => {
    editChannelId.value = '';
    modalChannelTitle.textContent = 'Add Monitored Channel or Chat';
    channelName.value = '';
    channelType.value = 'chat';
    channelUsage.value = 'CLIENT_SUPPORT';
    channelChatId.value = '';
    channelTeamId.value = '';
    channelChannelId.value = '';
    channelWebhookUrl.value = '';
    groupChatIdField.style.display = 'flex';
    groupTeamIdField.style.display = 'none';
    modalChannel.classList.add('active');
  });

  channelType.addEventListener('change', () => {
    if (channelType.value === 'chat') {
      groupChatIdField.style.display = 'flex';
      groupTeamIdField.style.display = 'none';
    } else {
      groupChatIdField.style.display = 'none';
      groupTeamIdField.style.display = 'flex';
    }
  });

  btnSaveChannel.addEventListener('click', async () => {
    const name = channelName.value.trim();
    if (!name) {
      showToast('Please enter a friendly channel name', 'error');
      return;
    }

    const payload = {
      name: name,
      type: channelType.value,
      usage: channelUsage.value,
      chat_id: channelChatId.value.trim(),
      team_id: channelTeamId.value.trim(),
      channel_id: channelChannelId.value.trim(),
      webhook_url: channelWebhookUrl.value.trim(),
    };

    try {
      const res = await fetch('/api/admin/channels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.success) {
        showToast('Channel added successfully');
        modalChannel.classList.remove('active');
        loadChannels();
        loadOverview();
      } else {
        showToast(data.detail || 'Could not add channel', 'error');
      }
    } catch (err) {
      showToast(`Error adding channel: ${err}`, 'error');
    }
  });

  // Modal Closes
  document.querySelectorAll('.btn-close-modal').forEach(btn => {
    btn.addEventListener('click', () => {
      modalMember.classList.remove('active');
      modalChannel.classList.remove('active');
      if (modalImportMembers) modalImportMembers.classList.remove('active');
    });
  });

  // =========================================================================
  // System Config (Jira, Gemini, Network)
  // =========================================================================
  async function loadConfig() {
    try {
      const res = await fetch('/api/admin/config');
      const data = await res.json();
      if (data.success) {
        configData = data.config;
        populateConfigFields(configData);
      }
    } catch (err) {
      console.error('Could not load config:', err);
    }
  }

  function populateConfigFields(cfg) {
    if (!cfg) return;

    // Jira
    if (cfg.jira) {
      jiraBaseUrl.value = cfg.jira.baseUrl || '';
      jiraProjectKey.value = cfg.jira.projectKey || '';
      jiraEmail.value = cfg.jira.email || '';
      jiraApiToken.value = cfg.jira.apiTokenMasked || '';
      if (cfg.jira.defaultIssueType) {
        jiraDefaultIssueType.value = cfg.jira.defaultIssueType;
      }
    }

    // Gemini
    if (cfg.gemini) {
      if (cfg.gemini.model) {
        geminiModel.value = cfg.gemini.model;
      }
      const keys = cfg.gemini.keys || [];
      geminiApiKey1.value = keys[0] ? keys[0].masked : '';
      geminiApiKey2.value = keys[1] ? keys[1].masked : '';
      geminiApiKey3.value = keys[2] ? keys[2].masked : '';
    }

    // Network & Teams
    if (cfg.network) {
      webhookPublicUrl.value = cfg.network.webhookPublicUrl || '';
    }
    if (cfg.teams) {
      teamsWebhookUrl.value = cfg.teams.webhookUrl || '';
      allowSelfApproval.checked = Boolean(cfg.teams.allowSelfApproval);
    }
  }

  // =========================================================================
  // Save All Config Changes
  // =========================================================================
  async function saveAllConfig() {
    const payload = {};

    if (jiraBaseUrl.value.trim()) payload.jira_base_url = jiraBaseUrl.value.trim();
    if (jiraProjectKey.value.trim()) payload.jira_project_key = jiraProjectKey.value.trim().toUpperCase();
    if (jiraEmail.value.trim()) payload.jira_email = jiraEmail.value.trim();
    const isMaskedVal = (str) => !str || str.includes('•') || str.includes('…') || str.startsWith('••');

    if (jiraApiToken.value.trim() && !isMaskedVal(jiraApiToken.value.trim())) {
      payload.jira_api_token = jiraApiToken.value.trim();
    }
    if (jiraDefaultIssueType.value) payload.jira_default_issue_type = jiraDefaultIssueType.value;

    if (geminiModel.value) payload.gemini_model = geminiModel.value;
    if (geminiApiKey1.value.trim() && !isMaskedVal(geminiApiKey1.value.trim())) {
      payload.gemini_api_key_1 = geminiApiKey1.value.trim();
    }
    if (geminiApiKey2.value.trim() && !isMaskedVal(geminiApiKey2.value.trim())) {
      payload.gemini_api_key_2 = geminiApiKey2.value.trim();
    }
    if (geminiApiKey3.value.trim() && !isMaskedVal(geminiApiKey3.value.trim())) {
      payload.gemini_api_key_3 = geminiApiKey3.value.trim();
    }

    if (webhookPublicUrl.value.trim()) payload.webhook_public_url = webhookPublicUrl.value.trim();
    if (teamsWebhookUrl.value.trim()) payload.teams_webhook_url = teamsWebhookUrl.value.trim();
    payload.allow_self_approval = allowSelfApproval.checked;

    try {
      btnTopSave.disabled = true;
      btnTopSave.textContent = 'Saving...';
      const res = await fetch('/api/admin/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.success) {
        showToast('Settings saved successfully & live configuration updated!');
        clearUnsaved();
        loadOverview();
        loadConfig();
      } else {
        showToast(data.detail || 'Could not save settings', 'error');
      }
    } catch (err) {
      showToast(`Error saving settings: ${err}`, 'error');
    } finally {
      btnTopSave.disabled = false;
      btnTopSave.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
        <span>Save Changes</span>
      `;
    }
  }

  btnTopSave.addEventListener('click', saveAllConfig);
  btnFloatingSave.addEventListener('click', saveAllConfig);

  // =========================================================================
  // Connection Testers
  // =========================================================================
  btnTestJira.addEventListener('click', async () => {
    const url = jiraBaseUrl.value.trim();
    const email = jiraEmail.value.trim();
    const token = jiraApiToken.value.trim();
    const proj = jiraProjectKey.value.trim();

    jiraTestResult.className = 'test-result-badge active';
    jiraTestResult.style.color = 'var(--text-muted)';
    jiraTestResult.textContent = 'Connecting to Jira Cloud...';

    try {
      const res = await fetch('/api/admin/test/jira', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_url: url,
          email: email,
          api_token: token,
          project_key: proj,
        }),
      });
      const data = await res.json();
      if (data.success) {
        jiraTestResult.className = 'test-result-badge active test-result-success';
        jiraTestResult.textContent = `🟢 ${data.message}`;
      } else {
        jiraTestResult.className = 'test-result-badge active test-result-error';
        jiraTestResult.textContent = `🔴 ${data.error}`;
      }
    } catch (err) {
      jiraTestResult.className = 'test-result-badge active test-result-error';
      jiraTestResult.textContent = `🔴 Connection failed: ${err}`;
    }
  });

  btnTestGemini.addEventListener('click', async () => {
    const key = geminiApiKey1.value.trim();
    const model = geminiModel.value;

    geminiTestResult.className = 'test-result-badge active';
    geminiTestResult.style.color = 'var(--text-muted)';
    geminiTestResult.textContent = 'Pinging Gemini API...';

    try {
      const res = await fetch('/api/admin/test/gemini', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: key, model: model }),
      });
      const data = await res.json();
      if (data.success) {
        geminiTestResult.className = 'test-result-badge active test-result-success';
        geminiTestResult.textContent = `🟢 ${data.message}`;
      } else {
        geminiTestResult.className = 'test-result-badge active test-result-error';
        geminiTestResult.textContent = `🔴 ${data.error}`;
      }
    } catch (err) {
      geminiTestResult.className = 'test-result-badge active test-result-error';
      geminiTestResult.textContent = `🔴 Test failed: ${err}`;
    }
  });

  btnTestTeamsWebhook.addEventListener('click', async () => {
    const url = teamsWebhookUrl.value.trim();

    teamsTestResult.className = 'test-result-badge active';
    teamsTestResult.style.color = 'var(--text-muted)';
    teamsTestResult.textContent = 'Sending card to Teams...';

    try {
      const res = await fetch('/api/admin/test/teams', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ webhook_url: url }),
      });
      const data = await res.json();
      if (data.success) {
        teamsTestResult.className = 'test-result-badge active test-result-success';
        teamsTestResult.textContent = `🟢 ${data.message}`;
      } else {
        teamsTestResult.className = 'test-result-badge active test-result-error';
        teamsTestResult.textContent = `🔴 ${data.error}`;
      }
    } catch (err) {
      teamsTestResult.className = 'test-result-badge active test-result-error';
      teamsTestResult.textContent = `🔴 Webhook call failed: ${err}`;
    }
  });

  // =========================================================================
  // Audit Log
  // =========================================================================
  async function loadAuditLog() {
    try {
      auditTableBody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:1.5rem; color:var(--text-muted);">Loading audit history...</td></tr>`;
      const res = await fetch('/api/admin/audit-log');
      const data = await res.json();
      if (data.success) {
        const logs = data.logs || [];
        if (!logs.length) {
          auditTableBody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:2rem; color:var(--text-muted);">No recorded changes yet.</td></tr>`;
          return;
        }

        auditTableBody.innerHTML = logs.map(l => {
          let catBadgeClass = 'badge-general';
          if (l.category === 'MEMBERS') catBadgeClass = 'badge-confirmed';
          if (l.category === 'CHANNELS') catBadgeClass = 'badge-possible';
          if (l.category === 'CONFIG') catBadgeClass = 'badge-auto-renew';

          return `
            <tr>
              <td style="white-space:nowrap; font-size:0.8rem; color:var(--text-muted);">
                ${escapeHtml(l.created_at || 'Just now')}
              </td>
              <td>
                <span class="badge-mini ${catBadgeClass}">${escapeHtml(l.category)}</span>
              </td>
              <td>
                <strong style="color:var(--text-main); font-size:0.85rem;">${escapeHtml(l.action)}</strong>
              </td>
              <td style="font-size:0.85rem; color:var(--text-muted);">
                ${escapeHtml(l.details)}
              </td>
              <td style="font-size:0.8rem; color:var(--text-faint);">
                ${escapeHtml(l.performed_by || 'Admin')}
              </td>
            </tr>
          `;
        }).join('');
      }
    } catch (err) {
      console.error('Could not load audit log:', err);
      auditTableBody.innerHTML = `<tr><td colspan="5" style="color:var(--accent-rose); text-align:center; padding:1.5rem;">Failed to load audit history.</td></tr>`;
    }
  }

  if (btnRefreshAudit) {
    btnRefreshAudit.addEventListener('click', loadAuditLog);
  }

  // =========================================================================
  // Escape HTML Utility
  // =========================================================================
  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // Run on start
  loadAllData();
});

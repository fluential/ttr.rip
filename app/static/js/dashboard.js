let authKey = window.AUTH_KEY;
let csrfToken = window.CSRF_TOKEN;
let currentSortBy = 'id';
let currentSortDir = 'desc';
let pageSize = 25;
let nextCursor = null;
let prevCursor = null;
let autoRefreshEnabled = true;
let autoRefreshInterval = 5; // seconds
let autoRefreshCountdown = autoRefreshInterval;
let autoRefreshTimer = null;

function getCsrfToken() {
    const cookies = document.cookie.split(';').map(c => c.trim());
    const csrfCookie = cookies.find(c => c.startsWith('csrf_token='));
    return csrfCookie ? csrfCookie.split('=')[1] : null;
}

function getMaskedAuthKey(key) {
    if (key.length <= 4) {
        return '****';
    }
    return '****************************' + key.slice(-4);
}

function copyAuthKey(element, fullKey) {
    if (element.dataset.copying) {
        return;
    }
    element.dataset.copying = 'true';

    navigator.clipboard.writeText(fullKey).then(() => {
        element.value = 'Copied!';
        setTimeout(() => {
            element.value = getMaskedAuthKey(fullKey);
            delete element.dataset.copying;
        }, 1200);
    }).catch(err => {
        console.error('Failed to copy auth key: ', err);
        element.value = getMaskedAuthKey(fullKey); // Restore on error
        delete element.dataset.copying;
    });
}

function confirmRotateKey() {
    if (confirm('Are you sure you want to rotate your access key? You will need to update any scripts or bookmarks using the current key.')) {
        rotateApiKey();
    }
}

async function rotateApiKey() {
    const rotateBtn = document.getElementById('rotate-key-btn');
    const messageDiv = document.getElementById('key-rotation-message');
    
    rotateBtn.disabled = true;
    rotateBtn.setAttribute('aria-busy', 'true');
    messageDiv.textContent = 'Rotating your access key...';
    messageDiv.style.display = 'block';
    messageDiv.style.color = 'inherit';
    
    try {
        const response = await fetch('/api/v1/user/rotate-key', {
            method: 'POST',
            headers: {
                'X-Auth-Key': authKey,
                'X-CSRF-Token': getCsrfToken(),
                'Content-Type': 'application/json'
            }
        });
        
        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to rotate key');
        }
        
        const data = await response.json();
        const newKey = data.auth_key;
        
        // Immediately update the in-memory authKey for subsequent requests
        authKey = newKey;
        window.AUTH_KEY = newKey; // Also update the global reference if needed elsewhere
        
        // Update cookie
        document.cookie = `auth_key=${newKey}; path=/; max-age=${365*24*60*60}; samesite=Lax`;
        
        messageDiv.textContent = 'Your access key has been rotated successfully. Your dashboard has been updated.';
        messageDiv.style.color = 'var(--pico-color-green-500)';
        
        // Update display and copy functionality
        const authKeyDisplay = document.getElementById('auth-key-display');
        authKeyDisplay.value = getMaskedAuthKey(newKey);
        authKeyDisplay.setAttribute('onclick', `copyAuthKey(this, '${newKey}')`);
        
        // Refresh the dashboard data with the new key
        await fetchChecks();
        await fetchUserStats();

        // Hide the message after a few seconds
        setTimeout(() => {
            messageDiv.style.display = 'none';
        }, 5000);

    } catch (error) {
        console.error('Error rotating key:', error);
        messageDiv.textContent = `Failed to rotate access key: ${error.message}. Please try again.`;
        messageDiv.style.color = 'var(--pico-color-red-500)';
    } finally {
        rotateBtn.disabled = false;
        rotateBtn.removeAttribute('aria-busy');
    }
}

// --- Status Page Functions ---

let allChecksForStatusPage = [];

async function fetchStatusPages() {
    try {
        const response = await fetch('/api/v1/status-pages', {
            headers: { 'X-Auth-Key': authKey }
        });
        if (!response.ok) throw new Error('Failed to fetch status pages');
        const statusPages = await response.json();
        renderStatusPages(statusPages);
    } catch (error) {
        console.error('Error fetching status pages:', error);
        document.getElementById('status-pages-list').innerHTML = `<p style="color: var(--pico-color-red-500);">Could not load status pages.</p>`;
    }
}

function renderStatusPages(statusPages) {
    const listDiv = document.getElementById('status-pages-list');
    if (statusPages.length === 0) {
        listDiv.innerHTML = '<p>No status pages found. Create one below!</p>';
        return;
    }

    listDiv.innerHTML = statusPages.map(page => `
        <div class="grid" style="align-items: center;">
            <div>
                <strong>${page.name}</strong><br>
                <small><a href="/s/${page.slug}" target="_blank">/s/${page.slug}</a></small>
            </div>
            <div style="text-align: right;">
                <button class="outline action-button" title="Edit" onclick="editStatusPage(event, ${page.id}, '${page.name.replace(/'/g, "\\'")}', '${page.slug.replace(/'/g, "\\'")}', [${page.checks.map(c => c.id)}])">✏️</button>
                <button class="secondary outline action-button" title="Delete" onclick="deleteStatusPage(${page.id})">🗑️</button>
            </div>
        </div>
    `).join('<hr>');
}

function populateCheckCheckboxes(selectedCheckIds = []) {
    const container = document.getElementById('status-page-checks-list');
    if (allChecksForStatusPage.length === 0) {
        container.innerHTML = '<p>No checks available to add to a status page.</p>';
        return;
    }
    container.innerHTML = allChecksForStatusPage.map(check => `
        <label for="sp-check-${check.id}">
            <input type="checkbox" id="sp-check-${check.id}" name="check_ids" value="${check.id}" ${selectedCheckIds.includes(check.id) ? 'checked' : ''}>
            ${check.name}
        </label>
    `).join('');
}

function editStatusPage(event, id, name, slug, checkIds) {
    event.stopPropagation();
    const details = document.getElementById('new-status-page-section');
    details.open = true;
    
    const form = document.getElementById('new-status-page-form');
    form.querySelector('#status-page-name').value = name;
    form.querySelector('#status-page-slug').value = slug;
    populateCheckCheckboxes(checkIds);
    
    form.dataset.editingId = id;
    details.querySelector('summary').textContent = 'Edit Status Page';
    form.querySelector('button[type="submit"]').textContent = 'Update Status Page';
    
    let cancelButton = form.querySelector('.cancel-edit');
    if (!cancelButton) {
        cancelButton = document.createElement('button');
        cancelButton.type = 'button';
        cancelButton.className = 'secondary outline cancel-edit';
        cancelButton.textContent = 'Cancel';
        cancelButton.style.marginLeft = '1rem';
        cancelButton.onclick = cancelStatusPageEdit;
        form.querySelector('button[type="submit"]').insertAdjacentElement('afterend', cancelButton);
    }
    form.scrollIntoView({ behavior: 'smooth' });
}

function cancelStatusPageEdit() {
    const form = document.getElementById('new-status-page-form');
    form.reset();
    delete form.dataset.editingId;
    
    const details = document.getElementById('new-status-page-section');
    details.querySelector('summary').textContent = 'New Status Page';
    details.open = false;
    
    form.querySelector('button[type="submit"]').textContent = 'Create Status Page';
    const cancelButton = form.querySelector('.cancel-edit');
    if (cancelButton) cancelButton.remove();
    populateCheckCheckboxes(); // Reset checkboxes
}

async function handleStatusPageFormSubmit(event) {
    event.preventDefault();
    const form = event.target;
    const editingId = form.dataset.editingId;

    const selectedChecks = Array.from(form.querySelectorAll('input[name="check_ids"]:checked')).map(cb => parseInt(cb.value));

    const data = {
        name: form.querySelector('#status-page-name').value,
        slug: form.querySelector('#status-page-slug').value,
        check_ids: selectedChecks
    };

    const headers = {
        'X-Auth-Key': authKey,
        'X-CSRF-Token': getCsrfToken(),
        'Content-Type': 'application/json'
    };

    let response;
    let url = '/api/v1/status-pages';
    let method = 'POST';

    if (editingId) {
        url += `/${editingId}`;
        method = 'PUT';
    }

    response = await fetch(url, { method, headers, body: JSON.stringify(data) });

    if (response.ok) {
        cancelStatusPageEdit();
        fetchStatusPages();
    } else {
        const error = await response.json();
        alert(`Failed to ${editingId ? 'update' : 'create'} status page: ${error.detail}`);
    }
}

async function deleteStatusPage(pageId) {
    if (!confirm('Are you sure you want to delete this status page?')) return;

    const response = await fetch(`/api/v1/status-pages/${pageId}`, {
        method: 'DELETE',
        headers: { 
            'X-Auth-Key': authKey,
            'X-CSRF-Token': getCsrfToken()
        }
    });

    if (response.ok) {
        fetchStatusPages();
    } else {
        alert('Failed to delete status page.');
    }
}

async function fetchOperationalMetrics() {
    try {
        const response = await fetch('/api/v1/metrics/summary');
        if (!response.ok) {
            throw new Error('Failed to fetch operational metrics');
        }
        const metrics = await response.json();
        const summaryDiv = document.getElementById('operational-metrics-summary');

        const avgLatency = metrics.average_api_latency_seconds ? (metrics.average_api_latency_seconds * 1000).toFixed(2) : 'N/A';

        summaryDiv.innerHTML = `
            <div class="grid">
                <div><strong>Total Checks:</strong> ${metrics.total_checks || 0}</div>
                <div><strong>API Requests:</strong> ${metrics.total_api_requests || 0}</div>
                <div><strong>Avg. API Latency:</strong> ${avgLatency} ms</div>
                <div><strong>Notifications Sent:</strong> ${metrics.total_notifications_sent || 0}</div>
            </div>
        `;
    } catch (error) {
        console.error("Error fetching operational metrics:", error);
        const summaryDiv = document.getElementById('operational-metrics-summary');
        summaryDiv.innerHTML = `<p style="color: var(--pico-color-red-500);">Could not load operational metrics.</p>`;
    }
}

function confirmDeleteAccount() {
    const confirmation = prompt('This action is irreversible. You will lose all your checks and your access key will be blacklisted for one hour. To confirm, type "DELETE" in the box below.');
    if (confirmation === 'DELETE') {
        deleteAccount();
    }
}

async function deleteAccount() {
    console.log("deleteAccount function called.");
    const deleteBtn = document.getElementById('delete-account-btn');
    const messageDiv = document.getElementById('import-message'); // Reuse this message div

    deleteBtn.disabled = true;
    deleteBtn.setAttribute('aria-busy', 'true');
    messageDiv.textContent = 'Deleting your account...';
    messageDiv.style.display = 'block';
    messageDiv.style.color = 'inherit';

    try {
        console.log("Sending delete request with authKey:", authKey ? `...${authKey.slice(-4)}` : 'null');
        const response = await fetch('/api/v1/user/delete', {
            method: 'POST',
            headers: {
                'X-Auth-Key': authKey,
                'X-CSRF-Token': getCsrfToken(),
            }
        });

        console.log(`Received response from /api/v1/user/delete: status=${response.status}`);
        console.log("Response headers:", Object.fromEntries(response.headers.entries()));

        if (!response.ok) {
            console.error("Response was not OK.");
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to delete account');
        }

        console.log("Account deletion successful on server. Clearing client-side auth.");
        // On success, redirect to homepage
        messageDiv.textContent = 'Account deleted successfully. Redirecting...';
        messageDiv.style.color = 'var(--pico-color-green-500)';
        
        // Invalidate the key in memory to prevent reuse
        authKey = null;
        window.AUTH_KEY = null;

        // Use a short delay to allow user to read the message
        setTimeout(() => {
            window.location.href = '/';
        }, 1500);

    } catch (error) {
        console.error('Error deleting account:', error);
        messageDiv.textContent = `Failed to delete account: ${error.message}. Please try again.`;
        messageDiv.style.color = 'var(--pico-color-red-500)';
        deleteBtn.disabled = false;
        deleteBtn.removeAttribute('aria-busy');
    }
}

async function handleExport() {
    const exportBtn = document.getElementById('export-btn');
    exportBtn.disabled = true;
    exportBtn.setAttribute('aria-busy', 'true');

    try {
        const response = await fetch('/api/v1/checks/export', {
            headers: { 'X-Auth-Key': authKey }
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to export checks');
        }

        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = 'ttr_rip_checks_export.json';
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        a.remove();

    } catch (error) {
        console.error('Error exporting checks:', error);
        alert(`Failed to export checks: ${error.message}`);
    } finally {
        exportBtn.disabled = false;
        exportBtn.removeAttribute('aria-busy');
    }
}

async function handleImport(file) {
    const importBtn = document.getElementById('import-btn');
    const messageDiv = document.getElementById('import-message');

    if (!file) {
        return;
    }

    importBtn.disabled = true;
    importBtn.setAttribute('aria-busy', 'true');
    messageDiv.textContent = 'Importing checks...';
    messageDiv.style.display = 'block';
    messageDiv.style.color = 'inherit';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch('/api/v1/checks/import', {
            method: 'POST',
            headers: {
                'X-Auth-Key': authKey,
                'X-CSRF-Token': getCsrfToken(),
            },
            body: formData
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.detail || 'Import failed');
        }

        let message = `Import complete. ${result.imported_count} checks imported successfully.`;
        if (result.failed_count > 0) {
            message += ` ${result.failed_count} checks failed.`;
            console.error('Import errors:', result.errors);
        }
        
        messageDiv.textContent = message;
        messageDiv.style.color = result.failed_count > 0 ? 'var(--pico-color-orange-500)' : 'var(--pico-color-green-500)';

        // Refresh the dashboard to show new checks
        await fetchChecks();
        await fetchUserStats();

    } catch (error) {
        console.error('Error importing checks:', error);
        messageDiv.textContent = `Failed to import checks: ${error.message}. Please check the file and try again.`;
        messageDiv.style.color = 'var(--pico-color-red-500)';
    } finally {
        importBtn.disabled = false;
        importBtn.removeAttribute('aria-busy');
        // Clear the file input value so the user can select the same file again
        document.getElementById('import-file-input').value = '';
    }
}

function copyUrl(element) {
    if (element.dataset.copying) {
        return;
    }
    element.dataset.copying = 'true';
    const originalValue = element.value;

    navigator.clipboard.writeText(originalValue).then(() => {
        element.value = 'Copied!';
        setTimeout(() => {
            element.value = originalValue;
            delete element.dataset.copying;
        }, 1200);
    }).catch(err => {
        console.error('Failed to copy text: ', err);
        element.value = originalValue; // Restore on error
        delete element.dataset.copying;
    });
}

function parseUTCDate(dateString) {
    if (!dateString) return null;
    // If timezone info (Z or +/-hh:mm) is missing, treat the string as UTC.
    // This corrects for browsers interpreting naive ISO-like strings as local time.
    if (!/Z|[+-]\d{2}:\d{2}$/.test(dateString)) {
        return new Date(dateString.replace(' ', 'T') + 'Z');
    }
    return new Date(dateString);
}

function formatTimeDifference(seconds) {
    if (isNaN(seconds)) return 'N/A';

    const isPast = seconds < 0;
    seconds = Math.abs(seconds);

    if (seconds < 1) {
        return isPast ? '0 seconds ago' : 'in 0 seconds';
    }

    const days = Math.floor(seconds / 86400);
    seconds %= 86400;
    const hours = Math.floor(seconds / 3600);
    seconds %= 3600;
    const minutes = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);

    const parts = [];
    if (days > 0) parts.push(`${days} day${days > 1 ? 's' : ''}`);
    if (hours > 0) parts.push(`${hours} hour${hours > 1 ? 's' : ''}`);
    if (minutes > 0) parts.push(`${minutes} minute${minutes > 1 ? 's' : ''}`);
    if (secs > 0 || parts.length === 0) parts.push(`${secs} second${secs !== 1 ? 's' : ''}`);

    // Show only the two most significant parts for brevity
    const result = parts.slice(0, 2).join(' ');

    return isPast ? `${result} ago` : `in ${result}`;
}

function formatDuration(seconds) {
    if (seconds === null || seconds === undefined || isNaN(seconds)) return 'N/A';
    if (seconds < 0) return 'N/A';

    if (seconds < 1) {
        return "less than a second";
    }
    if (seconds < 60) {
        const s = Math.round(seconds);
        return `${s} second${s !== 1 ? 's' : ''}`;
    }

    const minutes = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);

    const parts = [];
    if (minutes > 0) {
        parts.push(`${minutes} minute${minutes > 1 ? 's' : ''}`);
    }
    if (secs > 0) {
        parts.push(`${secs} second${secs !== 1 ? 's' : ''}`);
    }
    
    return parts.join(' ');
}

function startAutoRefreshTimer() {
    if (autoRefreshTimer) {
        clearInterval(autoRefreshTimer);
    }

    const countdownEl = document.getElementById('refresh-countdown');
    autoRefreshCountdown = autoRefreshInterval;
    countdownEl.textContent = autoRefreshCountdown;

    autoRefreshTimer = setInterval(() => {
        autoRefreshCountdown--;
        countdownEl.textContent = autoRefreshCountdown;
        
        if (autoRefreshCountdown <= 0) {
            autoRefreshCountdown = autoRefreshInterval;
            countdownEl.textContent = autoRefreshInterval;
            fetchChecks();
            fetchUserStats();
            fetchOperationalMetrics();
        }
    }, 1000);
}

function stopAutoRefreshTimer() {
    if (autoRefreshTimer) {
        clearInterval(autoRefreshTimer);
        autoRefreshTimer = null;
    }
}

function toggleAutoRefresh() {
    const button = document.getElementById('refresh-toggle-btn');
    const statusEl = document.getElementById('refresh-status');
    
    autoRefreshEnabled = !autoRefreshEnabled;
    
    if (autoRefreshEnabled) {
        button.textContent = '⏸️';
        button.title = 'Pause automatic refresh';
        statusEl.innerHTML = `Auto-refresh: <span id="refresh-countdown">${autoRefreshInterval}</span>s`;
        startAutoRefreshTimer();
    } else {
        button.textContent = '▶️';
        button.title = 'Resume automatic refresh';
        statusEl.textContent = 'Auto-refresh: paused';
        stopAutoRefreshTimer();
    }
}

async function fetchUserStats() {
    try {
        const response = await fetch('/api/v1/checks/stats', {
            headers: { 'X-Auth-Key': authKey }
        });
        if (!response.ok) {
            throw new Error('Failed to fetch user stats');
        }
        const stats = await response.json();
        const summaryDiv = document.getElementById('user-stats-summary');

        const avgInterval = stats.avg_interval_seconds ? formatTimeDifference(stats.avg_interval_seconds).replace('in ', '') : 'N/A';
        const avgDuration = stats.avg_duration_seconds ? formatDuration(stats.avg_duration_seconds) : 'N/A';

        summaryDiv.innerHTML = `
            <div class="grid">
                <div><strong>Total Checks:</strong> ${stats.total_checks}</div>
                <div><strong>Avg. Interval:</strong> ${avgInterval}</div>
                <div><strong>Avg. Duration:</strong> ${avgDuration}</div>
                <div><strong>Notifications Queued:</strong> ${stats.user_queued_notifications}</div>
            </div>
        `;
    } catch (error) {
        console.error("Error fetching user stats:", error);
        const summaryDiv = document.getElementById('user-stats-summary');
        summaryDiv.innerHTML = `<p style="color: var(--pico-color-red-500);">Could not load user stats.</p>`;
    }
}

async function fetchChecks(cursor = null, direction = currentSortDir) {
    let url = `/api/v1/checks?size=${pageSize}&sort_by=${currentSortBy}&sort_direction=${direction}`;
    if (cursor) {
        url += `&cursor=${cursor}`;
    }
    const response = await fetch(url, {
        headers: { 'X-Auth-Key': authKey }
    });

    if (response.status === 401) {
        const tableBody = document.querySelector('#checks-table tbody');
        tableBody.innerHTML = '<tr><td colspan="7" style="color: var(--pico-color-red-500);">Authentication failed. Your key may be invalid or expired. Please log in again.</td></tr>';
        stopAutoRefreshTimer();
        return;
    }

    const data = await response.json();
    const checks = data.items;
    allChecksForStatusPage = checks; // Cache for status page form
    populateCheckCheckboxes(); // Populate form now that we have checks
    const tableBody = document.querySelector('#checks-table tbody');
    const statusSummary = document.getElementById('status-summary');
    
    tableBody.innerHTML = '';
    const statusCounts = { up: 0, down: 0, new: 0 };

    if (checks.length === 0) {
        tableBody.innerHTML = '<tr><td colspan="8">No checks found. Create one above!</td></tr>';
        if (statusSummary) {
            statusSummary.querySelector('.status-up').innerHTML = `<span>🟢</span> Up: 0`;
            statusSummary.querySelector('.status-down').innerHTML = `<span>🔴</span> Down: 0`;
            statusSummary.querySelector('.status-new').innerHTML = `<span>🟡</span> New: 0`;
        }
        updatePagination(null, null);
        return;
    }

    checks.forEach(check => {
        const row = document.createElement('tr');
        const lastPing = check.last_ping ? parseUTCDate(check.last_ping).toLocaleString() : 'Never';
        const pingUrl = `${window.location.origin}/ping/${check.uuid}`;

        const referenceTime = parseUTCDate(check.last_ping) || parseUTCDate(check.created_at);
        const deadline = new Date(referenceTime.getTime() + (check.interval_seconds + check.grace_seconds) * 1000);
        const now = new Date();
        const diffSeconds = (deadline - now) / 1000;
        const expiresIn = formatTimeDifference(diffSeconds);
        const lastDuration = formatDuration(check.last_duration_seconds);

        let currentStatus = check.status;
        // If the check is not already marked as down, and the deadline has passed,
        // update the status on the frontend for immediate feedback.
        if (currentStatus !== 'down' && diffSeconds < 0) {
            currentStatus = 'down';
        }

        if (currentStatus === 'up') statusCounts.up++;
        else if (currentStatus === 'down') statusCounts.down++;
        else if (currentStatus === 'new') statusCounts.new++;

        const statusIcon = {
            'up': '🟢',
            'down': '🔴',
            'new': '🟡',
            'paused': '⏸️'
        };
        
        const displayStatus = check.paused ? 'paused' : currentStatus;
        const statusText = displayStatus.toUpperCase();
        const badgeUrl = `${window.location.origin}/ping/${check.uuid}/badge.svg`;

        row.innerHTML = `
            <td><span class="status-${displayStatus}" title="${statusText}">${statusIcon[displayStatus] || '⚪️'} ${statusText}</span></td>
            <td>${check.name}</td>
            <td><input type="text" class="ping-url" value="${pingUrl}" readonly onclick="copyUrl(this)"></td>
            <td>${lastPing}</td>
            <td>${lastDuration}</td>
            <td>${expiresIn}</td>
            <td><img src="${badgeUrl}" alt="Status Badge" style="cursor: pointer;" title="Click to copy Markdown" onclick="copyUrl(this, '[![Status](${badgeUrl})](${pingUrl})')"></td>
            <td>
                <div class="grid" style="margin-bottom: 0; grid-template-columns: repeat(4, 1fr); gap: 0.5rem;">
                    <button class="outline action-button" title="Edit" onclick="editCheck(event, ${check.id}, '${check.name.replace(/'/g, "\\'")}', ${check.interval_seconds}, ${check.grace_seconds}, ${check.max_runtime_seconds})">✏️</button>
                    <button class="outline action-button" title="${check.paused ? 'Resume' : 'Pause'}" onclick="togglePause(${check.id})">${check.paused ? '▶️' : '⏸️'}</button>
                    <button class="outline action-button" title="Integrations" onclick="window.location.href='/check/${check.id}/integrations'">⚙️</button>
                    <button class="secondary outline action-button" title="Delete" onclick="deleteCheck(${check.id})">🗑️</button>
                </div>
            </td>
        `;
        tableBody.appendChild(row);
    });

    if (statusSummary) {
        statusSummary.querySelector('.status-up').innerHTML = `<span>🟢</span> Up: ${statusCounts.up}`;
        statusSummary.querySelector('.status-down').innerHTML = `<span>🔴</span> Down: ${statusCounts.down}`;
        statusSummary.querySelector('.status-new').innerHTML = `<span>🟡</span> New: ${statusCounts.new}`;
    }
    updatePagination(data.next_cursor, data.prev_cursor);
    updateSortIndicators();
}

function updatePagination(newNextCursor, newPrevCursor) {
    const prevButton = document.getElementById('prev-page');
    const nextButton = document.getElementById('next-page');
    
    nextCursor = newNextCursor;
    prevCursor = newPrevCursor;

    prevButton.disabled = !prevCursor;
    nextButton.disabled = !nextCursor;
}

function updateSortIndicators() {
    document.querySelectorAll('#checks-table th[data-sort]').forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc');
        if (th.dataset.sort === currentSortBy) {
            th.classList.add(currentSortDir === 'asc' ? 'sort-asc' : 'sort-desc');
        }
    });
}

function editCheck(event, id, name, interval, grace, maxRuntime) {
    event.stopPropagation();
    const details = document.getElementById('new-check-section');
    if (details) {
        details.open = true;
    }
    const form = document.getElementById('new-check-form');
    form.querySelector('#name').value = name;
    form.querySelector('#interval_seconds').value = interval;
    form.querySelector('#grace_seconds').value = grace;
    form.querySelector('#max_runtime_seconds').value = maxRuntime || '';
    
    form.dataset.editingId = id;

    if (details) {
        details.querySelector('summary').textContent = 'Edit Check';
    }
    form.querySelector('button[type="submit"]').textContent = 'Update Check';
    
    let cancelButton = form.querySelector('.cancel-edit');
    if (!cancelButton) {
        cancelButton = document.createElement('button');
        cancelButton.type = 'button';
        cancelButton.className = 'secondary outline cancel-edit';
        cancelButton.textContent = 'Cancel';
        cancelButton.style.marginLeft = '1rem';
        cancelButton.onclick = cancelEdit;
        form.querySelector('button[type="submit"]').insertAdjacentElement('afterend', cancelButton);
    }
    form.scrollIntoView({ behavior: 'smooth' });
}

function cancelEdit() {
    const form = document.getElementById('new-check-form');
    form.reset();
    delete form.dataset.editingId;

    const details = document.getElementById('new-check-section');
    if (details) {
        details.querySelector('summary').textContent = 'New Check';
        details.open = false;
    }
    form.querySelector('button[type="submit"]').textContent = 'Create Check';
    
    const cancelButton = form.querySelector('.cancel-edit');
    if (cancelButton) {
        cancelButton.remove();
    }
}

async function handleFormSubmit(event) {
    event.preventDefault();
    const form = event.target;
    const editingId = form.dataset.editingId;

    const maxRuntimeInput = form.querySelector('#max_runtime_seconds');
    const maxRuntimeValue = maxRuntimeInput.value ? parseInt(maxRuntimeInput.value) : null;

    const data = {
        name: form.querySelector('#name').value,
        interval_seconds: parseInt(form.querySelector('#interval_seconds').value),
        grace_seconds: parseInt(form.querySelector('#grace_seconds').value),
        max_runtime_seconds: maxRuntimeValue
    };

    const headers = {
        'X-Auth-Key': authKey,
        'X-CSRF-Token': getCsrfToken(),
        'Content-Type': 'application/json'
    };

    let response;
    if (editingId) {
        response = await fetch(`/api/v1/checks/${editingId}`, {
            method: 'PUT',
            headers: headers,
            body: JSON.stringify(data)
        });
    } else {
        response = await fetch('/api/v1/checks', {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(data)
        });
    }

    if (response.ok) {
        cancelEdit();
        fetchChecks();
    } else {
        alert(`Failed to ${editingId ? 'update' : 'create'} check.`);
    }
}

async function togglePause(checkId) {
    const response = await fetch(`/api/v1/checks/${checkId}/toggle-pause`, {
        method: 'POST',
        headers: { 
            'X-Auth-Key': authKey,
            'X-CSRF-Token': getCsrfToken()
        }
    });

    if (response.ok) {
        fetchChecks(); // Refresh the list to show the new state
    } else {
        alert('Failed to toggle pause status.');
    }
}

async function deleteCheck(checkId) {
    if (!confirm('Are you sure you want to delete this check?')) return;

    const response = await fetch(`/api/v1/checks/${checkId}`, {
        method: 'DELETE',
        headers: { 
            'X-Auth-Key': authKey,
            'X-CSRF-Token': getCsrfToken()
        }
    });

    if (response.ok) {
        fetchChecks();
    } else {
        alert('Failed to delete check.');
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    // Set the initial masked value for the auth key
    const authKeyDisplay = document.getElementById('auth-key-display');
    if (authKeyDisplay) {
        authKeyDisplay.value = getMaskedAuthKey(authKey);
    }

    // Set up page size selector
    const pageSizeSelector = document.getElementById('page-size');
    if (pageSizeSelector) {
        // Set initial value from localStorage or default
        const savedPageSize = localStorage.getItem('pageSizePreference');
        if (savedPageSize) {
            pageSize = parseInt(savedPageSize);
            pageSizeSelector.value = pageSize;
        }

        pageSizeSelector.addEventListener('change', (e) => {
            pageSize = parseInt(e.target.value);
            localStorage.setItem('pageSizePreference', pageSize.toString());
            fetchChecks(); // Refresh with new page size
        });
    }

    // Set up refresh controls
    const refreshToggleBtn = document.getElementById('refresh-toggle-btn');
    if (refreshToggleBtn) {
        refreshToggleBtn.addEventListener('click', toggleAutoRefresh);
    }

    const manualRefreshBtn = document.getElementById('manual-refresh-btn');
    if (manualRefreshBtn) {
        manualRefreshBtn.addEventListener('click', () => {
            fetchChecks();
            fetchUserStats();
            fetchOperationalMetrics();
            
            // Reset the countdown if auto-refresh is enabled
            if (autoRefreshEnabled) {
                autoRefreshCountdown = autoRefreshInterval;
                const countdownEl = document.getElementById('refresh-countdown');
                if (countdownEl) {
                    countdownEl.textContent = autoRefreshInterval;
                }
            }
        });
    }

    await Promise.all([fetchChecks(), fetchUserStats(), fetchOperationalMetrics(), fetchStatusPages()]);
    
    const loadTime = performance.now() - window.pageLoadStartTime;
    const clientTimeElem = document.getElementById('client-load-time');
    const clientTimeContainer = document.getElementById('client-load-time-container');
    if (clientTimeElem && clientTimeContainer) {
        clientTimeElem.textContent = (loadTime / 1000).toFixed(3);
        clientTimeContainer.style.display = 'inline';
    }

    // Start auto-refresh timer
    if (autoRefreshEnabled) {
        startAutoRefreshTimer();
    }

    document.getElementById('new-check-form').addEventListener('submit', handleFormSubmit);
    document.getElementById('new-status-page-form').addEventListener('submit', handleStatusPageFormSubmit);

    // Account Management listeners
    const importBtn = document.getElementById('import-btn');
    const importFileInput = document.getElementById('import-file-input');
    const exportBtn = document.getElementById('export-btn');
    const deleteBtn = document.getElementById('delete-account-btn');

    if (importBtn && importFileInput) {
        importBtn.addEventListener('click', () => importFileInput.click());
        importFileInput.addEventListener('change', (event) => handleImport(event.target.files[0]));
    }
    if (exportBtn) {
        exportBtn.addEventListener('click', handleExport);
    }
    if (deleteBtn) {
        deleteBtn.addEventListener('click', confirmDeleteAccount);
    }

    // Pagination listeners
    document.getElementById('prev-page')?.addEventListener('click', () => {
        if (prevCursor) {
            fetchChecks(prevCursor, `${currentSortDir}_prev`);
        }
    });
    document.getElementById('next-page')?.addEventListener('click', () => {
        if (nextCursor) {
            fetchChecks(nextCursor, currentSortDir);
        }
    });

    // Sorting listeners
    document.querySelectorAll('#checks-table th[data-sort]').forEach(th => {
        th.addEventListener('click', () => {
            const sortBy = th.dataset.sort;
            if (currentSortBy === sortBy) {
                currentSortDir = currentSortDir === 'asc' ? 'desc' : 'asc';
            } else {
                currentSortBy = sortBy;
                currentSortDir = 'desc'; // Default to desc for new column
            }
            fetchChecks(); // Reset to first page on new sort
        });
    });
});

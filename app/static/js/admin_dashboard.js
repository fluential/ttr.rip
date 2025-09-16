const apiToken = window.API_TOKEN;
const csrfToken = window.CSRF_TOKEN;
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

function copyUrl(element) {
    element.select();
    navigator.clipboard.writeText(element.value).then(() => {
        const originalValue = element.value;
        element.value = 'Copied!';
        setTimeout(() => {
            element.value = originalValue;
        }, 1200);
    }).catch(err => {
        console.error('Failed to copy text: ', err);
        // Fallback for older browsers
        try {
            document.execCommand('copy');
            const originalValue = element.value;
            element.value = 'Copied!';
            setTimeout(() => {
                element.value = originalValue;
            }, 1200);
        } catch (e) {
            console.error('Fallback copy failed', e);
        }
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
        return isPast ? '0s ago' : 'in 0s';
    }

    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);

    const parts = [];
    if (hours > 0) {
        parts.push(`${hours}h`);
    }
    if (minutes > 0) {
        parts.push(`${minutes}m`);
    }
    if (secs > 0 || parts.length === 0) {
        parts.push(`${secs}s`);
    }

    const result = parts.join(' ');

    return isPast ? `${result} ago` : `in ${result}`;
}

function formatDuration(seconds) {
    if (seconds === null || seconds === undefined || isNaN(seconds)) return 'N/A';
    if (seconds < 0) return 'N/A';

    if (seconds < 60) {
        return `${seconds}s`;
    }

    const minutes = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);

    return `${minutes}m ${secs}s`;
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
            fetchSystemStats();
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

async function fetchSystemStats() {
    try {
        const response = await fetch('/api/v1/admin/stats', {
            headers: { 'Authorization': `Bearer ${apiToken}` }
        });
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to fetch stats');
        }
        const stats = await response.json();
        const summaryDiv = document.getElementById('system-status-summary');

        summaryDiv.innerHTML = `
            <div class="grid">
                <div><strong>Broker:</strong> ${stats.broker_status}</div>
                <div><strong>Workers:</strong> ${stats.workers_online}</div>
                <div><strong>Queued:</strong> ${stats.total_queued}</div>
                <div><strong>Active:</strong> ${stats.total_active}</div>
                <div><strong>Reserved:</strong> ${stats.total_reserved}</div>
            </div>
        `;
    } catch (error) {
        console.error("Error fetching system stats:", error);
        const summaryDiv = document.getElementById('system-status-summary');
        summaryDiv.innerHTML = `<p style="color: var(--pico-color-red-500);">Could not load system status.</p>`;
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

async function fetchChecks(cursor = null, direction = currentSortDir) {
    let url = `/api/v1/checks?size=${pageSize}&sort_by=${currentSortBy}&sort_direction=${direction}`;
    if (cursor) {
        url += `&cursor=${cursor}`;
    }
    const response = await fetch(url, {
        headers: { 'Authorization': `Bearer ${apiToken}` }
    });
    const data = await response.json();
    const checks = data.items;
    const tableBody = document.querySelector('#checks-table tbody');
    const statusSummary = document.getElementById('status-summary');

    tableBody.innerHTML = '';
    const statusCounts = { up: 0, down: 0, new: 0 };

    if (checks.length === 0) {
        tableBody.innerHTML = '<tr><td colspan="8">No checks found.</td></tr>';
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
        
        let owner = `User ID: ${check.owner_id}`;
        if (check.owner) {
            if (check.owner.is_admin && check.owner.username) {
                owner = `admin (${check.owner.username})`;
            } else if (check.owner.auth_key) {
                owner = `key: ...${check.owner.auth_key.slice(-4)}`;
            }
        }


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
            'new': '🟡'
        }[currentStatus] || '⚪️';

        row.innerHTML = `
            <td><span class="status-${currentStatus}" title="${currentStatus.toUpperCase()}">${statusIcon} ${currentStatus.toUpperCase()}</span></td>
            <td>${check.name}</td>
            <td><small>${owner}</small></td>
            <td><input type="text" class="ping-url" value="${pingUrl}" readonly onclick="copyUrl(this)"></td>
            <td>${lastPing}</td>
            <td>${lastDuration}</td>
            <td>${expiresIn}</td>
            <td>
                <div class="grid" style="margin-bottom: 0; grid-template-columns: repeat(3, 1fr); gap: 0.5rem;">
                    <button class="outline action-button" title="Edit" onclick="editCheck(event, ${check.id}, '${check.name.replace(/'/g, "\\'")}', ${check.interval_seconds}, ${check.grace_seconds})">✏️</button>
                    <button class="outline action-button" title="Integrations" onclick="window.location.href='/admin/check/${check.id}/integrations'">⚙️</button>
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

function editCheck(event, id, name, interval, grace) {
    event.stopPropagation();
    const details = document.getElementById('new-check-section');
    if (details) {
        details.open = true;
    }
    const form = document.getElementById('new-check-form');
    form.querySelector('#name').value = name;
    form.querySelector('#interval_seconds').value = interval;
    form.querySelector('#grace_seconds').value = grace;
    
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
        details.querySelector('summary').textContent = 'New Check (Admin)';
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

    const data = {
        name: form.querySelector('#name').value,
        interval_seconds: parseInt(form.querySelector('#interval_seconds').value),
        grace_seconds: parseInt(form.querySelector('#grace_seconds').value)
    };

    const headers = {
        'Authorization': `Bearer ${apiToken}`,
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

async function deleteCheck(checkId) {
    if (!confirm('Are you sure you want to delete this check?')) return;

    const response = await fetch(`/api/v1/checks/${checkId}`, {
        method: 'DELETE',
        headers: { 
            'Authorization': `Bearer ${apiToken}`,
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
    // Set up page size selector
    const pageSizeSelector = document.getElementById('page-size');
    if (pageSizeSelector) {
        // Set initial value from localStorage or default
        const savedPageSize = localStorage.getItem('adminPageSizePreference');
        if (savedPageSize) {
            pageSize = parseInt(savedPageSize);
            pageSizeSelector.value = pageSize;
        }

        pageSizeSelector.addEventListener('change', (e) => {
            pageSize = parseInt(e.target.value);
            localStorage.setItem('adminPageSizePreference', pageSize.toString());
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
            fetchSystemStats();
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

    await Promise.all([fetchChecks(), fetchSystemStats(), fetchOperationalMetrics()]);

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

    setInterval(fetchSystemStats, 10000);
    document.getElementById('new-check-form').addEventListener('submit', handleFormSubmit);

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
            fetchChecks();
        });
    });
});

let authKey = window.AUTH_KEY;
let csrfToken = window.CSRF_TOKEN;
let isConnectionLost = false;
let currentSortBy = 'id';
let currentSortDir = 'desc';
let currentTagFilter = '';
let pageSize = 25;
let nextCursor = null;
let prevCursor = null;
let autoRefreshEnabled = true;
let autoRefreshInterval = 5; // seconds
let autoRefreshCountdown = autoRefreshInterval;
let autoRefreshTimer = null;
let checksData = {}; // Global cache for check data

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
                'X-CSRF-Token': csrfToken,
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

function debounce(func, delay) {
    let timeout;
    return function(...args) {
        const context = this;
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(context, args), delay);
    };
}

const checkSlugAvailability = debounce(async function(slug) {
    const feedbackEl = document.getElementById('slug-feedback');
    const slugInput = document.getElementById('slug');
    if (!slug) {
        feedbackEl.textContent = '';
        return;
    }

    feedbackEl.textContent = 'Checking availability...';
    feedbackEl.style.color = 'inherit';
    slugInput.setAttribute('aria-invalid', 'false');

    const form = document.getElementById('new-check-form');
    const editingId = form.dataset.editingId;
    
    let url = `/api/v1/checks/slug-check?slug=${encodeURIComponent(slug)}`;
    if (editingId) {
        url += `&check_id=${editingId}`;
    }

    try {
        const response = await fetch(url, {
            headers: { 'X-Auth-Key': authKey }
        });
        const data = await response.json();

        if (data.is_taken) {
            feedbackEl.textContent = 'Slug is already taken.';
            feedbackEl.style.color = 'var(--pico-color-orange-500)';
            slugInput.setAttribute('aria-invalid', 'true');
        } else {
            feedbackEl.textContent = 'Slug is available.';
            feedbackEl.style.color = 'var(--pico-color-green-500)';
            slugInput.setAttribute('aria-invalid', 'false');
        }
    } catch (error) {
        console.error('Error checking slug availability:', error);
        feedbackEl.textContent = 'Could not check availability.';
        feedbackEl.style.color = 'var(--pico-color-red-500)';
        slugInput.setAttribute('aria-invalid', 'true');
    }
}, 500); // 500ms delay

function validateSlugInput() {
    const slugInput = document.getElementById('slug');
    const feedbackEl = document.getElementById('slug-feedback');
    const slug = slugInput.value;

    if (!slug) {
        feedbackEl.textContent = '';
        slugInput.setAttribute('aria-invalid', 'false');
        return;
    }

    if (!slugInput.checkValidity()) {
        feedbackEl.textContent = 'Invalid characters. Use a-z, 0-9, -, _';
        feedbackEl.style.color = 'var(--pico-color-red-500)';
        slugInput.setAttribute('aria-invalid', 'true');
    } else {
        slugInput.setAttribute('aria-invalid', 'false');
        checkSlugAvailability(slug);
    }
}

function buildCronExpression() {
    // Times of Day
    const timeFrom = document.getElementById('time-from').value;
    const timeTo = document.getElementById('time-to').value;
    let hourPart = '*';
    if (timeFrom !== '00:00' || timeTo !== '23:59') {
        const fromHour = parseInt(timeFrom.split(':')[0]);
        const toHour = parseInt(timeTo.split(':')[0]);
        hourPart = `${fromHour}-${toHour}`;
    }

    // Days of Week
    const dowCheckboxes = document.querySelectorAll('#dow-grid input[type="checkbox"]');
    const checkedDow = Array.from(dowCheckboxes).filter(cb => cb.checked).map(cb => cb.value);
    let dowPart = '*';
    if (checkedDow.length > 0 && checkedDow.length < 7) {
        dowPart = checkedDow.join(',');
    }

    // Days of Month
    const domCheckboxes = document.querySelectorAll('#dom-grid input[type="checkbox"]');
    const checkedDom = Array.from(domCheckboxes).filter(cb => cb.checked).map(cb => cb.value);
    let domPart = '*';
    if (checkedDom.length > 0 && checkedDom.length < 31) {
        domPart = checkedDom.join(',');
    }

    // Assemble the expression (minute hour dom month dow)
    const cronExpression = `* ${hourPart} ${domPart} * ${dowPart}`;
    document.getElementById('schedule').value = cronExpression;
    document.getElementById('on-calendar-modal').close();
}

async function handleUnlinkTelegram() {
    if (!confirm('Are you sure you want to disconnect your Telegram account? You will no longer be able to log in via Telegram.')) {
        return;
    }

    const unlinkBtn = document.getElementById('unlink-telegram-btn');
    unlinkBtn.disabled = true;
    unlinkBtn.setAttribute('aria-busy', 'true');

    try {
        const response = await fetch('/api/v1/user/unlink-telegram', {
            method: 'POST',
            headers: {
                'X-Auth-Key': authKey,
                'X-CSRF-Token': csrfToken,
            }
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to disconnect Telegram account');
        }

        // On success, reload the page to show the login widget again
        alert('Telegram account disconnected successfully.');
        window.location.reload();

    } catch (error) {
        console.error('Error disconnecting Telegram:', error);
        alert(`Failed to disconnect Telegram account: ${error.message}`);
        unlinkBtn.disabled = false;
        unlinkBtn.removeAttribute('aria-busy');
    }
}

async function fetchAllTags() {
    try {
        const response = await fetch('/api/v1/checks/tags', {
            headers: { 'X-Auth-Key': authKey }
        });
        if (!response.ok) return;
        const tags = await response.json();
        renderTagFilter(tags);
    } catch (error) {
        console.error('Error fetching tags:', error);
    }
}

function renderTagFilter(tags) {
    const select = document.getElementById('tag-filter');
    if (!select) return;
    
    // Preserve the "All Checks" option and the currently selected value
    const selectedValue = select.value;
    select.innerHTML = '<option value="">All Checks</option>';
    
    tags.forEach(tag => {
        const option = document.createElement('option');
        option.value = tag.name;
        option.textContent = tag.name;
        select.appendChild(option);
    });
    select.value = selectedValue;
}

async function updateTelegramSection() {
    const telegramDiv = document.getElementById('telegram-management');
    if (!telegramDiv) return;

    try {
        const response = await fetch('/api/v1/user/me', {
            headers: { 'X-Auth-Key': authKey }
        });
        if (!response.ok) {
            // If user is not found (404), it means they are a new transient user,
            // so the default login widget is correct. We can just return.
            return;
        }
        
        const user = await response.json();
        
        if (user && user.telegram_user_id) {
            telegramDiv.innerHTML = `
                <p>Your account is linked to Telegram user: <strong>@${user.telegram_username || user.telegram_first_name}</strong></p>
                <button id="unlink-telegram-btn" class="secondary outline">Disconnect Telegram</button>
            `;
            // We need to re-add the event listener to the new button
            const unlinkBtn = document.getElementById('unlink-telegram-btn');
            if (unlinkBtn) {
                unlinkBtn.addEventListener('click', handleUnlinkTelegram);
            }
        }
        // If not linked, the default HTML from the template will remain, which is correct.
    } catch (error) {
        console.error('Error updating Telegram section:', error);
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
                <button class="outline action-button" title="Edit" onclick="editStatusPage(event, ${page.id}, '${(page.name || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n').replace(/\r/g, '\\r')}', '${page.slug.replace(/'/g, "\\'")}', [${page.checks.map(c => c.id)}])">✏️</button>
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
        'X-CSRF-Token': csrfToken,
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

function updateConnectionStatus(status) {
    const statusBar = document.getElementById('connection-status');
    const borderOverlay = document.getElementById('connection-border-overlay');
    if (!statusBar || !borderOverlay) return;

    if (status === 'error') {
        if (isConnectionLost) return; // Don't stack messages
        isConnectionLost = true;
        statusBar.textContent = 'Connection to the server was lost. Retrying...';
        statusBar.className = 'error';
        borderOverlay.className = 'error';
    } else if (status === 'success') {
        isConnectionLost = false;
        statusBar.textContent = 'Connection restored. Data is up to date.';
        statusBar.className = 'success';
        borderOverlay.className = 'success';
        setTimeout(() => {
            statusBar.className = ''; // Hide the bar
            borderOverlay.className = ''; // Hide the border
        }, 2500);
    }
}

async function deleteStatusPage(pageId) {
    if (!confirm('Are you sure you want to delete this status page?')) return;

    const response = await fetch(`/api/v1/status-pages/${pageId}`, {
        method: 'DELETE',
        headers: { 
            'X-Auth-Key': authKey,
            'X-CSRF-Token': csrfToken
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
            if (response.status >= 500) updateConnectionStatus('error');
            throw new Error('Failed to fetch operational metrics');
        }
        if (isConnectionLost) updateConnectionStatus('success');

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
        updateConnectionStatus('error');
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
    const deleteBtn = document.getElementById('delete-account-btn');
    const messageDiv = document.getElementById('import-message'); // Reuse this message div

    deleteBtn.disabled = true;
    deleteBtn.setAttribute('aria-busy', 'true');
    messageDiv.textContent = 'Deleting your account...';
    messageDiv.style.display = 'block';
    messageDiv.style.color = 'inherit';

    try {
        const response = await fetch('/api/v1/user/delete', {
            method: 'POST',
            headers: {
                'X-Auth-Key': authKey,
                'X-CSRF-Token': csrfToken,
            }
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to delete account');
        }

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
                'X-CSRF-Token': csrfToken,
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

function copyUrl(element, textToCopy) {
    if (element.dataset.copying) {
        return;
    }
    element.dataset.copying = 'true';
    const originalValue = element.value;
    const text = textToCopy || originalValue;

    navigator.clipboard.writeText(text).then(() => {
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
        return `${Math.round(seconds * 1000)} ms`;
    }
    if (seconds < 60) {
        const s = parseFloat(seconds.toFixed(1));
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
            if (response.status >= 500) updateConnectionStatus('error');
            throw new Error('Failed to fetch user stats');
        }
        if (isConnectionLost) updateConnectionStatus('success');

        const stats = await response.json();
        const summaryDiv = document.getElementById('user-stats-summary');

        const avgInterval = stats.avg_interval_seconds ? formatTimeDifference(stats.avg_interval_seconds).replace('in ', '') : 'N/A';
        const avgDuration = stats.avg_duration_seconds ? formatDuration(stats.avg_duration_seconds) : 'N/A';

        summaryDiv.innerHTML = `
            <div class="grid">
                <div><strong>Total:</strong> ${stats.total_checks}</div>
                <div><strong>Up:</strong> ${stats.up_count}</div>
                <div><strong>Down:</strong> ${stats.down_count}</div>
                <div><strong>Paused:</strong> ${stats.paused_count}</div>
            </div>
            <div class="grid">
                <div><strong>Avg. Interval:</strong> ${avgInterval}</div>
                <div><strong>Avg. Duration:</strong> ${avgDuration}</div>
                <div style="grid-column: span 2;"><strong>Notifications Queued:</strong> ${stats.user_queued_notifications}</div>
            </div>
        `;
    } catch (error) {
        console.error("Error fetching user stats:", error);
        updateConnectionStatus('error');
        const summaryDiv = document.getElementById('user-stats-summary');
        summaryDiv.innerHTML = `<p style="color: var(--pico-color-red-500);">Could not load user stats.</p>`;
    }
}

async function fetchChecks(cursor = null, direction = currentSortDir) {
    let url = `/api/v1/checks?size=${pageSize}&sort_by=${currentSortBy}&sort_direction=${direction}`;
    if (cursor) {
        url += `&cursor=${cursor}`;
    }
    if (currentTagFilter) {
        url += `&tag=${encodeURIComponent(currentTagFilter)}`;
    }
    
    try {
        const response = await fetch(url, {
            headers: { 'X-Auth-Key': authKey }
        });

        if (!response.ok) {
            if (response.status >= 500) updateConnectionStatus('error');
            if (response.status === 401) {
                const tableBody = document.querySelector('#checks-table tbody');
                tableBody.innerHTML = '<tr><td colspan="9" style="color: var(--pico-color-red-500);">Authentication failed. Your key may be invalid or expired. Please log in again.</td></tr>';
                stopAutoRefreshTimer();
            }
            return; // Stop processing on error
        }

        if (isConnectionLost) {
            updateConnectionStatus('success');
        }

        const data = await response.json();
    const checks = data.items;
    checks.forEach(c => checksData[c.id] = c); // Update global cache
    allChecksForStatusPage = checks; // Cache for status page form
    populateCheckCheckboxes(); // Populate form now that we have checks
    const tableBody = document.querySelector('#checks-table tbody');
    const statusSummary = document.getElementById('status-summary');
    
    tableBody.innerHTML = '';
    const statusCounts = { up: 0, down: 0, new: 0, paused: 0 };

    if (checks.length === 0) {
        tableBody.innerHTML = '<tr><td colspan="9">No checks found. Create one above!</td></tr>';
        if (statusSummary) {
            statusSummary.querySelector('.status-up').innerHTML = `<span>🟢</span> Up: 0`;
            statusSummary.querySelector('.status-down').innerHTML = `<span>🔴</span> Down: 0`;
            statusSummary.querySelector('.status-new').innerHTML = `<span>🟡</span> New: 0`;
            statusSummary.querySelector('.status-paused').innerHTML = `<span>⏸️</span> Paused: 0`;
        }
        updatePagination(null, null);
        return;
    }

    checks.forEach(check => {
        const row = document.createElement('tr');
        const lastPing = check.last_ping ? parseUTCDate(check.last_ping).toLocaleString() : 'Never';
        const pingIdentifier = check.slug || check.uuid;
        const pingUrl = `${window.location.origin}/p/${pingIdentifier}`;

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

        const displayStatus = check.paused ? 'paused' : currentStatus;

        if (displayStatus === 'up') statusCounts.up++;
        else if (displayStatus === 'down') statusCounts.down++;
        else if (displayStatus === 'new') statusCounts.new++;
        else if (displayStatus === 'paused') statusCounts.paused++;

        const statusIcon = {
            'up': '🟢',
            'down': '🔴',
            'new': '🟡',
            'paused': '⏸️'
        };
        
        const statusText = displayStatus.toUpperCase();
        const badgeUrl = `${window.location.origin}/p/${pingIdentifier}/badge.svg`;

        row.dataset.checkId = check.id;
        row.dataset.checkName = check.name;

        row.innerHTML = `
            <td><span class="status-${displayStatus}" title="${statusText}">${statusIcon[displayStatus] || '⚪️'} ${statusText}</span></td>
            <td>${check.name}</td>
            <td>${check.tags.map(t => `<span class="tag">${t.name}</span>`).join(' ')}</td>
            <td><input type="text" class="ping-url" value="Copy" readonly onclick="copyUrl(this, '${pingUrl}')" style="width: 10ch; text-align: center;"></td>
            <td><input type="text" class="ping-url" value="Copy" readonly onclick="copyUrl(this, '${badgeUrl}')" style="width: 10ch; text-align: center;"></td>
            <td>${lastPing}</td>
            <td>${lastDuration}</td>
            <td>${expiresIn}</td>
            <td>
                <button class="outline action-button" title="Recent Pings" onclick="viewRecentPings(${check.id})" ${!check.last_pings || check.last_pings.length === 0 ? 'disabled' : ''}>📜</button>
            </td>
            <td>
                <div class="grid" style="margin-bottom: 0; grid-template-columns: repeat(5, 1fr); gap: 0.5rem;">
                    <button class="outline action-button" title="Edit" onclick="editCheck(event, ${check.id})">✏️</button>
                    <button class="outline action-button" title="View Last Content" onclick="viewLastContent(${check.id})" ${!check.last_content ? 'disabled' : ''}>📄</button>
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
        statusSummary.querySelector('.status-paused').innerHTML = `<span>⏸️</span> Paused: ${statusCounts.paused}`;
    }
    updatePagination(data.next_cursor, data.prev_cursor);
    updateSortIndicators();

    } catch (error) {
        console.error("Network error during fetchChecks:", error);
        updateConnectionStatus('error');
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

function editCheck(event, id) {
    event.stopPropagation();
    const check = checksData[id];
    if (!check) {
        alert('Could not find check data to edit.');
        return;
    }
    const { name, slug, tags, schedule_type, schedule, tz, interval_seconds, grace_seconds, max_runtime_seconds, notify_after_failures, notify_on_up, expected_content, expected_content_type, use_regex_for_content } = check;

    const details = document.getElementById('new-check-section');
    if (details) {
        details.open = true;
    }
    const form = document.getElementById('new-check-form');
    form.querySelector('#name').value = name;
    form.querySelector('#slug').value = slug || '';
    form.querySelector('#tags').value = tags.map(t => t.name).join(', ');
    form.querySelector('#grace_seconds').value = grace_seconds;
    form.querySelector('#tz').value = tz || 'UTC';
    form.querySelector('#max_runtime_seconds').value = max_runtime_seconds || '';
    
    // Handle schedule type
    const scheduleTypeRadio = document.getElementById(`schedule-type-${schedule_type || 'interval'}`);
    if (scheduleTypeRadio) {
        scheduleTypeRadio.checked = true;
    }
    form.querySelector('#schedule').value = schedule || '';
    form.querySelector('#interval_seconds').value = interval_seconds || '';
    
    // Trigger change event to show/hide correct fields
    document.querySelector('input[name="schedule-type"]:checked').dispatchEvent(new Event('change'));

    // Clear slug feedback when populating the form for editing
    const slugFeedback = document.getElementById('slug-feedback');
    if (slugFeedback) {
        slugFeedback.textContent = '';
    }
    const slugInput = document.getElementById('slug');
    if (slugInput) {
        slugInput.setAttribute('aria-invalid', 'false');
    }

    // Notification settings
    form.querySelector('#notify_after_failures').value = notify_after_failures === null ? 0 : notify_after_failures;
    form.querySelector('#notify_on_up').checked = notify_on_up;

    // Content filtering
    form.querySelector('#expected_content').value = expected_content || '';
    form.querySelector('#expected_content_type').value = expected_content_type || 'present';
    form.querySelector('#use_regex_for_content').checked = use_regex_for_content;
    
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

    // Clear slug feedback
    const slugFeedback = document.getElementById('slug-feedback');
    if (slugFeedback) {
        slugFeedback.textContent = '';
    }
    const slugInput = document.getElementById('slug');
    if (slugInput) {
        slugInput.setAttribute('aria-invalid', 'false');
    }

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
    const submitButton = form.querySelector('button[type="submit"]');

    submitButton.disabled = true;
    submitButton.setAttribute('aria-busy', 'true');

    try {
        const maxRuntimeInput = form.querySelector('#max_runtime_seconds');
        const maxRuntimeValue = maxRuntimeInput.value ? parseInt(maxRuntimeInput.value) : null;

        const scheduleType = form.querySelector('input[name="schedule-type"]:checked').value;
        const schedule = form.querySelector('#schedule').value;
        const interval = form.querySelector('#interval_seconds').value;

        const data = {
            name: form.querySelector('#name').value,
            slug: form.querySelector('#slug').value || null,
            tags: form.querySelector('#tags').value.split(',').map(t => t.trim()).filter(Boolean),
            schedule_type: scheduleType,
            schedule: (scheduleType === 'cron' || scheduleType === 'oncalendar') ? (schedule || null) : null,
            interval_seconds: scheduleType === 'interval' ? (interval ? parseInt(interval) : null) : null,
            tz: form.querySelector('#tz').value || 'UTC',
            grace_seconds: parseInt(form.querySelector('#grace_seconds').value),
            max_runtime_seconds: maxRuntimeValue,
            notify_after_failures: parseInt(form.querySelector('#notify_after_failures').value) || null,
            notify_on_up: form.querySelector('#notify_on_up').checked,
            expected_content: form.querySelector('#expected_content').value,
            expected_content_type: form.querySelector('#expected_content_type').value,
            use_regex_for_content: form.querySelector('#use_regex_for_content').checked
        };

        const headers = {
            'X-Auth-Key': authKey,
            'X-CSRF-Token': csrfToken,
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
            fetchAllTags(); // Refresh tag filter to include any new tags
        } else {
            const error = await response.json();
            alert(`Failed to ${editingId ? 'update' : 'create'} check: ${error.detail}`);
        }
    } catch (error) {
        console.error('Error submitting check form:', error);
        alert(`An unexpected error occurred: ${error.message}`);
    } finally {
        submitButton.disabled = false;
        submitButton.removeAttribute('aria-busy');
    }
}

async function togglePause(checkId) {
    const response = await fetch(`/api/v1/checks/${checkId}/toggle-pause`, {
        method: 'POST',
        headers: { 
            'X-Auth-Key': authKey,
            'X-CSRF-Token': csrfToken
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
            'X-CSRF-Token': csrfToken
        }
    });

    if (response.ok) {
        fetchChecks();
    } else {
        alert('Failed to delete check.');
    }
}

function viewLastContent(checkId) {
    const modal = document.getElementById('content-modal');
    const contentDiv = document.getElementById('content-modal-content');
    const checkNameSpan = document.getElementById('content-modal-check-name');

    const check = checksData[checkId];
    if (!check) return;

    checkNameSpan.textContent = check.name;
    modal.showModal();

    if (check.last_content) {
        // Use <pre> and <code> for better formatting of raw content
        const pre = document.createElement('pre');
        const code = document.createElement('code');
        code.textContent = check.last_content;
        pre.appendChild(code);
        contentDiv.innerHTML = '';
        contentDiv.appendChild(pre);
    } else {
        contentDiv.innerHTML = '<p>No content recorded for this check.</p>';
    }
}

function viewRecentPings(checkId) {
    const modal = document.getElementById('pings-modal');
    const contentDiv = document.getElementById('pings-modal-content');
    const checkNameSpan = document.getElementById('pings-modal-check-name');
    
    const check = checksData[checkId];
    if (!check) return;

    checkNameSpan.textContent = check.name;
    const pings = check.last_pings;

    if (!pings || pings.length === 0) {
        contentDiv.innerHTML = '<p>No recent pings recorded.</p>';
    } else {
        const now = new Date();
        contentDiv.innerHTML = pings.map(ping => {
            const pingDate = new Date(ping.timestamp);
            const diffSeconds = (pingDate - now) / 1000;
            const timeAgo = formatTimeDifference(diffSeconds);
            return `
            <div class="ping-log-entry">
                <p><strong><span class="fi fi-${ping.country_code.toLowerCase()}"></span> ${ping.country_name}</strong> - <code>${ping.connection_type}</code></p>
                <p><small><code>${pingDate.toLocaleString()} (${timeAgo})</code></small></p>
                <p><small><strong>IP:</strong> <code>${ping.ip_address}</code></small></p>
                <p><small><strong>Agent:</strong> <code>${ping.user_agent}</code></small></p>
            </div>
        `}).join('<hr class="modal-hr">');
    }

    modal.showModal();
}

document.addEventListener('DOMContentLoaded', async () => {
    // Cron Builder Modal setup
    const showCronBuilderBtn = document.getElementById('show-cron-builder-btn');
    if (showCronBuilderBtn) {
        showCronBuilderBtn.addEventListener('click', () => {
            // Reset modal to default state when opening
            document.getElementById('time-from').value = '00:00';
            document.getElementById('time-to').value = '23:59';
            document.querySelectorAll('#dow-grid input, #dom-grid input').forEach(cb => cb.checked = false);
            document.getElementById('on-calendar-modal').showModal();
        });
    }

    const applyCalendarBtn = document.getElementById('apply-calendar-btn');
    if (applyCalendarBtn) {
        applyCalendarBtn.addEventListener('click', buildCronExpression);
    }

    // Populate checkbox grids
    const dowGrid = document.getElementById('dow-grid');
    if (dowGrid) {
        const days = [{label: 'Mon', value: 1}, {label: 'Tue', value: 2}, {label: 'Wed', value: 3}, {label: 'Thu', value: 4}, {label: 'Fri', value: 5}, {label: 'Sat', value: 6}, {label: 'Sun', value: 0}];
        dowGrid.innerHTML = days.map(day => `
            <label>
                <input type="checkbox" value="${day.value}">
                ${day.label}
            </label>
        `).join('');
    }

    const domGrid = document.getElementById('dom-grid');
    if (domGrid) {
        let checkboxes = '';
        for (let i = 1; i <= 31; i++) {
            checkboxes += `
                <label>
                    <input type="checkbox" value="${i}">
                    ${i}
                </label>
            `;
        }
        domGrid.innerHTML = checkboxes;
    }

    // Slug validation listener
    const slugInput = document.getElementById('slug');
    if (slugInput) {
        slugInput.addEventListener('input', validateSlugInput);
    }

    // Schedule type toggle
    document.querySelectorAll('input[name="schedule-type"]').forEach(radio => {
        radio.addEventListener('change', (event) => {
            const scheduleType = event.target.value;
            const simpleFields = document.getElementById('simple-schedule-fields');
            const textFields = document.getElementById('text-schedule-fields');
            const scheduleInput = document.getElementById('schedule');
            const intervalInput = document.getElementById('interval_seconds');
            const scheduleLabel = document.getElementById('schedule-label');
            const cronBuilderBtn = document.getElementById('show-cron-builder-btn');
            const oncalendarHelpBtn = document.getElementById('oncalendar-help-btn');

            simpleFields.style.display = 'none';
            textFields.style.display = 'none';
            intervalInput.required = false;
            scheduleInput.required = false;
            cronBuilderBtn.style.display = 'none';
            oncalendarHelpBtn.style.display = 'none';

            if (scheduleType === 'interval') {
                simpleFields.style.display = 'block';
                intervalInput.required = true;
            } else if (scheduleType === 'cron') {
                textFields.style.display = 'block';
                scheduleInput.required = true;
                scheduleLabel.textContent = 'Cron Expression';
                scheduleInput.placeholder = '* * * * *';
                cronBuilderBtn.style.display = 'inline-block';
            } else if (scheduleType === 'oncalendar') {
                textFields.style.display = 'block';
                scheduleInput.required = true;
                scheduleLabel.textContent = 'On-Calendar Expression';
                scheduleInput.placeholder = 'Mon,Fri *-*-* 12:00:00';
                oncalendarHelpBtn.style.display = 'inline-block';
            }
        });
    });

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

    await Promise.all([fetchChecks(), fetchUserStats(), fetchOperationalMetrics(), fetchStatusPages(), fetchAllTags()]);
    await updateTelegramSection();
    
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
    const unlinkTelegramBtn = document.getElementById('unlink-telegram-btn');

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
    if (unlinkTelegramBtn) {
        unlinkTelegramBtn.addEventListener('click', handleUnlinkTelegram);
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

    // Tag filter listener
    const tagFilter = document.getElementById('tag-filter');
    if (tagFilter) {
        tagFilter.addEventListener('change', (e) => {
            currentTagFilter = e.target.value;
            fetchChecks(); // Reset to first page on new filter
        });
    }

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

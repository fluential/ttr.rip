import { getCsrfToken, refreshAdminToken, fetchWithAuth } from '/static/js/shared/auth.js';
const checkId = window.CHECK_ID;
const isAdmin = window.IS_ADMIN;
let apiToken = sessionStorage.getItem('admin_access_token');



const authKey = window.AUTH_KEY;
const csrfToken = window.CSRF_TOKEN;

function updateIntegrationStatusUI(integration, check) {
    const container = document.querySelector(`.integration-status-container[data-integration="${integration}"]`);
    if (!container) return;

    const statusTextElem = container.querySelector('.integration-status-text');
    const detailsContainer = container.querySelector('.integration-status-details');
    const timeElem = container.querySelector('.last-notification-time');
    const messageElem = container.querySelector('.last-notification-message');

    container.style.backgroundColor = 'var(--pico-card-background-color)';
    container.style.color = 'inherit';

    const status = check[`${integration}_last_notification_status`];
    const timestamp = check[`${integration}_last_notification_timestamp`];
    const message = check[`${integration}_last_notification_message`];

    if (status === 'ok') {
        container.style.backgroundColor = 'var(--pico-color-green-200)';
        container.style.color = 'var(--pico-color-green-900)';
        statusTextElem.innerHTML = '<strong>Status: ✅ OK</strong>';
    } else if (status === 'error') {
        container.style.backgroundColor = 'var(--pico-color-red-200)';
        container.style.color = 'var(--pico-color-red-900)';
        statusTextElem.innerHTML = '<strong>Status: ❌ Error</strong>';
    } else {
        statusTextElem.innerHTML = '<strong>Status: Unknown</strong> (No notification sent yet)';
    }

    if (timestamp) {
        const date = new Date(timestamp);
        timeElem.textContent = date.toLocaleString();
        messageElem.textContent = message;
        detailsContainer.style.display = 'block';
    } else {
        detailsContainer.style.display = 'none';
    }

    // Pre-create a holder for rate info to be updated asynchronously
    try {
        const rateInfoElClass = 'integration-rate-info';
        let rateEl = container.querySelector(`.${rateInfoElClass}`);
        if (!rateEl) {
            rateEl = document.createElement('div');
            rateEl.className = rateInfoElClass;
            rateEl.style.marginTop = '0.25rem';
            rateEl.style.fontSize = '0.9em';
            container.appendChild(rateEl);
        }
        // Leave content update to fetchIntegrationRate()
    } catch {}
}

async function fetchIntegrationRate(integration) {
    const container = document.querySelector(`.integration-status-container[data-integration="${integration}"]`);
    if (!container) return;

    let rateEl = container.querySelector('.integration-rate-info');
    if (!rateEl) {
        rateEl = document.createElement('div');
        rateEl.className = 'integration-rate-info';
        rateEl.style.marginTop = '0.25rem';
        rateEl.style.fontSize = '0.9em';
        container.appendChild(rateEl);
    }
    // Show a placeholder immediately so the user sees the field even if the request fails
    rateEl.innerHTML = `<strong>Rate:</strong> loading…`;

    try {
        const res = await fetchWithAuth(`/api/v1/checks/${checkId}/${integration}/rate`);
        if (!res.ok) {
            const icon = '⚠️';
            const msg = res.status === 401 ? 'unauthorized' : 'unavailable';
            rateEl.innerHTML = `<strong>Rate:</strong> ${icon} ${msg}`;
            return;
        }

        const data = await res.json();

        if (data && data.enabled === false) {
            rateEl.innerHTML = `<strong>Rate:</strong> disabled`;
            return;
        }

        const perMin = data && typeof data.current_rps_per_minute === 'number'
            ? data.current_rps_per_minute.toFixed(2) : 'N/A';
        const minPerMin = data && typeof data.min_rps_per_minute === 'number'
            ? data.min_rps_per_minute.toFixed(2) : 'N/A';
        const burst = data && typeof data.max_tokens === 'number'
            ? data.max_tokens : 'N/A';
        const limitPerMin = data && typeof data.assumed_limit_per_minute === 'number'
            ? data.assumed_limit_per_minute.toFixed(0) : '30';
        const sentPerMin = data && typeof data.sent_per_minute === 'number'
            ? data.sent_per_minute.toFixed(0) : 'N/A';

        let statusIcon = '✅';
        let statusText = 'normal';
        if (data && data.limited) {
            statusIcon = '⚠️';
            if (data.limited_reason === 'backoff') {
                const secs = Math.max(0, Math.ceil(data.backoff_seconds_remaining || 0));
                statusText = `backoff ${secs}s`;
            } else {
                const ra = data.retry_after_seconds ? Math.ceil(data.retry_after_seconds) : null;
                statusText = `limited${ra ? `, retry ~${ra}s` : ''}`;
            }
        }

        rateEl.innerHTML = `<strong>Rate:</strong> sent ${sentPerMin}/min, allowed ${perMin}/min (limit ${limitPerMin}/min, min ${minPerMin}/min, burst ${burst}) — ${statusIcon} ${statusText}`;
    } catch (e) {
        console.error("Failed to load rate snapshot:", e);
        rateEl.innerHTML = `<strong>Rate:</strong> ⚠️ unavailable`;
    }
}

async function handleFormSubmit(event) {
    event.preventDefault();
    const form = event.target;
    const integration = form.dataset.integration;
    const formMessage = form.nextElementSibling;
    const submitButton = form.querySelector('button[type="submit"]');

    submitButton.setAttribute('aria-busy', 'true');
    submitButton.disabled = true;
    formMessage.textContent = 'Saving...';
    formMessage.style.color = 'inherit';

    const formData = new FormData(form);
    const data = Object.fromEntries(formData.entries());
    // Handle checkbox
    data[`${integration}_enabled`] = form.querySelector(`input[name="${integration}_enabled"]`).checked;

    const headers = { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken };
    const response = await fetchWithAuth(`/api/v1/checks/${checkId}/${integration}`, {
        method: 'PUT',
        headers: headers,
        body: JSON.stringify(data)
    });

    submitButton.removeAttribute('aria-busy');
    submitButton.disabled = false;

    if (response.ok) {
        formMessage.textContent = 'Settings saved successfully!';
        formMessage.style.color = 'var(--pico-color-green-500)';
        // Clear password fields after successful save
        form.querySelectorAll('input[type="password"]').forEach(input => input.value = '');
        // Refresh rate info after saving settings
        fetchIntegrationRate(integration);
    } else {
        const error = await response.json();
        formMessage.textContent = `Failed to save settings: ${error.detail || 'Unknown error'}`;
        formMessage.style.color = 'var(--pico-color-red-500)';
    }
}

async function handleTestClick(event, useQueue = false) {
    event.preventDefault();
    const button = event.target;
    const form = button.closest('form');
    const integration = form.dataset.integration;
    const formMessage = form.nextElementSibling;

    button.setAttribute('aria-busy', 'true');
    button.disabled = true;
    formMessage.textContent = useQueue ? 'Queueing test message...' : 'Sending test message...';
    formMessage.style.color = 'inherit';

    const headers = { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken };
    const url = `/api/v1/checks/${checkId}/${integration}/test${useQueue ? '-queue' : ''}`;
    const response = await fetchWithAuth(url, { method: 'POST', headers: headers });

    button.removeAttribute('aria-busy');
    button.disabled = false;

    if (response.ok) {
        if (useQueue) {
            formMessage.textContent = 'Test message queued successfully! Status will update after processing.';
            formMessage.style.color = 'var(--pico-color-green-500)';
        } else {
            const updatedCheck = await response.json();
            updateIntegrationStatusUI(integration, updatedCheck);
            formMessage.textContent = 'Test message sent successfully!';
            formMessage.style.color = 'var(--pico-color-green-500)';
        }
        // Refresh rate info after a send attempt
        fetchIntegrationRate(integration);
    } else {
        const error = await response.json();
        formMessage.textContent = `Failed to send test: ${error.detail || 'Unknown error'}`;
        formMessage.style.color = 'var(--pico-color-red-500)';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('form[data-integration]').forEach(form => {
        form.addEventListener('submit', handleFormSubmit);
    });

    document.querySelectorAll('.test-btn').forEach(button => {
        button.addEventListener('click', (e) => handleTestClick(e, false));
    });

    document.querySelectorAll('.test-queue-btn').forEach(button => {
        button.addEventListener('click', (e) => handleTestClick(e, true));
    });

    document.querySelectorAll('.last-notification-time').forEach(elem => {
        if (elem.textContent) {
            try {
                const date = new Date(elem.textContent);
                elem.textContent = date.toLocaleString();
            } catch (e) {
                console.error("Could not parse date:", elem.textContent);
            }
        }
    });

    // Fetch rate info for all integrations on page load
    document.querySelectorAll('form[data-integration]').forEach(form => {
        const integ = form.dataset.integration;
        if (integ) fetchIntegrationRate(integ);
    });
});

const checkId = window.CHECK_ID;
const isAdmin = window.IS_ADMIN;
const apiToken = window.API_TOKEN;
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
    if (isAdmin) headers['Authorization'] = `Bearer ${apiToken}`;
    else headers['X-Auth-Key'] = authKey;

    const response = await fetch(`/api/v1/checks/${checkId}/${integration}`, {
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
    if (isAdmin) headers['Authorization'] = `Bearer ${apiToken}`;
    else headers['X-Auth-Key'] = authKey;

    const url = `/api/v1/checks/${checkId}/${integration}/test${useQueue ? '-queue' : ''}`;
    const response = await fetch(url, { method: 'POST', headers: headers });

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
});

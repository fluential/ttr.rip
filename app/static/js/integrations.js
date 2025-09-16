const checkId = window.CHECK_ID;
const isAdmin = window.IS_ADMIN;
const apiToken = window.API_TOKEN;
const authKey = window.AUTH_KEY;
const csrfToken = window.CSRF_TOKEN;

function getCsrfToken() {
    const cookies = document.cookie.split(';').map(c => c.trim());
    const csrfCookie = cookies.find(c => c.startsWith('csrf_token='));
    return csrfCookie ? csrfCookie.split('=')[1] : null;
}

function updateIntegrationStatusUI(check) {
    const container = document.getElementById('integration-status-container');
    const statusTextElem = document.getElementById('integration-status-text');
    const detailsContainer = document.getElementById('integration-status-details');
    const timeElem = document.getElementById('last-notification-time');
    const messageElem = document.getElementById('last-notification-message');

    // Reset styles
    container.style.backgroundColor = 'var(--pico-card-background-color)';
    container.style.color = 'inherit';

    if (check.telegram_last_notification_status === 'ok') {
        container.style.backgroundColor = 'var(--pico-color-green-200)';
        container.style.color = 'var(--pico-color-green-900)';
        statusTextElem.innerHTML = '<strong>Status: ✅ OK</strong>';
    } else if (check.telegram_last_notification_status === 'error') {
        container.style.backgroundColor = 'var(--pico-color-red-200)';
        container.style.color = 'var(--pico-color-red-900)';
        statusTextElem.innerHTML = '<strong>Status: ❌ Error</strong>';
    } else {
        statusTextElem.innerHTML = '<strong>Status: Unknown</strong> (No notification sent yet)';
    }

    if (check.telegram_last_notification_timestamp) {
        const date = new Date(check.telegram_last_notification_timestamp);
        timeElem.textContent = date.toLocaleString();
        messageElem.textContent = check.telegram_last_notification_message;
        detailsContainer.style.display = 'block';
    } else {
        detailsContainer.style.display = 'none';
    }
}

async function handleFormSubmit(event) {
event.preventDefault();
const form = event.target;
const formMessage = document.getElementById('form-message');
const submitButton = form.querySelector('button[type="submit"]');

submitButton.setAttribute('aria-busy', 'true');
submitButton.disabled = true;
formMessage.textContent = 'Saving...';
formMessage.style.color = 'inherit';

const data = {
    telegram_bot_token: form.querySelector('#telegram_bot_token').value,
    telegram_chat_id: form.querySelector('#telegram_chat_id').value,
    telegram_enabled: form.querySelector('#telegram_enabled').checked
};

const headers = {
    'Content-Type': 'application/json',
    'X-CSRF-Token': getCsrfToken()
};
if (isAdmin) {
    headers['Authorization'] = `Bearer ${apiToken}`;
} else {
    headers['X-Auth-Key'] = authKey;
}

const response = await fetch(`/api/v1/checks/${checkId}/telegram`, {
    method: 'PUT',
    headers: headers,
    body: JSON.stringify(data)
});

submitButton.removeAttribute('aria-busy');
submitButton.disabled = false;

if (response.ok) {
    formMessage.textContent = 'Settings saved successfully!';
    formMessage.style.color = 'var(--pico-color-green-500)';
} else {
    const error = await response.json();
    formMessage.textContent = `Failed to save settings: ${error.detail || 'Unknown error'}`;
    formMessage.style.color = 'var(--pico-color-red-500)';
}
}

async function handleTestQueueClick(event) {
event.preventDefault();
const formMessage = document.getElementById('form-message');
const testQueueButton = document.getElementById('test-queue-btn');

testQueueButton.setAttribute('aria-busy', 'true');
testQueueButton.disabled = true;
formMessage.textContent = 'Queueing test message...';
formMessage.style.color = 'inherit';

const headers = {
    'Content-Type': 'application/json',
    'X-CSRF-Token': getCsrfToken()
};
if (isAdmin) {
    headers['Authorization'] = `Bearer ${apiToken}`;
} else {
    headers['X-Auth-Key'] = authKey;
}

const response = await fetch(`/api/v1/checks/${checkId}/telegram/test-queue`, {
    method: 'POST',
    headers: headers
});

testQueueButton.removeAttribute('aria-busy');
testQueueButton.disabled = false;

if (response.ok) {
    formMessage.textContent = 'Test message queued successfully! Status will update after processing.';
    formMessage.style.color = 'var(--pico-color-green-500)';
} else {
    const error = await response.json();
    formMessage.textContent = `Failed to queue test message: ${error.detail || 'Unknown error'}`;
    formMessage.style.color = 'var(--pico-color-red-500)';
}
}

async function handleTestClick(event) {
event.preventDefault();
const formMessage = document.getElementById('form-message');
const testButton = document.getElementById('test-telegram-btn');

testButton.setAttribute('aria-busy', 'true');
testButton.disabled = true;
formMessage.textContent = 'Sending test connection message...';
formMessage.style.color = 'inherit';

const headers = {
    'Content-Type': 'application/json',
    'X-CSRF-Token': getCsrfToken()
};
if (isAdmin) {
    headers['Authorization'] = `Bearer ${apiToken}`;
} else {
    headers['X-Auth-Key'] = authKey;
}

const response = await fetch(`/api/v1/checks/${checkId}/telegram/test`, {
    method: 'POST',
    headers: headers
});

testButton.removeAttribute('aria-busy');
testButton.disabled = false;

if (response.ok) {
    const updatedCheck = await response.json();
    updateIntegrationStatusUI(updatedCheck);
    formMessage.textContent = 'Test connection message sent successfully!';
    formMessage.style.color = 'var(--pico-color-green-500)';
} else {
    const error = await response.json();
    formMessage.textContent = `Failed to send test connection message: ${error.detail || 'Unknown error'}`;
    formMessage.style.color = 'var(--pico-color-red-500)';
}
}

document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('telegram-settings-form');
    if (form) {
        form.addEventListener('submit', handleFormSubmit);
    }
    const testButton = document.getElementById('test-telegram-btn');
    if (testButton) {
        testButton.addEventListener('click', handleTestClick);
    }

    const testQueueButton = document.getElementById('test-queue-btn');
    if (testQueueButton) {
        testQueueButton.addEventListener('click', handleTestQueueClick);
    }

    const lastNotificationTimeElem = document.getElementById('last-notification-time');
    if (lastNotificationTimeElem && lastNotificationTimeElem.textContent) {
        try {
            const date = new Date(lastNotificationTimeElem.textContent);
            lastNotificationTimeElem.textContent = date.toLocaleString();
        } catch (e) {
            console.error("Could not parse date:", lastNotificationTimeElem.textContent);
        }
    }
});

export function getCsrfToken() {
  try {
    const cookies = document.cookie.split(";").map((c) => c.trim());
    const csrfCookie = cookies.find((c) => c.startsWith("csrf_token="));
    return csrfCookie ? csrfCookie.split("=")[1] : null;
  } catch {
    return null;
  }
}

export async function refreshAdminToken() {
  try {
    const res = await fetch("/admin/token/refresh", {
      method: "POST",
      headers: { "X-CSRF-Token": getCsrfToken() },
    });
    if (!res.ok) throw new Error("Refresh failed");
    const data = await res.json();
    const token = data.access_token;
    if (token) {
      sessionStorage.setItem("admin_access_token", token);
    }
    return true;
  } catch (e) {
    console.error("Could not refresh token:", e);
    sessionStorage.removeItem("admin_access_token");
    try {
      window.location.href = "/admin/login";
    } catch {}
    return false;
  }
}

/**
 * fetchWithAuth automatically attaches auth headers:
 * - Admin: Bearer sessionStorage.admin_access_token (with auto-refresh on 401)
 * - Public: X-Auth-Key from window.AUTH_KEY
 */
export async function fetchWithAuth(url, options = {}) {
  const isAdmin = Boolean(window.IS_ADMIN);
  const opts = { ...options, headers: { ...(options.headers || {}) } };

  if (isAdmin) {
    const token = sessionStorage.getItem("admin_access_token");
    if (token) opts.headers["Authorization"] = `Bearer ${token}`;
  } else if (window.AUTH_KEY) {
    opts.headers["X-Auth-Key"] = window.AUTH_KEY;
  }

  let res = await fetch(url, opts);
  if (isAdmin && res.status === 401) {
    const refreshed = await refreshAdminToken();
    if (!refreshed) return res;
    const newToken = sessionStorage.getItem("admin_access_token");
    if (newToken) opts.headers["Authorization"] = `Bearer ${newToken}`;
    res = await fetch(url, opts);
  }
  return res;
}

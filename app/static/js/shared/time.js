export function parseUTCDate(dateString) {
  if (!dateString) return null;
  if (!/Z|[+-]\d{2}:\d{2}$/.test(dateString)) {
    return new Date(dateString.replace(" ", "T") + "Z");
  }
  return new Date(dateString);
}

export function formatTimeDifference(seconds) {
  if (isNaN(seconds)) return "N/A";
  const isPast = seconds < 0;
  seconds = Math.abs(seconds);
  if (seconds < 1) return isPast ? "0 seconds ago" : "in 0 seconds";

  const days = Math.floor(seconds / 86400);
  seconds %= 86400;
  const hours = Math.floor(seconds / 3600);
  seconds %= 3600;
  const minutes = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);

  const parts = [];
  if (days > 0) parts.push(`${days} day${days > 1 ? "s" : ""}`);
  if (hours > 0) parts.push(`${hours} hour${hours > 1 ? "s" : ""}`);
  if (minutes > 0) parts.push(`${minutes} minute${minutes > 1 ? "s" : ""}`);
  if (secs > 0 || parts.length === 0) parts.push(`${secs} second${secs !== 1 ? "s" : ""}`);

  const result = parts.slice(0, 2).join(" ");
  return isPast ? `${result} ago` : `in ${result}`;
}

export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || isNaN(seconds)) return "N/A";
  if (seconds < 0) return "N/A";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) {
    const s = parseFloat(seconds.toFixed(1));
    return `${s} second${s !== 1 ? "s" : ""}`;
  }
  const minutes = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  const parts = [];
  if (minutes > 0) parts.push(`${minutes} minute${minutes > 1 ? "s" : ""}`);
  if (secs > 0) parts.push(`${secs} second${secs !== 1 ? "s" : ""}`);
  return parts.join(" ");
}

/** Optional helper for relative time used on public page */
export function formatTimeAgo(date) {
  const seconds = Math.floor((new Date() - date) / 1000);
  if (seconds < 0) return "just now";
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds} seconds ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes > 1 ? "s" : ""} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours > 1 ? "s" : ""} ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} day${days > 1 ? "s" : ""} ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} month${months > 1 ? "s" : ""} ago`;
  const years = Math.floor(days / 365);
  return `${years} year${years > 1 ? "s" : ""} ago`;
}

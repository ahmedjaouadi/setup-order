import { formatSetupValidationDetail, normalizeDetailMessages } from "./setup-messages.js";

export async function api(path, options = {}) {
  const request = { ...options };
  request.headers = { ...(request.headers || {}) };
  if (request.body && typeof request.body !== "string") {
    request.headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(request.body);
  }
  const response = await fetch(path, request);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = formatErrorDetail(data.detail);
    throw new Error(detail || response.statusText);
  }
  return data;
}

export async function optionalApi(path, options = {}) {
  const request = { ...options };
  request.headers = { ...(request.headers || {}) };
  if (request.body && typeof request.body !== "string") {
    request.headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(request.body);
  }
  const response = await fetch(path, request);
  if (response.status === 404) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = formatErrorDetail(data.detail);
    throw new Error(detail || response.statusText);
  }
  return data;
}

export function formatErrorDetail(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.join(", ");
  const setupValidationMessage = formatSetupValidationDetail(detail);
  if (setupValidationMessage) return setupValidationMessage;
  if (detail.issues && Array.isArray(detail.issues)) {
    const messages = detail.issues
      .map((item) => item && (item.message || item.code))
      .filter(Boolean);
    if (messages.length) return messages.join(", ");
  }
  if (detail.save_validation && Array.isArray(detail.save_validation.errors)) {
    return normalizeDetailMessages(detail.save_validation.errors).join(", ");
  }
  if (detail.errors) return normalizeDetailMessages(detail.errors).join(", ");
  if (detail.detail) return formatErrorDetail(detail.detail);
  if (detail.message) return String(detail.message);
  return JSON.stringify(detail);
}

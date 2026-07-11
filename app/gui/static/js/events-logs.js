import { api } from "./api-client.js";
import { escapeHtml, formData, formatTime } from "./ui-helpers.js";

export function renderEvents(containerId, events) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = events.map((event) => {
    const data = event.data && Object.keys(event.data).length
      ? `<pre class="event-data">${escapeHtml(JSON.stringify(event.data, null, 2))}</pre>`
      : "";
    return `
      <article class="event-item">
        <time>${escapeHtml(formatTime(event.timestamp))}</time>
        <span>${escapeHtml(event.level)}</span>
        <div>
          <strong>${escapeHtml(event.event_type)}</strong>
          <div>${escapeHtml(event.message)}</div>
          ${data}
        </div>
      </article>
    `;
  }).join("") || `<article class="event-item"><span>Aucun evenement</span></article>`;
}

export function renderTwsEvents(containerId, events) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = events.map((event) => {
    const data = event.data || {};
    const extra = data.extra && Object.keys(data.extra).length
      ? `<pre class="event-data">${escapeHtml(JSON.stringify(data.extra, null, 2))}</pre>`
      : "";
    const detailParts = [
      data.request ? `Req: ${data.request}` : "",
      data.detail ? `Detail: ${data.detail}` : "",
      data.sent_at ? `Envoyee: ${formatTime(data.sent_at)}` : "",
      data.response_at ? `Reponse: ${formatTime(data.response_at)}` : "",
      data.latency_ms != null ? `Latence: ${data.latency_ms} ms` : "",
      data.status ? `Statut: ${data.status}` : "",
      data.error ? `Erreur: ${data.error}` : "",
    ].filter(Boolean);
    const meta = detailParts.length
      ? `<div class="event-meta">${escapeHtml(detailParts.join(" | "))}</div>`
      : "";
    return `
      <article class="event-item">
        <time>${escapeHtml(formatTime(event.timestamp))}</time>
        <span>${escapeHtml(event.level)}</span>
        <div>
          <strong>${escapeHtml(event.message || event.event_type)}</strong>
          ${meta}
          ${extra}
        </div>
      </article>
    `;
  }).join("") || `<article class="event-item"><span>Aucun echange TWS</span></article>`;
}

export async function renderLogsPage() {
  const container = document.getElementById("logs-events");
  const form = document.getElementById("logs-filter");
  const twsContainer = document.getElementById("logs-tws-events");

  async function loadLogs() {
    if (container) {
      const result = await api("/api/events?limit=200");
      renderEvents("logs-events", result.items || []);
    }
    if (twsContainer) {
      const tws = await api("/api/logs/tws?limit=200");
      renderTwsEvents("logs-tws-events", tws.items || []);
    }
  }

  if (!container && !twsContainer) return;
  await loadLogs();
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = formData(form);
    const params = new URLSearchParams();
    Object.entries(data).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    const query = params.toString();
    const filtered = await api(`/api/events?limit=200${query ? `&${query}` : ""}`);
    renderEvents("logs-events", filtered.items || []);
    const twsFiltered = await api(`/api/logs/tws?limit=200${query ? `&${query}` : ""}`);
    renderTwsEvents("logs-tws-events", twsFiltered.items || []);
  });
}

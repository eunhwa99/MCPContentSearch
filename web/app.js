const state = {
  activeTab: "answer",
  busy: false,
  lastPayload: null,
  lastKind: "",
  lastMarkdown: "",
  syncPollTimer: null,
  syncPollToken: 0,
  activeSyncSourceId: "",
  activeSyncJobId: "",
  syncAwaitingResponse: false,
};

const elements = {
  healthBadge: document.querySelector("#healthBadge"),
  statusText: document.querySelector("#statusText"),
  questionInput: document.querySelector("#questionInput"),
  answerModeSelect: document.querySelector("#answerModeSelect"),
  topicInput: document.querySelector("#topicInput"),
  sourceIdInput: document.querySelector("#sourceIdInput"),
  topKInput: document.querySelector("#topKInput"),
  githubRepositoryInput: document.querySelector("#githubRepositoryInput"),
  targetSourceTypeSelect: document.querySelector("#targetSourceTypeSelect"),
  targetSyncInput: document.querySelector("#targetSyncInput"),
  requireGeneratedInput: document.querySelector("#requireGeneratedInput"),
  answerButton: document.querySelector("#answerButton"),
  wikiButton: document.querySelector("#wikiButton"),
  fakeSmokeButton: document.querySelector("#fakeSmokeButton"),
  githubSmokeButton: document.querySelector("#githubSmokeButton"),
  targetSyncButton: document.querySelector("#targetSyncButton"),
  refreshButton: document.querySelector("#refreshButton"),
  downloadMarkdownButton: document.querySelector("#downloadMarkdownButton"),
  downloadJsonButton: document.querySelector("#downloadJsonButton"),
  resultMeta: document.querySelector("#resultMeta"),
  answerPane: document.querySelector("#answerPane"),
  markdownPane: document.querySelector("#markdownPane"),
  jsonPane: document.querySelector("#jsonPane"),
  citationsList: document.querySelector("#citationsList"),
  backlinksList: document.querySelector("#backlinksList"),
  chunksList: document.querySelector("#chunksList"),
  sourcesList: document.querySelector("#sourcesList"),
  syncProgress: document.querySelector("#syncProgress"),
  syncProgressLabel: document.querySelector("#syncProgressLabel"),
  syncProgressPercent: document.querySelector("#syncProgressPercent"),
  syncProgressBar: document.querySelector("#syncProgressBar"),
  syncProgressDetail: document.querySelector("#syncProgressDetail"),
};

function init() {
  bindEvents();
  refreshHealth();
  refreshSources();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}

function bindEvents() {
  document.querySelector("#queryForm").addEventListener("submit", (event) => {
    event.preventDefault();
  });

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => setActiveTab(tab.dataset.tab));
    tab.addEventListener("keydown", (event) => handleTabKeydown(event));
  });

  elements.answerButton.addEventListener("click", () => runAnswer());
  elements.wikiButton.addEventListener("click", () => runWiki());
  elements.fakeSmokeButton.addEventListener("click", () => runFakeSmoke());
  elements.githubSmokeButton.addEventListener("click", () => runGithubSmoke());
  elements.targetSourceTypeSelect.addEventListener("change", () => updateTargetPlaceholder());
  elements.targetSyncButton.addEventListener("click", () => runTargetSync());
  elements.sourcesList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-sync-source-id]");
    if (!button) {
      return;
    }
    runSourceSync(button.dataset.syncSourceId);
  });
  elements.refreshButton.addEventListener("click", () => {
    refreshHealth();
    refreshSources();
  });
  elements.downloadMarkdownButton.addEventListener("click", () => downloadMarkdown());
  elements.downloadJsonButton.addEventListener("click", () => downloadJson());
  updateTargetPlaceholder();
}

function setActiveTab(tabName) {
  state.activeTab = tabName;
  document.querySelectorAll(".tab").forEach((tab) => {
    const isActive = tab.dataset.tab === tabName;
    tab.classList.toggle("active", isActive);
    tab.setAttribute("aria-selected", String(isActive));
    tab.tabIndex = isActive ? 0 : -1;
  });
  document.querySelectorAll(".tab-pane").forEach((pane) => {
    const isActive = pane.id === `${tabName}Pane`;
    pane.classList.toggle("active", isActive);
    pane.hidden = !isActive;
    pane.setAttribute("aria-hidden", String(!isActive));
  });
}

async function refreshHealth() {
  try {
    const health = await requestJson("/api/health");
    const label = health.status || health.state || health.ok || "ready";
    setHealth("ok", `API ${String(label)}`);
  } catch (error) {
    setHealth("error", "API unavailable");
    setStatus(error.message);
  }
}

async function refreshSources() {
  try {
    const payload = sanitizePayload(await requestJson("/api/sources"));
    renderSources(normalizeArray(payload.sources || payload.items || payload.data || payload));
  } catch (error) {
    renderSources([]);
    elements.sourcesList.textContent = redactSensitiveString(error.message);
    elements.sourcesList.className = "list empty";
  }
}

async function runAnswer() {
  const question = elements.questionInput.value.trim();
  if (!question) {
    showClientError("Enter a question before running an answer request.");
    return;
  }

  const body = {
    question,
    ...buildRequestOptions(),
    top_k: readTopK(),
  };
  const isCodexMode = elements.answerModeSelect.value === "codex";
  const url = isCodexMode ? "/api/answer/codex" : "/api/answer";
  const kind = isCodexMode ? "codex answer" : "answer";

  clearInactiveSyncProgress();
  await runAction(kind, url, body);
}

async function runWiki() {
  const topic = readTopic();
  if (!topic) {
    showClientError("Enter a wiki topic before calling /api/wiki/generate.");
    return;
  }

  const body = {
    topic,
    ...buildRequestOptions(),
    top_k: readTopK(8),
  };

  clearInactiveSyncProgress();
  await runAction("wiki", "/api/wiki/generate", body);
}

async function runFakeSmoke() {
  const topic = readTopic();
  const body = topic ? { topic } : {};
  clearInactiveSyncProgress();
  await runAction("fake smoke", "/api/smoke/fake", body);
}

async function runGithubSmoke() {
  const topic = readTopic();
  const repository = elements.githubRepositoryInput.value.trim();
  const body = {
    ...(topic ? { topic } : {}),
    ...(repository ? { github_repository: repository } : {}),
    require_generated: elements.requireGeneratedInput.checked,
  };

  clearInactiveSyncProgress();
  await runAction("github smoke", "/api/smoke/github", body);
}

async function runTargetSync() {
  const sourceType = elements.targetSourceTypeSelect.value;
  const target = elements.targetSyncInput.value.trim();
  if (!target) {
    stopSyncPolling();
    elements.syncProgress.hidden = true;
    showClientError("Enter a target URL or id before calling /api/targets/sync.");
    return;
  }
  await runSyncAction(
    `${sourceType} target sync`,
    "/api/targets/sync",
    { source_type: sourceType, target },
    sourceIdForTargetType(sourceType),
  );
}

async function runSourceSync(sourceId) {
  if (!sourceId) {
    return;
  }
  await runSyncAction(
    `sync ${sourceId}`,
    `/api/sources/${encodeURIComponent(sourceId)}/sync`,
    {},
    sourceId,
  );
}

async function runSyncAction(kind, url, body, sourceId) {
  beginSyncProgress(sourceId, `Starting ${kind}`);
  startSyncPolling(sourceId);
  const payload = await runAction(kind, url, body);
  const job = syncJobFromPayload(payload);
  state.syncAwaitingResponse = false;
  if (!payload || !actionSucceeded(payload)) {
    stopSyncPolling();
    renderSyncRequestStopped(sourceId, payload, "Sync request failed");
    await refreshSources();
    return;
  }
  if (job) {
    state.activeSyncJobId = job.job_id || state.activeSyncJobId;
    renderSyncProgress(job, sourceId);
    if (!isTerminalSyncJob(job) && !state.syncPollTimer) {
      startSyncPolling(sourceId);
    }
  } else {
    stopSyncPolling();
    renderSyncRequestStopped(sourceId, payload, "No sync job started");
  }
  await refreshSources();
  if (!job || isTerminalSyncJob(job)) {
    stopSyncPolling();
  }
}

async function runAction(kind, url, body) {
  setBusy(true, `Calling ${url}`);

  try {
    const payload = await requestJson(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const safePayload = sanitizePayload(payload);
    renderResult(kind, safePayload);
    setStatus(actionStatusMessage(kind, safePayload));
    return safePayload;
  } catch (error) {
    showClientError(error.message);
    setStatus(`Failed ${kind}.`);
    return null;
  } finally {
    setBusy(false);
  }
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const safePayload = sanitizePayload(payload);
    const detail =
      typeof safePayload === "string"
        ? safePayload
        : safePayload.detail || safePayload.error || safePayload.message || JSON.stringify(safePayload, null, 2);
    throw new Error(`${response.status} ${response.statusText}: ${detail}`);
  }

  return payload;
}

function buildFilters() {
  const { sourceIds, sourceIdText } = readFilterValues();
  const filters = {};

  if (sourceIdText) {
    filters.source_id = sourceIdText;
  }
  if (sourceIds.length) {
    filters.source_ids = sourceIds;
  }

  return filters;
}

function buildRequestOptions() {
  const { sourceTypes, sourceIds } = readFilterValues();
  return {
    filters: buildFilters(),
    source_types: sourceTypes,
    source_ids: sourceIds,
  };
}

function readFilterValues() {
  const sourceTypes = [...document.querySelectorAll('input[name="sourceType"]:checked')].map(
    (input) => input.value,
  );
  const sourceIdText = elements.sourceIdInput.value.trim();
  const sourceIds = sourceIdText
    ? sourceIdText
        .split(/[,\n]/)
        .map((value) => value.trim())
        .filter(Boolean)
    : [];

  return { sourceTypes, sourceIds, sourceIdText };
}

function readTopK(fallback = 5) {
  const value = Number.parseInt(elements.topKInput.value, 10);
  if (Number.isNaN(value)) {
    return fallback;
  }
  return Math.min(Math.max(value, 1), 20);
}

function readTopic() {
  return elements.topicInput.value.trim();
}

function renderResult(kind, payload) {
  const safePayload = sanitizePayload(payload);
  const normalized = normalizeResult(safePayload);
  state.lastKind = kind;
  state.lastPayload = safePayload;
  state.lastMarkdown = normalized.markdown || normalized.answer || "";

  elements.resultMeta.textContent = `${kind} response received at ${new Date().toLocaleTimeString()}`;
  elements.answerPane.innerHTML = buildAnswerHtml(normalized, kind);
  elements.markdownPane.textContent = state.lastMarkdown || "";
  elements.jsonPane.textContent = JSON.stringify(safePayload, null, 2);
  renderList(elements.citationsList, normalized.citations, "citation");
  renderList(elements.backlinksList, normalized.backlinks, "backlink");
  renderList(elements.chunksList, normalized.usedChunks, "chunk");
  elements.downloadMarkdownButton.disabled = !state.lastMarkdown;
  elements.downloadJsonButton.disabled = !state.lastPayload;
  setActiveTab(kind.includes("wiki") ? "markdown" : "answer");
}

function normalizeResult(payload) {
  const root = payload.result || payload.data || payload;
  const answer = firstString(
    root.answer,
    root.response,
    root.summary,
    root.message,
    payload.message,
    buildStatusSummary(root),
  );
  const markdown = firstString(root.markdown, root.page_markdown, root.content, root.wiki_markdown);

  return {
    answer,
    markdown,
    citations: normalizeEvidenceArray(root.citations || root.sources || root.references || payload.citations),
    backlinks: normalizeEvidenceArray(root.backlinks || root.related_pages || payload.backlinks),
    usedChunks: normalizeEvidenceArray(
      root.used_chunks || root.usedChunks || root.chunks || root.evidence || payload.used_chunks,
    ),
    raw: root,
  };
}

function sanitizePayload(value) {
  if (Array.isArray(value)) {
    return value.map((item) => sanitizePayload(item));
  }
  if (typeof value === "string") {
    return redactSensitiveString(value);
  }
  if (!value || typeof value !== "object") {
    return value;
  }

  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [
      key,
      isSensitivePayloadKey(key) ? "redacted" : sanitizePayload(item),
    ]),
  );
}

function isSensitivePayloadKey(key) {
  const normalized = String(key || "")
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .toLowerCase();
  return /(^|[_-])(auth|authorization|bearer|cookie|credential|key|password|private_key|secret|session|token)([_-]|$)/i
    .test(normalized);
}

function redactSensitiveString(value) {
  return String(value)
    .replace(/(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}/gi, "$1 [REDACTED]")
    .replace(/sk-(?:proj-)?[A-Za-z0-9_-]{8,}/gi, "[REDACTED]")
    .replace(/gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+/gi, "[REDACTED]")
    .replace(/([?&](?:auth|authorization|key|password|secret|session|sig|signature|token)=)[^&#\s]+/gi, "$1[REDACTED]")
    .replace(/\b((?:access|api|auth|bearer|client|github|notion|openai|refresh|session|id)?[_-]?(?:key|password|secret|token))\s*[:=]\s*[^'"\s,;}]+/gi, "$1=[REDACTED]");
}

function actionSucceeded(payload) {
  const root = payload.result || payload.data || payload;
  const status = String(root.status || root.evidence_status || "").toLowerCase();
  const codexStatus = String(root.codex_status || "").toLowerCase();
  return !["error", "failed"].includes(status) &&
    !["failed", "missing_cli", "timeout"].includes(codexStatus);
}

function actionStatusMessage(kind, payload) {
  const root = payload && typeof payload === "object" ? payload.result || payload.data || payload : {};
  const status = String(root.status || "").toLowerCase();
  if (status === "already_running") {
    return "Sync already running for this source.";
  }
  if (status === "running") {
    return "Sync is running for this source.";
  }
  return actionSucceeded(payload) ? `Completed ${kind}.` : `Failed ${kind}.`;
}

function buildStatusSummary(root) {
  if (!root || typeof root !== "object") {
    return "";
  }

  const parts = [];
  if (root.mode) {
    parts.push(`mode=${root.mode}`);
  }
  if (root.status) {
    parts.push(`status=${root.status}`);
  }
  if (root.wiki_status) {
    parts.push(`wiki_status=${root.wiki_status}`);
  }
  if (root.reason) {
    parts.push(`reason=${root.reason}`);
  }
  if (root.error) {
    parts.push(`error=${root.error}`);
  }
  ["citations", "backlinks", "used_chunks"].forEach((key) => {
    if (typeof root[key] === "number") {
      parts.push(`${key}=${root[key]}`);
    }
  });
  return parts.join("\n");
}

function firstString(...values) {
  return values.find((value) => typeof value === "string" && value.trim()) || "";
}

function normalizeArray(value) {
  if (!value) {
    return [];
  }
  if (Array.isArray(value)) {
    return value;
  }
  if (typeof value === "object") {
    return Object.values(value);
  }
  return [value];
}

function normalizeEvidenceArray(value) {
  if (!value || typeof value === "number") {
    return [];
  }
  return normalizeArray(value);
}

function buildAnswerHtml(result, kind) {
  const text = result.answer || result.markdown || "Response did not include a text answer.";
  return `
    <div class="answer-block">
      <section>
        <h3>${escapeHtml(titleCase(kind))}</h3>
        <div class="answer-text">${escapeHtml(text)}</div>
      </section>
    </div>
  `;
}

function renderList(container, items, kind) {
  const normalizedItems = normalizeArray(items);
  if (!normalizedItems.length) {
    container.textContent = `No ${kind}s returned.`;
    container.className = "list empty";
    return;
  }

  container.className = "list";
  container.innerHTML = normalizedItems.map((item, index) => buildItemHtml(item, index, kind)).join("");
}

function renderSources(sources) {
  if (!sources.length) {
    elements.sourcesList.textContent = "No sources returned.";
    elements.sourcesList.className = "list empty";
    return;
  }
  elements.sourcesList.className = "list";
  elements.sourcesList.innerHTML = sources.map((source, index) => buildSourceHtml(source, index)).join("");
  updateSourceSyncButtons();
}

function buildSourceHtml(source, index) {
  if (typeof source !== "object" || source === null) {
    return buildItemHtml(source, index, "source");
  }

  const title = firstString(source.name, source.source_id, `Source ${index + 1}`);
  const meta = compactObject({
    source_id: source.source_id,
    type: source.source_type,
    enabled: source.enabled,
    status: source.sync_status,
    last_synced_at: source.last_synced_at,
  });
  const body = source.last_error
    ? String(source.last_error)
    : source.auth_ref
      ? "auth=configured"
      : "";
  const canSync = Boolean(source.source_id && source.enabled !== false);

  return `
    <article class="item source-item">
      <div class="source-item-main">
        <div>
          <div class="item-title">${escapeHtml(title)}</div>
          ${meta ? `<div class="item-meta">${escapeHtml(meta)}</div>` : ""}
          ${body ? `<div class="item-body">${escapeHtml(truncate(body, 180))}</div>` : ""}
        </div>
        <button
          type="button"
          class="secondary source-sync-button"
          data-sync-source-id="${escapeHtml(source.source_id || "")}"
          data-sync-enabled="${canSync ? "true" : "false"}"
          ${canSync ? "" : "disabled"}
        >
          Sync configured
        </button>
      </div>
    </article>
  `;
}

function buildItemHtml(item, index, kind) {
  if (typeof item !== "object" || item === null) {
    return `
      <article class="item">
        <div class="item-title">${escapeHtml(titleCase(kind))} ${index + 1}</div>
        <div class="item-body">${escapeHtml(String(item))}</div>
      </article>
    `;
  }

  const title = firstString(
    item.title,
    item.name,
    item.source_id,
    item.document_id,
    item.chunk_id,
    item.url,
    `${titleCase(kind)} ${index + 1}`,
  );
  const meta = compactObject({
    type: item.source_type || item.type,
    source_id: item.source_id,
    document_id: item.document_id,
    chunk_id: item.chunk_id,
    score: item.score,
    status: item.status,
  });
  const body = firstString(item.text, item.content, item.snippet, item.summary, item.url);

  return `
    <article class="item">
      <div class="item-title">${escapeHtml(title)}</div>
      ${meta ? `<div class="item-meta">${escapeHtml(meta)}</div>` : ""}
      ${body ? `<div class="item-body">${escapeHtml(truncate(body, 360))}</div>` : ""}
    </article>
  `;
}

function compactObject(values) {
  return Object.entries(values)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([key, value]) => `${key}=${value}`)
    .join("  ");
}

function truncate(value, maxLength) {
  const text = String(value);
  return text.length <= maxLength ? text : `${text.slice(0, maxLength - 1)}...`;
}

function showClientError(message) {
  state.lastPayload = null;
  state.lastMarkdown = "";
  elements.resultMeta.textContent = "Request failed.";
  elements.answerPane.innerHTML = `<div class="error-box">${escapeHtml(message)}</div>`;
  elements.markdownPane.textContent = "";
  elements.jsonPane.textContent = "";
  renderList(elements.citationsList, [], "citation");
  renderList(elements.backlinksList, [], "backlink");
  renderList(elements.chunksList, [], "chunk");
  elements.downloadMarkdownButton.disabled = true;
  elements.downloadJsonButton.disabled = true;
  setActiveTab("answer");
}

function setBusy(isBusy, message = "") {
  state.busy = isBusy;
  [
    elements.answerButton,
    elements.wikiButton,
    elements.fakeSmokeButton,
    elements.githubSmokeButton,
    elements.targetSyncButton,
    elements.refreshButton,
  ].forEach((button) => {
    button.disabled = isBusy;
  });
  updateSourceSyncButtons();

  if (message) {
    setStatus(message);
  }
}

function updateTargetPlaceholder() {
  const placeholders = {
    github: "github.com/eunhwa99 or owner/repo@main",
    notion: "https://www.notion.so/... page/database URL or id",
    web: "https://docs.example.com",
  };
  elements.targetSyncInput.placeholder =
    placeholders[elements.targetSourceTypeSelect.value] || placeholders.github;
}

function sourceIdForTargetType(sourceType) {
  return {
    github: "source_github",
    notion: "source_notion",
    web: "source_web",
  }[sourceType] || "";
}

function beginSyncProgress(sourceId, label) {
  state.activeSyncSourceId = sourceId;
  state.activeSyncJobId = "";
  state.syncAwaitingResponse = true;
  elements.syncProgress.hidden = false;
  elements.syncProgress.classList.add("indeterminate");
  elements.syncProgressLabel.textContent = label;
  elements.syncProgressPercent.textContent = "Starting";
  setSyncProgressValue(0, false);
  elements.syncProgressDetail.textContent = "Waiting for sync job...";
}

function startSyncPolling(sourceId) {
  stopSyncPolling();
  if (!sourceId) {
    return;
  }
  const token = state.syncPollToken + 1;
  state.syncPollToken = token;
  state.activeSyncSourceId = sourceId;
  state.syncPollTimer = window.setInterval(() => {
    refreshSyncStatus(sourceId, token);
  }, 1000);
  refreshSyncStatus(sourceId, token);
}

function stopSyncPolling() {
  state.syncPollToken += 1;
  if (state.syncPollTimer) {
    window.clearInterval(state.syncPollTimer);
    state.syncPollTimer = null;
  }
}

async function refreshSyncStatus(sourceId, token = state.syncPollToken) {
  if (!sourceId) {
    return;
  }
  try {
    const payload = sanitizePayload(
      await requestJson(`/api/sources/${encodeURIComponent(sourceId)}/sync-status`),
    );
    if (token !== state.syncPollToken || sourceId !== state.activeSyncSourceId) {
      return;
    }
    if (String(payload.status || "").toLowerCase() === "error") {
      if (!state.activeSyncJobId) {
        elements.syncProgress.hidden = false;
        elements.syncProgress.classList.remove("indeterminate");
        elements.syncProgressLabel.textContent = "Sync status unavailable";
        elements.syncProgressPercent.textContent = "Error";
        elements.syncProgressDetail.textContent =
          payload.message || "Unable to load sync status.";
      }
      return;
    }
    const job = payload.latest_job || null;
    if (!shouldRenderSyncJob(job)) {
      return;
    }
    renderSyncProgress(job, sourceId);
    if (isTerminalSyncJob(job)) {
      stopSyncPolling();
      refreshSources();
    }
  } catch (error) {
    if (token !== state.syncPollToken || sourceId !== state.activeSyncSourceId) {
      return;
    }
    elements.syncProgress.hidden = false;
    elements.syncProgress.classList.remove("indeterminate");
    elements.syncProgressLabel.textContent = "Sync status unavailable";
    elements.syncProgressPercent.textContent = "Error";
    elements.syncProgressDetail.textContent = error.message;
  }
}

function renderSyncProgress(job, sourceId) {
  if (!job) {
    elements.syncProgress.hidden = false;
    elements.syncProgress.classList.add("indeterminate");
    elements.syncProgressLabel.textContent = `Waiting for ${sourceId}`;
    elements.syncProgressPercent.textContent = "Starting";
    setSyncProgressValue(0, false);
    elements.syncProgressDetail.textContent = "No job is visible yet.";
    return;
  }

  const status = String(job.status || "").toLowerCase();
  const total = Number(job.total_documents || 0);
  const processed = Number(job.processed_documents || 0);
  const skipped = Number(job.skipped_documents || 0);
  const indexed = Number(job.indexed_chunks || 0);
  const completed = processed + skipped;
  const hasTotal = total > 0;
  const percent = hasTotal ? Math.min(Math.round((completed / total) * 100), 100) : 0;

  elements.syncProgress.hidden = false;
  elements.syncProgress.classList.toggle("indeterminate", status === "running" && !hasTotal);
  elements.syncProgressLabel.textContent = `${sourceId} ${status || "sync"}`;
  elements.syncProgressPercent.textContent = hasTotal ? `${percent}%` : titleCase(status || "running");
  setSyncProgressValue(percent, hasTotal);
  elements.syncProgressDetail.textContent = hasTotal
    ? `${completed}/${total} documents completed (${processed} indexed or refreshed, ${skipped} skipped), ${indexed} chunks indexed`
    : `${indexed} chunks indexed. Discovering documents...`;
}

function renderSyncRequestStopped(sourceId, payload, fallbackLabel) {
  const root = payload && typeof payload === "object" ? payload.result || payload.data || payload : {};
  const status = String(root.status || "error");
  const message = firstString(root.message, root.reason, fallbackLabel);
  elements.syncProgress.hidden = false;
  elements.syncProgress.classList.remove("indeterminate");
  elements.syncProgressLabel.textContent = `${sourceId || "sync"} ${status}`;
  elements.syncProgressPercent.textContent = titleCase(status);
  setSyncProgressValue(0, true);
  elements.syncProgressDetail.textContent = message;
}

function clearInactiveSyncProgress() {
  if (state.syncAwaitingResponse) {
    return;
  }
  const label = elements.syncProgressLabel.textContent.toLowerCase();
  const percent = elements.syncProgressPercent.textContent.toLowerCase();
  const hasTerminalState = /error|failed|succeeded|skipped|cancel/.test(`${label} ${percent}`);
  if (state.syncPollTimer && !hasTerminalState) {
    return;
  }
  if (hasTerminalState) {
    stopSyncPolling();
  }
  elements.syncProgress.hidden = true;
}

function setSyncProgressValue(percent, hasValue) {
  const boundedPercent = Math.min(Math.max(Number(percent) || 0, 0), 100);
  const progressTrack = document.querySelector(".progress-track");
  elements.syncProgressBar.style.width = hasValue ? `${boundedPercent}%` : "0%";
  if (!progressTrack) {
    return;
  }
  if (hasValue) {
    progressTrack.setAttribute("aria-valuenow", String(boundedPercent));
  } else {
    progressTrack.removeAttribute("aria-valuenow");
  }
}

function syncJobFromPayload(payload) {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const root = payload.result || payload.data || payload;
  if (root.job_id && root.status) {
    return root;
  }
  return root.job || root.latest_job || null;
}

function shouldRenderSyncJob(job) {
  if (!job || typeof job !== "object") {
    return !state.syncAwaitingResponse && !state.activeSyncJobId;
  }
  const jobId = job.job_id || "";
  if (state.syncAwaitingResponse) {
    return false;
  }
  if (state.activeSyncJobId) {
    return !jobId || jobId === state.activeSyncJobId;
  }
  return !isTerminalSyncJob(job);
}

function isTerminalSyncJob(job) {
  if (!job || typeof job !== "object") {
    return false;
  }
  const status = String(job.status || "").toLowerCase();
  return ["succeeded", "failed", "cancelled", "canceled", "skipped"].includes(status);
}

function updateSourceSyncButtons() {
  document.querySelectorAll("[data-sync-source-id]").forEach((button) => {
    button.disabled = state.busy || button.dataset.syncEnabled === "false";
  });
}

function setHealth(level, message) {
  elements.healthBadge.textContent = message;
  elements.healthBadge.className = `badge badge-${level}`;
}

function setStatus(message) {
  elements.statusText.textContent = message;
}

function downloadMarkdown() {
  if (!state.lastMarkdown) {
    return;
  }
  downloadText(`${downloadBaseName()}.md`, state.lastMarkdown, "text/markdown");
}

function downloadJson() {
  if (!state.lastPayload) {
    return;
  }
  downloadText(
    `${downloadBaseName()}.json`,
    JSON.stringify(state.lastPayload, null, 2),
    "application/json",
  );
}

function downloadBaseName() {
  const label = (state.lastKind || "contextwiki-result").replace(/\s+/g, "-");
  return `${label}-${new Date().toISOString().replace(/[:.]/g, "-")}`;
}

function downloadText(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function handleTabKeydown(event) {
  const tabs = [...document.querySelectorAll(".tab")];
  const index = tabs.indexOf(event.currentTarget);
  const keyActions = {
    ArrowRight: () => tabs[(index + 1) % tabs.length],
    ArrowLeft: () => tabs[(index - 1 + tabs.length) % tabs.length],
    Home: () => tabs[0],
    End: () => tabs[tabs.length - 1],
  };
  const nextTab = keyActions[event.key] ? keyActions[event.key]() : null;
  if (!nextTab) {
    return;
  }
  event.preventDefault();
  setActiveTab(nextTab.dataset.tab);
  nextTab.focus();
}

function titleCase(value) {
  return String(value)
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

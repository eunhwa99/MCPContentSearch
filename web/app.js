const state = {
  activeTab: "answer",
  lastPayload: null,
  lastKind: "",
  lastMarkdown: "",
};

const elements = {
  healthBadge: document.querySelector("#healthBadge"),
  statusText: document.querySelector("#statusText"),
  questionInput: document.querySelector("#questionInput"),
  topicInput: document.querySelector("#topicInput"),
  sourceIdInput: document.querySelector("#sourceIdInput"),
  topKInput: document.querySelector("#topKInput"),
  githubRepositoryInput: document.querySelector("#githubRepositoryInput"),
  requireGeneratedInput: document.querySelector("#requireGeneratedInput"),
  answerButton: document.querySelector("#answerButton"),
  wikiButton: document.querySelector("#wikiButton"),
  fakeSmokeButton: document.querySelector("#fakeSmokeButton"),
  githubSmokeButton: document.querySelector("#githubSmokeButton"),
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
};

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  refreshHealth();
  refreshSources();
});

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
  elements.refreshButton.addEventListener("click", () => {
    refreshHealth();
    refreshSources();
  });
  elements.downloadMarkdownButton.addEventListener("click", () => downloadMarkdown());
  elements.downloadJsonButton.addEventListener("click", () => downloadJson());
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
    const payload = await requestJson("/api/sources");
    renderSources(normalizeArray(payload.sources || payload.items || payload.data || payload));
  } catch (error) {
    renderSources([]);
    elements.sourcesList.textContent = error.message;
    elements.sourcesList.className = "list empty";
  }
}

async function runAnswer() {
  const question = elements.questionInput.value.trim();
  if (!question) {
    showClientError("Enter a question before calling /api/answer.");
    return;
  }

  const body = {
    question,
    ...buildRequestOptions(),
    top_k: readTopK(),
  };

  await runAction("answer", "/api/answer", body);
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

  await runAction("wiki", "/api/wiki/generate", body);
}

async function runFakeSmoke() {
  const topic = readTopic();
  const body = topic ? { topic } : {};
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

  await runAction("github smoke", "/api/smoke/github", body);
}

async function runAction(kind, url, body) {
  setBusy(true, `Calling ${url}`);

  try {
    const payload = await requestJson(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    renderResult(kind, payload);
    setStatus(actionSucceeded(payload) ? `Completed ${kind}.` : `Failed ${kind}.`);
  } catch (error) {
    showClientError(error.message);
    setStatus(`Failed ${kind}.`);
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
    const detail =
      typeof payload === "string"
        ? payload
        : payload.detail || payload.error || payload.message || JSON.stringify(payload, null, 2);
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
  const normalized = normalizeResult(payload);
  state.lastKind = kind;
  state.lastPayload = payload;
  state.lastMarkdown = normalized.markdown || normalized.answer || JSON.stringify(payload, null, 2);

  elements.resultMeta.textContent = `${kind} response received at ${new Date().toLocaleTimeString()}`;
  elements.answerPane.innerHTML = buildAnswerHtml(normalized, kind);
  elements.markdownPane.textContent = state.lastMarkdown || "";
  elements.jsonPane.textContent = JSON.stringify(payload, null, 2);
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

function actionSucceeded(payload) {
  const root = payload.result || payload.data || payload;
  const status = String(root.status || root.evidence_status || "").toLowerCase();
  return !["error", "failed"].includes(status);
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
  elements.sourcesList.innerHTML = sources.map((source, index) => buildItemHtml(source, index, "source")).join("");
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
  [
    elements.answerButton,
    elements.wikiButton,
    elements.fakeSmokeButton,
    elements.githubSmokeButton,
    elements.refreshButton,
  ].forEach((button) => {
    button.disabled = isBusy;
  });

  if (message) {
    setStatus(message);
  }
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
  const nextTab = keyActions[event.key]?.();
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

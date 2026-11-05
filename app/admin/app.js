(() => {
  "use strict";

  const TOKEN_STORAGE_KEY = "smartreply_access_token";
  const API = Object.freeze({
    access: "/api/v1/admin/access",
    unlock: "/api/v1/admin/unlock",
    lock: "/api/v1/admin/lock",
    me: "/api/v1/admin/me",
    overview: "/api/v1/admin/overview",
    companies: "/api/v1/admin/companies",
    incidents: "/api/v1/admin/incidents",
  });
  const VIEW_TITLES = Object.freeze({
    overview: "Vue d’ensemble",
    companies: "Entreprises",
    incidents: "Incidents",
  });
  const STATUS_LABELS = Object.freeze({
    pending: "En attente",
    validated: "Validée",
    ready: "Prête",
    incomplete: "À compléter",
    connected: "Connecté",
    revoked: "Révoqué",
    missing: "Absent",
    configured: "Configuré",
    critical: "Critique",
    warning: "Attention",
  });
  const TONE_LABELS = Object.freeze({
    professional: "Professionnel",
    friendly: "Convivial",
    direct: "Direct",
    warm: "Chaleureux",
  });

  const state = {
    currentView: "overview",
    companiesPage: 1,
    companiesTotalPages: 0,
    incidentsPage: 1,
    incidentsTotalPages: 0,
    currentCompanyId: null,
    adminEmail: null,
  };

  class ApiError extends Error {
    constructor(status, message) {
      super(message);
      this.status = status;
    }
  }

  function element(id) {
    return document.getElementById(id);
  }

  function setText(id, value) {
    const target = element(id);
    if (target) {
      target.textContent = value === null || value === undefined ? "—" : String(value);
    }
  }

  function setHidden(id, hidden) {
    const target = element(id);
    if (target) {
      target.hidden = hidden;
    }
  }

  function clearElement(target) {
    target.replaceChildren();
  }

  function getToken() {
    try {
      return window.localStorage.getItem(TOKEN_STORAGE_KEY);
    } catch (_error) {
      return null;
    }
  }

  function clearToken() {
    try {
      window.localStorage.removeItem(TOKEN_STORAGE_KEY);
    } catch (_error) {
      // L'interface reste fermée même si le stockage est indisponible.
    }
  }

  async function apiRequest(path, options = {}) {
    const token = getToken();
    if (!token) {
      throw new ApiError(401, "Session manquante");
    }
    const headers = { Authorization: `Bearer ${token}` };
    if (options.body) {
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(path, {
      method: options.method || "GET",
      body: options.body || null,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = null;
    }
    if (!response.ok) {
      const message = payload && typeof payload.detail === "string"
        ? payload.detail
        : "Requête administrateur impossible";
      throw new ApiError(response.status, message);
    }
    return payload;
  }

  function formatDate(value, includeTime = true) {
    if (!value) {
      return "Jamais";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "Indisponible";
    }
    const options = includeTime
      ? { dateStyle: "medium", timeStyle: "short" }
      : { dateStyle: "medium" };
    return new Intl.DateTimeFormat("fr-FR", options).format(date);
  }

  function statusTone(status) {
    if (["validated", "ready", "connected", "configured"].includes(status)) {
      return "success";
    }
    if (["revoked", "critical"].includes(status)) {
      return "danger";
    }
    if (["pending", "incomplete", "warning"].includes(status)) {
      return "warning";
    }
    return "neutral";
  }

  function statusBadge(status, explicitLabel = null) {
    const badge = document.createElement("span");
    badge.className = `status-badge ${statusTone(status)}`;
    badge.textContent = explicitLabel || STATUS_LABELS[status] || status || "—";
    return badge;
  }

  function showGate(kind) {
    setHidden("admin-loading", true);
    setHidden("admin-code-gate", true);
    setHidden("admin-app", true);
    setHidden("admin-access-message", false);
    if (kind === "forbidden") {
      setText("gate-title", "Accès administrateur refusé");
      setText(
        "gate-description",
        "Ce compte SmartReply n’est pas autorisé à ouvrir le centre de pilotage.",
      );
      setText("gate-icon", "×");
    } else if (kind === "unavailable") {
      setText("gate-title", "Administration indisponible");
      setText(
        "gate-description",
        "La configuration administrateur doit être vérifiée côté serveur.",
      );
      setText("gate-icon", "!");
    } else {
      setText("gate-title", "Connexion requise");
      setText(
        "gate-description",
        "Connectez-vous d’abord à votre compte SmartReply autorisé.",
      );
      setText("gate-icon", "→");
    }
  }

  function showCodeGate(email = null) {
    if (email) {
      state.adminEmail = email;
    }
    setHidden("admin-loading", true);
    setHidden("admin-access-message", true);
    setHidden("admin-app", true);
    setHidden("admin-code-gate", false);
    setHidden("admin-code-error", true);
    setText("admin-code-email", state.adminEmail || "compte autorisé");
    element("admin-access-code").value = "";
    window.setTimeout(() => element("admin-access-code").focus(), 0);
  }

  function openAdmin(identity) {
    state.adminEmail = identity.email;
    setText("sidebar-admin-email", identity.email);
    setHidden("admin-loading", true);
    setHidden("admin-access-message", true);
    setHidden("admin-code-gate", true);
    setHidden("admin-app", false);
    const requestedView = window.location.hash.replace("#", "");
    navigate(requestedView || "overview");
  }

  async function unlockAdmin(event) {
    event.preventDefault();
    const input = element("admin-access-code");
    const button = element("admin-code-submit");
    const errorMessage = element("admin-code-error");
    setHidden("admin-code-error", true);
    button.disabled = true;
    button.textContent = "Vérification…";
    try {
      await apiRequest(API.unlock, {
        method: "POST",
        body: JSON.stringify({ code: input.value }),
      });
      input.value = "";
      const identity = await apiRequest(API.me);
      openAdmin(identity);
    } catch (error) {
      input.value = "";
      if (error instanceof ApiError && error.status === 401) {
        clearToken();
        showGate("login");
        return;
      }
      if (error instanceof ApiError && error.status === 429) {
        errorMessage.textContent = "Trop de tentatives. Réessayez après le délai indiqué.";
      } else if (error instanceof ApiError && error.status === 503) {
        errorMessage.textContent = "Le code administrateur n’est pas configuré correctement côté serveur.";
      } else if (error instanceof ApiError && error.message === "Accès administrateur refusé") {
        showGate("forbidden");
        return;
      } else {
        errorMessage.textContent = "Code administrateur incorrect.";
      }
      setHidden("admin-code-error", false);
      input.focus();
    } finally {
      button.disabled = false;
      button.textContent = "Déverrouiller l’administration";
    }
  }

  function closeMobileMenu() {
    const app = element("admin-app");
    const button = element("admin-menu-button");
    app.classList.remove("menu-open");
    button.setAttribute("aria-expanded", "false");
  }

  function navigate(view) {
    const normalized = Object.hasOwn(VIEW_TITLES, view) ? view : "overview";
    state.currentView = normalized;
    document.querySelectorAll(".admin-view").forEach((section) => {
      section.hidden = section.id !== `${normalized}-view`;
    });
    document.querySelectorAll(".admin-nav-item").forEach((button) => {
      const active = button.dataset.view === normalized;
      button.classList.toggle("active", active);
      if (active) {
        button.setAttribute("aria-current", "page");
      } else {
        button.removeAttribute("aria-current");
      }
    });
    setText("current-view-title", VIEW_TITLES[normalized]);
    closeMobileMenu();
    if (window.location.hash !== `#${normalized}`) {
      window.history.replaceState(null, "", `#${normalized}`);
    }
    if (normalized === "overview") {
      loadOverview();
    } else if (normalized === "companies") {
      showCompanyList();
      loadCompanies();
    } else {
      loadIncidents();
    }
  }

  async function loadOverview() {
    setHidden("overview-error", true);
    element("refresh-overview").disabled = true;
    try {
      const response = await apiRequest(API.overview);
      const metrics = response.metrics;
      setText("metric-companies", metrics.companies);
      setText("metric-users", metrics.users);
      setText("metric-validated", metrics.validated_companies);
      setText("metric-pending", metrics.pending_companies);
      setText("metric-ready", metrics.ready_companies);
      setText("metric-incomplete", metrics.incomplete_companies);
      setText("metric-incidents", metrics.active_incidents);
      setText("metric-processed", metrics.processed_emails);
      setText("metric-failed", metrics.failed_emails);
      setText("metric-drafts", metrics.pending_drafts);
      setText("metric-telegram", metrics.telegram_connected);
      setText("metric-gmail-issues", metrics.gmail_issues);
      setText("metric-drive-issues", metrics.drive_issues);
      setText("sidebar-incident-count", metrics.active_incidents);
      setText(
        "last-processing-badge",
        response.last_processing_at
          ? `Dernier traitement ${formatDate(response.last_processing_at)}`
          : "Aucun traitement connu",
      );
      setText(
        "overview-generated-at",
        `Actualisé le ${formatDate(response.generated_at)} · Données Neon, sans lecture des contenus.`,
      );
    } catch (error) {
      if (handleAuthorizationError(error)) {
        return;
      }
      setHidden("overview-error", false);
    } finally {
      element("refresh-overview").disabled = false;
    }
  }

  function companyFiltersQuery() {
    const params = new URLSearchParams({
      page: String(state.companiesPage),
      page_size: "25",
    });
    const values = {
      search: element("company-search").value.trim(),
      validation_status: element("company-validation-filter").value,
      configuration_status: element("company-configuration-filter").value,
      telegram_connected: element("company-telegram-filter").value,
    };
    Object.entries(values).forEach(([key, value]) => {
      if (value) {
        params.set(key, value);
      }
    });
    return params;
  }

  function createCompanyRow(company) {
    const row = document.createElement("tr");

    const identityCell = document.createElement("td");
    const identity = document.createElement("div");
    identity.className = "table-identity";
    const name = document.createElement("strong");
    name.textContent = company.name;
    const metadata = document.createElement("small");
    metadata.textContent = `${company.owner_email} · ID ${company.company_id}`;
    identity.append(name, metadata);
    identityCell.append(identity);

    const validationCell = document.createElement("td");
    validationCell.append(statusBadge(company.validation_status));
    const configurationCell = document.createElement("td");
    configurationCell.append(statusBadge(company.configuration_status));

    const integrationsCell = document.createElement("td");
    const integrations = document.createElement("div");
    integrations.className = "integration-summary";
    const integrationValues = [
      ["Gmail", company.gmail_status],
      ["Sheet", company.sheet_status],
      ["Telegram", company.telegram_connected ? "connected" : "missing"],
    ];
    integrationValues.forEach(([label, status]) => {
      const pill = document.createElement("span");
      pill.className = `integration-pill ${status}`;
      pill.textContent = `${label} · ${STATUS_LABELS[status] || status}`;
      integrations.append(pill);
    });
    integrationsCell.append(integrations);

    const processedCell = document.createElement("td");
    processedCell.textContent = formatDate(company.pipeline_last_processed_at);

    const actionCell = document.createElement("td");
    const action = document.createElement("button");
    action.type = "button";
    action.className = "button button-secondary button-small table-action";
    action.textContent = "Voir";
    action.addEventListener("click", () => openCompanyDetail(company.company_id));
    actionCell.append(action);

    row.append(
      identityCell,
      validationCell,
      configurationCell,
      integrationsCell,
      processedCell,
      actionCell,
    );
    return row;
  }

  function showCompanyLoading() {
    setHidden("companies-loading", false);
    setHidden("companies-error", true);
    setHidden("companies-empty", true);
    setHidden("companies-table-card", true);
  }

  async function loadCompanies() {
    showCompanyLoading();
    try {
      const response = await apiRequest(`${API.companies}?${companyFiltersQuery()}`);
      state.companiesTotalPages = response.pagination.total_pages;
      const body = element("companies-table-body");
      clearElement(body);
      response.items.forEach((company) => body.append(createCompanyRow(company)));
      setHidden("companies-loading", true);
      if (response.items.length === 0) {
        setHidden("companies-empty", false);
        return;
      }
      setHidden("companies-table-card", false);
      setText(
        "companies-pagination-summary",
        `${response.pagination.total_items} entreprise${response.pagination.total_items > 1 ? "s" : ""}`,
      );
      setText(
        "companies-page-indicator",
        `Page ${response.pagination.page} sur ${response.pagination.total_pages}`,
      );
      element("companies-previous").disabled = response.pagination.page <= 1;
      element("companies-next").disabled = response.pagination.page >= response.pagination.total_pages;
    } catch (error) {
      setHidden("companies-loading", true);
      if (handleAuthorizationError(error)) {
        return;
      }
      setHidden("companies-error", false);
    }
  }

  function showCompanyList() {
    setHidden("companies-list-workspace", false);
    setHidden("company-detail-workspace", true);
    state.currentCompanyId = null;
  }

  async function openCompanyDetail(companyId) {
    state.currentCompanyId = companyId;
    setHidden("companies-list-workspace", true);
    setHidden("company-detail-workspace", false);
    setHidden("company-detail-loading", false);
    setHidden("company-detail-error", true);
    setHidden("company-detail-content", true);
    try {
      const company = await apiRequest(`${API.companies}/${companyId}`);
      renderCompanyDetail(company);
      setHidden("company-detail-loading", true);
      setHidden("company-detail-content", false);
    } catch (error) {
      setHidden("company-detail-loading", true);
      if (handleAuthorizationError(error)) {
        return;
      }
      setHidden("company-detail-error", false);
    }
  }

  function renderCompanyDetail(company) {
    setText("detail-company-name", company.name);
    setText("detail-company-sector", company.sector);
    setText("detail-company-id", company.company_id);
    setText("detail-owner-email", company.owner_email);
    setText("detail-created-at", formatDate(company.created_at));
    setText(
      "detail-sheet-owner",
      company.sheet_owner_mode === "client" ? "Drive du client" : "Compte plateforme historique",
    );
    setText("detail-gmail-status", STATUS_LABELS[company.gmail_status]);
    setText("detail-drive-status", STATUS_LABELS[company.drive_status]);
    setText("detail-sheet-status", STATUS_LABELS[company.sheet_status]);
    setText("detail-telegram-status", company.telegram_connected ? "Connecté" : "Non connecté");
    setText("detail-pipeline-activated", formatDate(company.pipeline_activated_at));
    setText("detail-last-processed", formatDate(company.pipeline_last_processed_at));
    setText("detail-processed", company.processed_emails);
    setText("detail-failed", company.failed_emails);
    setText("detail-pending-drafts", company.pending_drafts);
    setText(
      "detail-profile-completeness",
      `${company.profile.configured_fields}/${company.profile.total_fields}`,
    );
    setText("detail-response-tone", TONE_LABELS[company.profile.response_tone] || "—");
    setText("detail-updated-at", formatDate(company.updated_at));

    const statuses = element("detail-statuses");
    clearElement(statuses);
    statuses.append(
      statusBadge(company.validation_status),
      statusBadge(company.configuration_status),
    );
  }

  function incidentFiltersQuery() {
    const params = new URLSearchParams({
      page: String(state.incidentsPage),
      page_size: "25",
    });
    const severity = element("incident-severity-filter").value;
    const companyId = element("incident-company-filter").value.trim();
    if (severity) {
      params.set("severity", severity);
    }
    if (companyId) {
      params.set("company_id", companyId);
    }
    return params;
  }

  function createIncidentRow(incident) {
    const row = document.createElement("tr");
    const severity = document.createElement("td");
    severity.append(statusBadge(incident.severity));

    const incidentCell = document.createElement("td");
    const title = document.createElement("div");
    title.className = "incident-title-cell";
    const label = document.createElement("strong");
    label.textContent = incident.label;
    const reference = document.createElement("small");
    reference.textContent = incident.incident_ref;
    title.append(label, reference);
    incidentCell.append(title);

    const company = document.createElement("td");
    company.textContent = `${incident.company_name} · ID ${incident.company_id}`;
    const code = document.createElement("td");
    code.className = "code-value";
    code.textContent = incident.code || "—";
    const attempts = document.createElement("td");
    attempts.textContent = incident.attempts === null ? "—" : String(incident.attempts);
    const occurred = document.createElement("td");
    occurred.textContent = formatDate(incident.occurred_at);
    row.append(severity, incidentCell, company, code, attempts, occurred);
    return row;
  }

  function showIncidentLoading() {
    setHidden("incidents-loading", false);
    setHidden("incidents-error", true);
    setHidden("incidents-empty", true);
    setHidden("incidents-table-card", true);
  }

  async function loadIncidents() {
    showIncidentLoading();
    try {
      const response = await apiRequest(`${API.incidents}?${incidentFiltersQuery()}`);
      state.incidentsTotalPages = response.pagination.total_pages;
      const body = element("incidents-table-body");
      clearElement(body);
      response.items.forEach((incident) => body.append(createIncidentRow(incident)));
      setHidden("incidents-loading", true);
      if (response.items.length === 0) {
        setHidden("incidents-empty", false);
        return;
      }
      setHidden("incidents-table-card", false);
      setText(
        "incidents-pagination-summary",
        `${response.pagination.total_items} incident${response.pagination.total_items > 1 ? "s" : ""}`,
      );
      setText(
        "incidents-page-indicator",
        `Page ${response.pagination.page} sur ${response.pagination.total_pages}`,
      );
      element("incidents-previous").disabled = response.pagination.page <= 1;
      element("incidents-next").disabled = response.pagination.page >= response.pagination.total_pages;
    } catch (error) {
      setHidden("incidents-loading", true);
      if (handleAuthorizationError(error)) {
        return;
      }
      setHidden("incidents-error", false);
    }
  }

  async function copyCompanyId() {
    if (!state.currentCompanyId) {
      return;
    }
    const value = String(state.currentCompanyId);
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(value);
      } else {
        const fallback = element("clipboard-fallback-input");
        fallback.value = value;
        fallback.select();
        document.execCommand("copy");
      }
      const button = element("copy-company-id");
      button.textContent = "Identifiant copié";
      window.setTimeout(() => {
        button.textContent = "Copier l’identifiant";
      }, 1800);
    } catch (_error) {
      element("copy-company-id").textContent = `ID ${value}`;
    }
  }

  function handleAuthorizationError(error) {
    if (!(error instanceof ApiError)) {
      return false;
    }
    if (error.status === 401) {
      clearToken();
      showGate("login");
      return true;
    }
    if (error.status === 403) {
      showGate("forbidden");
      return true;
    }
    if (error.status === 423) {
      showCodeGate();
      return true;
    }
    return false;
  }

  function bindEvents() {
    document.querySelectorAll(".admin-nav-item").forEach((button) => {
      button.addEventListener("click", () => navigate(button.dataset.view));
    });
    document.querySelectorAll("[data-view-link]").forEach((link) => {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        navigate(link.dataset.viewLink);
      });
    });
    element("admin-menu-button").addEventListener("click", () => {
      const app = element("admin-app");
      const open = app.classList.toggle("menu-open");
      element("admin-menu-button").setAttribute("aria-expanded", String(open));
    });
    element("admin-overlay").addEventListener("click", closeMobileMenu);
    element("admin-code-form").addEventListener("submit", unlockAdmin);
    element("admin-logout").addEventListener("click", async () => {
      try {
        await apiRequest(API.lock, { method: "POST" });
      } catch (_error) {
        // Le JWT local est supprimé même si le verrouillage distant échoue.
      } finally {
        clearToken();
        window.location.assign("/dashboard/");
      }
    });
    element("refresh-overview").addEventListener("click", loadOverview);
    element("refresh-companies").addEventListener("click", loadCompanies);
    element("companies-retry").addEventListener("click", loadCompanies);
    element("company-filters").addEventListener("submit", (event) => {
      event.preventDefault();
      state.companiesPage = 1;
      loadCompanies();
    });
    element("companies-previous").addEventListener("click", () => {
      if (state.companiesPage > 1) {
        state.companiesPage -= 1;
        loadCompanies();
      }
    });
    element("companies-next").addEventListener("click", () => {
      if (state.companiesPage < state.companiesTotalPages) {
        state.companiesPage += 1;
        loadCompanies();
      }
    });
    element("company-detail-back").addEventListener("click", showCompanyList);
    element("company-detail-retry").addEventListener("click", () => {
      if (state.currentCompanyId) {
        openCompanyDetail(state.currentCompanyId);
      }
    });
    element("copy-company-id").addEventListener("click", copyCompanyId);
    element("refresh-incidents").addEventListener("click", loadIncidents);
    element("incidents-retry").addEventListener("click", loadIncidents);
    element("incident-filters").addEventListener("submit", (event) => {
      event.preventDefault();
      state.incidentsPage = 1;
      loadIncidents();
    });
    element("incidents-previous").addEventListener("click", () => {
      if (state.incidentsPage > 1) {
        state.incidentsPage -= 1;
        loadIncidents();
      }
    });
    element("incidents-next").addEventListener("click", () => {
      if (state.incidentsPage < state.incidentsTotalPages) {
        state.incidentsPage += 1;
        loadIncidents();
      }
    });
  }

  async function bootstrap() {
    bindEvents();
    if (!getToken()) {
      showGate("login");
      return;
    }
    try {
      const access = await apiRequest(API.access);
      state.adminEmail = access.email;
      if (!access.unlocked) {
        showCodeGate(access.email);
        return;
      }
      const identity = await apiRequest(API.me);
      openAdmin(identity);
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) {
        showGate("forbidden");
      } else if (error instanceof ApiError && error.status === 401) {
        clearToken();
        showGate("login");
      } else {
        showGate("unavailable");
      }
    }
  }

  bootstrap();
})();

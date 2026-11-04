(() => {
  "use strict";

  const API = Object.freeze({
    register: "/api/v1/auth/register",
    login: "/api/v1/auth/login",
    company: "/api/v1/companies/me",
    companies: "/api/v1/companies",
    companySettings: "/api/v1/companies/me/settings",
    companyValidationStatus: "/api/v1/companies/me/validation-status",
    gmailConnect: "/api/v1/gmail/connect",
    gmailStatus: "/api/v1/gmail/status",
    driveConnect: "/api/v1/drive/connect",
    driveStatus: "/api/v1/drive/status",
    driveDisconnect: "/api/v1/drive/connection",
    pipelineAccess: "/api/v1/companies/me/pipeline-access",
    telegramCode: "/api/v1/telegram/link-code",
    telegramStatus: "/api/v1/telegram/status",
    dashboardOverview: "/api/v1/dashboard/overview",
    recentEmails: "/api/v1/dashboard/recent-emails",
    dashboardEmails: "/api/v1/dashboard/emails",
  });

  const TOKEN_STORAGE_KEY = "smartreply_access_token";
  const TELEGRAM_POLL_INTERVAL_MS = 4000;
  const EMAILS_PAGE_SIZE = 25;
  const EMAILS_SEARCH_DEBOUNCE_MS = 350;
  const EMAIL_FILTER_VALUES = Object.freeze({
    category: Object.freeze([
      "Devis",
      "Support",
      "Facturation",
      "Partenariat",
      "Spam",
      "Newsletter",
      "Autre",
    ]),
    priority: Object.freeze(["Haute", "Moyenne", "Basse"]),
    status: Object.freeze(["Analysé", "Brouillon prêt", "Envoyé", "Ignoré"]),
  });
  let emailsRequestSequence = 0;
  let emailDetailRequestSequence = 0;
  let dashboardRequestSequence = 0;
  let integrationRequestSequence = 0;
  let emailsSearchDebounceId = null;
  const SCREEN_IDS = [
    "loading-screen",
    "auth-screen",
    "company-screen",
    "dashboard-shell",
  ];
  const ALL_INTEGRATIONS = Object.freeze(["gmail", "sheet", "telegram"]);
  const REQUIRED_INTEGRATIONS = Object.freeze(["gmail", "sheet"]);
  const SECTOR_OPTIONS = Object.freeze([
    "Conseil",
    "Services",
    "Commerce",
    "E-commerce",
    "Formation",
    "Marketing et communication",
    "Technologie",
    "Freelance",
    "Agence",
    "Immobilier",
  ]);
  const RESPONSE_TONES = Object.freeze(["professional", "friendly", "direct", "warm"]);
  const OTHER_SECTOR_VALUE = "__other__";
  const APP_ROUTES = Object.freeze(["dashboard", "emails", "integrations", "settings"]);
  const ROUTE_TITLES = Object.freeze({
    dashboard: "Tableau de bord",
    emails: "Emails",
    integrations: "Intégrations",
    settings: "Paramètres",
  });
  const EMAILS_VIEW_IDS = Object.freeze({
    workspace: "emails-workspace",
    search: "emails-search",
    category: "emails-category-filter",
    priority: "emails-priority-filter",
    status: "emails-status-filter",
    loading: "emails-loading-state",
    empty: "emails-empty-state",
    error: "emails-error-state",
    retry: "emails-retry",
    table: "emails-table-wrap",
    tableBody: "emails-table-body",
    pagination: "emails-pagination",
    paginationSummary: "emails-pagination-summary",
    pageIndicator: "emails-page-indicator",
    previousPage: "emails-previous-page",
    nextPage: "emails-next-page",
    detailWorkspace: "email-detail-workspace",
    detailBack: "email-detail-back",
    detailLoading: "email-detail-loading-state",
    detailNotFound: "email-detail-not-found-state",
    detailError: "email-detail-error-state",
    detailContent: "email-detail-content",
    detailTitle: "email-detail-title",
    detailSenderName: "email-detail-sender-name",
    detailSenderEmail: "email-detail-sender-email",
    detailSubject: "email-detail-subject",
    detailReceivedAt: "email-detail-received-at",
    detailSummary: "email-detail-summary",
    detailCategory: "email-detail-category",
    detailPriority: "email-detail-priority",
    detailStatus: "email-detail-status",
  });
  let emailsViewElements = null;

  function createEmailCriteria() {
    return {
      search: "",
      category: "",
      priority: "",
      status: "",
    };
  }

  function createEmailListState(criteria = createEmailCriteria()) {
    return {
      items: [],
      pagination: {
        page: 1,
        page_size: EMAILS_PAGE_SIZE,
        total_items: 0,
        total_pages: 0,
      },
      criteria: { ...criteria },
      loaded: false,
      loading: false,
    };
  }

  function createEmailDetailState(emailId = null) {
    return {
      emailId,
      item: null,
      loaded: false,
      loading: false,
      error: null,
    };
  }

  const state = {
    token: readStoredToken(),
    user: null,
    company: null,
    appReady: false,
    companyValidation: {
      status: null,
      error: false,
    },
    integrations: {
      gmail: false,
      sheet: false,
      telegram: false,
      gmailError: false,
      sheetError: false,
      sheetState: "not_created",
      sheetOwnership: "none",
      driveConnected: false,
      telegramError: false,
      telegramAvailable: null,
      statusesAvailable: false,
    },
    integrationSetup: {
      dismissed: false,
      missing: [],
    },
    telegramPollId: null,
    telegramCountdownId: null,
    telegramPollInFlight: false,
    telegramExpiresAt: null,
    dashboard: {
      overview: null,
      emails: null,
      loaded: false,
    },
    emailList: createEmailListState(),
    emailDetail: createEmailDetailState(),
  };

  class ApiError extends Error {
    constructor(status, message) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }

  const $ = (id) => document.getElementById(id);

  function cacheEmailsViewElements() {
    emailsViewElements = Object.freeze({
      workspace: $(EMAILS_VIEW_IDS.workspace),
      search: $(EMAILS_VIEW_IDS.search),
      category: $(EMAILS_VIEW_IDS.category),
      priority: $(EMAILS_VIEW_IDS.priority),
      status: $(EMAILS_VIEW_IDS.status),
      loading: $(EMAILS_VIEW_IDS.loading),
      empty: $(EMAILS_VIEW_IDS.empty),
      error: $(EMAILS_VIEW_IDS.error),
      retry: $(EMAILS_VIEW_IDS.retry),
      table: $(EMAILS_VIEW_IDS.table),
      tableBody: $(EMAILS_VIEW_IDS.tableBody),
      pagination: $(EMAILS_VIEW_IDS.pagination),
      paginationSummary: $(EMAILS_VIEW_IDS.paginationSummary),
      pageIndicator: $(EMAILS_VIEW_IDS.pageIndicator),
      previousPage: $(EMAILS_VIEW_IDS.previousPage),
      nextPage: $(EMAILS_VIEW_IDS.nextPage),
      detailWorkspace: $(EMAILS_VIEW_IDS.detailWorkspace),
      detailBack: $(EMAILS_VIEW_IDS.detailBack),
      detailLoading: $(EMAILS_VIEW_IDS.detailLoading),
      detailNotFound: $(EMAILS_VIEW_IDS.detailNotFound),
      detailError: $(EMAILS_VIEW_IDS.detailError),
      detailContent: $(EMAILS_VIEW_IDS.detailContent),
      detailTitle: $(EMAILS_VIEW_IDS.detailTitle),
      detailSenderName: $(EMAILS_VIEW_IDS.detailSenderName),
      detailSenderEmail: $(EMAILS_VIEW_IDS.detailSenderEmail),
      detailSubject: $(EMAILS_VIEW_IDS.detailSubject),
      detailReceivedAt: $(EMAILS_VIEW_IDS.detailReceivedAt),
      detailSummary: $(EMAILS_VIEW_IDS.detailSummary),
      detailCategory: $(EMAILS_VIEW_IDS.detailCategory),
      detailPriority: $(EMAILS_VIEW_IDS.detailPriority),
      detailStatus: $(EMAILS_VIEW_IDS.detailStatus),
    });
    return emailsViewElements;
  }

  function getEmailsViewElements() {
    return emailsViewElements || cacheEmailsViewElements();
  }

  const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
  const DONUT_PALETTES = Object.freeze({
    category: Object.freeze({
      devis: "donut-color-primary-900",
      support: "donut-color-telegram",
      facturation: "donut-color-purple",
      partenariat: "donut-color-orange",
      spam: "donut-color-danger",
      newsletter: "donut-color-warning",
      autre: "donut-color-muted",
      unclassified: "donut-color-subtle",
    }),
    priority: Object.freeze({
      haute: "donut-color-danger",
      moyenne: "donut-color-warning",
      basse: "donut-color-success",
      unclassified: "donut-color-subtle",
    }),
  });
  const DONUT_FALLBACK_COLORS = Object.freeze([
    "donut-color-primary-700",
    "donut-color-telegram",
    "donut-color-warning",
    "donut-color-danger",
  ]);

  function readStoredToken() {
    try {
      return window.localStorage.getItem(TOKEN_STORAGE_KEY);
    } catch (_) {
      return null;
    }
  }

  function storeToken(token) {
    try {
      window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
      return true;
    } catch (_) {
      return false;
    }
  }

  function removeStoredToken() {
    try {
      window.localStorage.removeItem(TOKEN_STORAGE_KEY);
    } catch (_) {
      // L'état mémoire est tout de même supprimé ci-dessous.
    }
  }

  function showScreen(screenId) {
    SCREEN_IDS.forEach((id) => {
      $(id).hidden = id !== screenId;
    });
  }

  function showAlert(element, message) {
    element.textContent = message;
    element.hidden = !message;
  }

  function syncOtherSectorField(select, field, input) {
    const isOther = select.value === OTHER_SECTOR_VALUE;
    field.hidden = !isOther;
    input.disabled = !isOther || select.disabled;
    input.required = isOther && !select.disabled;
  }

  function setSectorControlValue(select, field, input, sector) {
    if (SECTOR_OPTIONS.includes(sector)) {
      select.value = sector;
      input.value = "";
    } else if (sector) {
      select.value = OTHER_SECTOR_VALUE;
      input.value = sector;
    } else {
      select.value = "";
      input.value = "";
    }
    syncOtherSectorField(select, field, input);
  }

  function selectedSectorValue(select, input) {
    return select.value === OTHER_SECTOR_VALUE ? input.value.trim() : select.value;
  }

  function setBusy(button, busy, label = "Traitement…") {
    if (busy) {
      if (!button.dataset.idleHtml) {
        button.dataset.idleHtml = button.innerHTML;
      }
      button.disabled = true;
      button.classList.add("loading");
      button.innerHTML = `<span class="spinner" aria-hidden="true"></span><span>${label}</span>`;
      return;
    }
    button.disabled = false;
    button.classList.remove("loading");
    if (button.dataset.idleHtml) {
      button.innerHTML = button.dataset.idleHtml;
      delete button.dataset.idleHtml;
    }
  }

  function showToast(message, type = "success") {
    const region = $("toast-region");
    region.replaceChildren();
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.setAttribute("role", type === "error" ? "alert" : "status");
    toast.innerHTML = `<svg aria-hidden="true"><use href="#${type === "error" ? "icon-close" : "icon-check"}"></use></svg>`;
    const text = document.createElement("span");
    text.textContent = message;
    toast.append(text);
    region.append(toast);
    window.setTimeout(() => {
      if (toast.isConnected) toast.remove();
    }, 5000);
  }

  function detailToMessage(detail) {
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      const messages = detail
        .map((entry) => entry && (entry.msg || entry.message))
        .filter(Boolean);
      if (messages.length) return messages.join(" · ");
    }
    return "La requête n'a pas pu aboutir.";
  }

  async function readPayload(response) {
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      try {
        return await response.json();
      } catch (_) {
        return {};
      }
    }
    try {
      return { detail: await response.text() };
    } catch (_) {
      return {};
    }
  }

  async function apiRequest(path, options = {}) {
    const {
      method = "GET",
      body,
      authenticated = true,
    } = options;
    const headers = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (authenticated && state.token) {
      headers.Authorization = `Bearer ${state.token}`;
    }

    let response;
    try {
      response = await window.fetch(path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (_) {
      throw new ApiError(0, "Connexion au service impossible. Vérifiez votre réseau puis réessayez.");
    }

    const payload = await readPayload(response);
    if (!response.ok) {
      if (response.status === 401 && authenticated) {
        logout({ expired: true });
      }
      throw new ApiError(response.status, detailToMessage(payload.detail));
    }
    return payload;
  }

  function decodeJwt(token) {
    try {
      const segment = token.split(".")[1];
      if (!segment) return null;
      const normalized = segment.replace(/-/g, "+").replace(/_/g, "/");
      const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
      const binary = window.atob(padded);
      const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
      return JSON.parse(new TextDecoder().decode(bytes));
    } catch (_) {
      return null;
    }
  }

  function formatEmailPrefix(email) {
    const prefix = String(email || "utilisateur").split("@")[0];
    const words = prefix
      .replace(/([a-zà-ÿ])([A-Z])/g, "$1 $2")
      .split(/[._+\-\s]+/)
      .filter(Boolean)
      .slice(0, 3)
      .map((word) => word.charAt(0).toLocaleUpperCase("fr") + word.slice(1).toLocaleLowerCase("fr"));
    return words.join(" ") || "Utilisateur";
  }

  function initialsFromEmail(email) {
    const name = formatEmailPrefix(email);
    return name
      .split(" ")
      .slice(0, 2)
      .map((part) => part.charAt(0))
      .join("")
      .toLocaleUpperCase("fr");
  }

  function describeGoogleProblem(error) {
    if (error.status === 502) {
      return "Google n'a pas pu terminer cette opération. Vérifiez la configuration ou réessayez dans quelques instants.";
    }
    if (error.status === 503) {
      return "Le service Google est temporairement indisponible ou incomplet sur le serveur. Réessayez plus tard.";
    }
    return error.message;
  }

  function setSession(token) {
    const payload = decodeJwt(token);
    if (!payload || !payload.email || !payload.sub) {
      throw new Error("Le serveur a renvoyé une session invalide.");
    }
    if (!storeToken(token)) {
      throw new Error("Votre navigateur empêche l'enregistrement local de la session.");
    }
    state.token = token;
    state.user = {
      id: payload.sub,
      email: payload.email,
    };
    state.companyValidation = {
      status: null,
      error: false,
    };
    state.integrationSetup = {
      dismissed: false,
      missing: [],
    };
  }

  function resetEmailListState() {
    if (emailsSearchDebounceId !== null) {
      window.clearTimeout(emailsSearchDebounceId);
      emailsSearchDebounceId = null;
    }
    emailsRequestSequence += 1;
    emailDetailRequestSequence += 1;
    state.emailList = createEmailListState();
    state.emailDetail = createEmailDetailState();
    if (!emailsViewElements) return;

    emailsViewElements.search.value = "";
    emailsViewElements.category.value = "";
    emailsViewElements.priority.value = "";
    emailsViewElements.status.value = "";
    emailsViewElements.tableBody.replaceChildren();
    emailsViewElements.workspace.setAttribute("aria-busy", "false");
    emailsViewElements.loading.hidden = true;
    emailsViewElements.empty.hidden = false;
    emailsViewElements.error.hidden = true;
    emailsViewElements.table.hidden = true;
    emailsViewElements.pagination.hidden = true;
    emailsViewElements.paginationSummary.textContent = "0 email";
    emailsViewElements.pageIndicator.textContent = "Page 1 sur 1";
    emailsViewElements.previousPage.disabled = true;
    emailsViewElements.nextPage.disabled = true;
    emailsViewElements.retry.disabled = false;
    emailsViewElements.workspace.hidden = false;
    emailsViewElements.detailWorkspace.hidden = true;
    emailsViewElements.detailWorkspace.setAttribute("aria-busy", "false");
    emailsViewElements.detailLoading.hidden = true;
    emailsViewElements.detailNotFound.hidden = true;
    emailsViewElements.detailError.hidden = true;
    emailsViewElements.detailContent.hidden = true;
    emailsViewElements.detailTitle.textContent = "Email";
    [
      emailsViewElements.detailSenderName,
      emailsViewElements.detailSenderEmail,
      emailsViewElements.detailSubject,
      emailsViewElements.detailReceivedAt,
      emailsViewElements.detailSummary,
      emailsViewElements.detailCategory,
      emailsViewElements.detailPriority,
      emailsViewElements.detailStatus,
    ].forEach((element) => {
      element.textContent = "—";
    });
    [
      emailsViewElements.detailCategory,
      emailsViewElements.detailPriority,
      emailsViewElements.detailStatus,
    ].forEach((element) => {
      element.className = "data-badge neutral";
    });
  }

  function resetDashboardDomState() {
    if (!emailsViewElements) return;

    $("greeting-name").textContent = "Utilisateur";
    $("sidebar-user-name").textContent = "Utilisateur";
    $("sidebar-user-email").textContent = "—";
    $("user-avatar").textContent = "U";
    $("sidebar-company-name").textContent = "—";
    $("sidebar-company-sector").textContent = "—";
    $("topbar-company-name").textContent = "—";
    $("topbar-sector").textContent = "—";
    ["sidebar-validation-status", "topbar-validation-status"].forEach((id) => {
      const element = $(id);
      element.className = id === "topbar-validation-status"
        ? "status-pill neutral topbar-validation-status"
        : "status-pill neutral";
      element.textContent = "Vérification…";
    });
    $("settings-company-name").textContent = "—";
    $("settings-account-email").textContent = "—";
    const settingsForm = $("settings-form");
    if (settingsForm && typeof settingsForm.reset === "function") settingsForm.reset();
    setSectorControlValue(
      $("settings-sector"),
      $("settings-sector-other-field"),
      $("settings-sector-other"),
      "",
    );
    $("settings-sector").disabled = true;
    syncOtherSectorField(
      $("settings-sector"),
      $("settings-sector-other-field"),
      $("settings-sector-other"),
    );
    $("settings-sector-help").textContent = "Le secteur sera modifiable après vérification du statut de l'entreprise.";
    showAlert($("settings-error"), "");
    $("sheet-company-label").textContent = "Sheet de votre entreprise";
    $("open-sheet").setAttribute("href", "#");
    $("open-sheet").setAttribute("aria-disabled", "true");
    $("open-sheet").tabIndex = -1;

    $("dashboard-stats").setAttribute("aria-busy", "true");
    ["metric-analyzed", "metric-processed", "metric-ignored", "metric-pending"].forEach((id) => {
      $(id).textContent = "—";
    });
    ["volume-chart", "category-chart", "priority-chart"].forEach((id) => {
      $(id).replaceChildren();
    });
    $("recent-emails-body").replaceChildren();
    $("recent-emails-table-wrap").hidden = true;
    $("recent-emails-state").hidden = false;
    $("recent-emails-state").classList.remove("error");
    $("recent-emails-state").textContent = "Chargement des emails…";
    $("recent-activity-list").replaceChildren();
    $("recent-activity-list").hidden = true;
    $("recent-activity-state").hidden = false;
    $("recent-activity-state").classList.remove("error");
    $("recent-activity-state").textContent = "Chargement de l'activité…";
    $("analytics-history-note").hidden = true;
    $("dashboard-empty-state").hidden = true;
    showAlert($("dashboard-data-error"), "");

    $("dashboard-connected-count").textContent = "0/3";
    $("integrations-progress").textContent = "0/3";
    ["gmail-status", "sheet-status", "telegram-status"].forEach((id) => {
      const statusElement = $(id);
      statusElement.className = "status-pill neutral";
      statusElement.textContent = "Vérification…";
    });
    setIntegrationConfigurationHighlights([]);
    $("readiness-banner").classList.remove("ready");
    $("readiness-title").textContent = "Finalisez les services requis";
    $("readiness-text").textContent = "Connectez Gmail et Google Sheets pour rendre SmartReply disponible. Telegram est optionnel.";
    $("connect-gmail").disabled = false;
    $("connect-gmail").textContent = "Connecter Gmail";
    $("generate-telegram-code").disabled = false;
    $("generate-telegram-code").textContent = "Générer un code";
    $("telegram-code-panel").hidden = true;
    $("telegram-code").textContent = "SR-XXXXX";
    $("telegram-command").textContent = "/start SR-XXXXX";
    $("telegram-expiry").textContent = "Expiration dans 15:00";
    showAlert($("telegram-code-error"), "");
    showAlert($("integrations-error"), "");
  }

  function clearSession() {
    stopTelegramPolling();
    closeIntegrationSetupModal();
    removeStoredToken();
    state.token = null;
    state.user = null;
    state.company = null;
    state.appReady = false;
    state.companyValidation = {
      status: null,
      error: false,
    };
    state.integrations = {
      gmail: false,
      sheet: false,
      telegram: false,
      gmailError: false,
      sheetError: false,
      sheetState: "not_created",
      sheetOwnership: "none",
      driveConnected: false,
      telegramError: false,
      telegramAvailable: null,
      statusesAvailable: false,
    };
    state.integrationSetup = {
      dismissed: false,
      missing: [],
    };
    state.dashboard = {
      overview: null,
      emails: null,
      loaded: false,
    };
    dashboardRequestSequence += 1;
    integrationRequestSequence += 1;
    resetEmailListState();
    resetDashboardDomState();
  }

  function logout(options = {}) {
    clearSession();
    switchAuth("login");
    showScreen("auth-screen");
    window.history.replaceState(null, "", "/dashboard/");
    if (options.expired) {
      showAlert($("login-error"), "Votre session a expiré. Connectez-vous de nouveau.");
    }
  }

  function switchAuth(mode) {
    const isLogin = mode === "login";
    $("login-form").hidden = !isLogin;
    $("register-form").hidden = isLogin;
    $("login-tab").classList.toggle("active", isLogin);
    $("register-tab").classList.toggle("active", !isLogin);
    $("login-tab").setAttribute("aria-selected", String(isLogin));
    $("register-tab").setAttribute("aria-selected", String(!isLogin));
    showAlert($("login-error"), "");
    showAlert($("register-error"), "");
    window.setTimeout(() => {
      $(isLogin ? "login-email" : "register-email").focus();
    }, 0);
  }

  async function handleLogin(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const button = form.querySelector('button[type="submit"]');
    showAlert($("login-error"), "");
    setBusy(button, true, "Connexion…");
    try {
      const response = await apiRequest(API.login, {
        method: "POST",
        authenticated: false,
        body: {
          email: $("login-email").value.trim(),
          password: $("login-password").value,
        },
      });
      setSession(response.access_token);
      form.reset();
      await bootstrapAuthenticatedApp();
    } catch (error) {
      const message = error.status === 401
        ? "Email ou mot de passe incorrect."
        : error.message || "Connexion impossible.";
      showAlert($("login-error"), message);
    } finally {
      setBusy(button, false);
    }
  }

  async function handleRegister(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const errorElement = $("register-error");
    const password = $("register-password").value;
    const confirmation = $("register-confirm").value;
    if (password !== confirmation) {
      showAlert(errorElement, "Les deux mots de passe ne correspondent pas.");
      $("register-confirm").focus();
      return;
    }

    const button = form.querySelector('button[type="submit"]');
    showAlert(errorElement, "");
    setBusy(button, true, "Création…");
    try {
      const response = await apiRequest(API.register, {
        method: "POST",
        authenticated: false,
        body: {
          email: $("register-email").value.trim(),
          password,
          invitation_code: $("register-invitation").value.trim(),
        },
      });
      setSession(response.access_token);
      form.reset();
      showScreen("company-screen");
      renderOnboardingDriveStatus();
      await refreshOnboardingDriveStatus();
      $("company-name").focus();
    } catch (error) {
      let message = error.message || "Inscription impossible.";
      if (error.status === 403) {
        message = "Code d'invitation invalide.";
      } else if (error.status === 409) {
        message = "Un compte existe déjà pour cette adresse email. Connectez-vous plutôt.";
      }
      showAlert(errorElement, message);
    } finally {
      setBusy(button, false);
    }
  }

  function renderOnboardingDriveStatus(statusResponse = null, hasError = false) {
    const statusElement = $("onboarding-drive-status");
    const connectButton = $("connect-drive-onboarding");
    const submitButton = $("company-submit");
    const connected = Boolean(statusResponse && statusResponse.connected);

    statusElement.classList.remove("connected", "neutral", "error");
    if (hasError) {
      statusElement.classList.add("error");
      statusElement.textContent = "Statut Drive indisponible";
    } else if (connected) {
      statusElement.classList.add("connected");
      statusElement.textContent = "Google Drive connecté";
    } else {
      statusElement.classList.add("neutral");
      statusElement.textContent = "Google Drive requis";
    }
    connectButton.hidden = connected;
    connectButton.disabled = hasError;
    submitButton.disabled = !connected || hasError;
  }

  async function refreshOnboardingDriveStatus() {
    try {
      const response = await apiRequest(API.driveStatus);
      renderOnboardingDriveStatus(response);
      return response;
    } catch (error) {
      if (error.status !== 401) renderOnboardingDriveStatus(null, true);
      return null;
    }
  }

  async function connectDrive(button = $("connect-drive-onboarding")) {
    showAlert($("company-error"), "");
    showAlert($("integrations-error"), "");
    setBusy(button, true, "Ouverture de Google…");
    try {
      const response = await apiRequest(API.driveConnect);
      const authorizationUrl = new URL(response.authorization_url);
      if (authorizationUrl.protocol !== "https:" || authorizationUrl.hostname !== "accounts.google.com") {
        throw new ApiError(500, "L'adresse d'autorisation Google reçue est invalide.");
      }
      window.location.assign(authorizationUrl.href);
    } catch (error) {
      const target = state.company ? $("integrations-error") : $("company-error");
      showAlert(target, describeGoogleProblem(error));
      setBusy(button, false);
    }
  }

  async function handleCompanyCreation(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const button = form.querySelector('button[type="submit"]');
    const errorElement = $("company-error");
    const sector = selectedSectorValue($("company-sector"), $("company-sector-other"));
    if (!sector) {
      showAlert(errorElement, "Précisez votre secteur d'activité.");
      $("company-sector-other").focus();
      return;
    }
    showAlert(errorElement, "");
    setBusy(button, true, "Préparation du Sheet…");
    try {
      state.company = await apiRequest(API.companies, {
        method: "POST",
        body: {
          nom_entreprise: $("company-name").value.trim(),
          secteur: sector,
        },
      });
      form.reset();
      syncOtherSectorField(
        $("company-sector"),
        $("company-sector-other-field"),
        $("company-sector-other"),
      );
      await enterDashboard();
      showToast("Votre espace d'entreprise est prêt.");
    } catch (error) {
      let message = error.message || "Création de l'entreprise impossible.";
      if (error.status === 428) {
        message = "Connectez ou renouvelez Google Drive avant de créer votre espace.";
        await refreshOnboardingDriveStatus();
      } else if (error.status === 409) {
        message = "Une entreprise est déjà associée à ce compte. Rechargez la page pour ouvrir votre espace.";
      } else if (error.status === 502 || error.status === 503) {
        message = describeGoogleProblem(error);
      }
      showAlert(errorElement, message);
    } finally {
      setBusy(button, false);
      if (!state.company) await refreshOnboardingDriveStatus();
    }
  }

  async function bootstrapAuthenticatedApp() {
    showScreen("loading-screen");
    const payload = decodeJwt(state.token || "");
    if (!payload || !payload.email || (payload.exp && payload.exp * 1000 <= Date.now())) {
      logout({ expired: Boolean(payload && payload.exp) });
      return;
    }
    state.user = { id: payload.sub, email: payload.email };

    try {
      state.company = await apiRequest(API.company);
      await enterDashboard();
    } catch (error) {
      if (error.status === 404) {
        showAlert($("company-error"), "");
        showScreen("company-screen");
        renderOnboardingDriveStatus();
        await refreshOnboardingDriveStatus();
        handleDriveReturn();
        $("company-name").focus();
        return;
      }
      if (error.status !== 401) {
        showScreen("company-screen");
        showAlert(
          $("company-error"),
          error.status === 503
            ? "La base de données est temporairement indisponible. Rechargez la page dans quelques instants."
            : error.message,
        );
      }
    }
  }

  async function enterDashboard() {
    renderIdentity();
    state.appReady = true;
    showScreen("dashboard-shell");
    renderRoute();
    await loadDashboardResourcesIndependently();
    handleGmailReturn();
    handleDriveReturn();
  }

  async function loadDashboardResourcesIndependently(
    integrationLoader = () => checkIntegrationStatuses({ silent: true }),
    analyticsLoader = loadDashboardData,
  ) {
    const integrationPromise = integrationLoader().catch(() => null);
    const analyticsPromise = analyticsLoader().catch(() => undefined);
    await integrationPromise;
    await analyticsPromise;
  }

  function renderIdentity() {
    const email = state.user.email;
    const displayName = formatEmailPrefix(email);
    $("greeting-name").textContent = displayName;
    $("sidebar-user-name").textContent = displayName;
    $("sidebar-user-email").textContent = email;
    $("user-avatar").textContent = initialsFromEmail(email);

    const name = state.company.nom_entreprise;
    const sector = state.company.secteur;
    $("sidebar-company-name").textContent = name;
    $("sidebar-company-sector").textContent = sector;
    $("topbar-company-name").textContent = name;
    $("topbar-sector").textContent = sector;
    $("sheet-company-label").textContent = `Sheet : ${name}`;
    $("open-sheet").href = `https://docs.google.com/spreadsheets/d/${encodeURIComponent(state.company.sheet_id)}/edit`;
    renderSettings();
    renderCompanyStatuses();
  }

  function renderSettings() {
    if (!state.company || !state.user) return;
    $("settings-company-name").textContent = state.company.nom_entreprise;
    $("settings-account-email").textContent = state.user.email;
    setSectorControlValue(
      $("settings-sector"),
      $("settings-sector-other-field"),
      $("settings-sector-other"),
      state.company.secteur,
    );
    const responseTone = RESPONSE_TONES.includes(state.company.response_tone)
      ? state.company.response_tone
      : "professional";
    const toneInput = document.querySelector(
      `input[name="response_tone"][value="${responseTone}"]`,
    );
    if (toneInput) toneInput.checked = true;
    updateSectorEditingState();
  }

  function updateSectorEditingState() {
    const select = $("settings-sector");
    const otherField = $("settings-sector-other-field");
    const otherInput = $("settings-sector-other");
    const help = $("settings-sector-help");
    const editable = state.companyValidation.status === "pending"
      && !state.companyValidation.error;
    select.disabled = !editable;
    if (state.companyValidation.status === "validated") {
      help.textContent = "Votre entreprise est validée. Contactez le support pour modifier son secteur.";
    } else if (state.companyValidation.error) {
      help.textContent = "La modification du secteur est temporairement indisponible.";
    } else if (editable) {
      help.textContent = "Le secteur décrit votre activité. Il ne vaut pas autorisation pour les activités exclues du pilote.";
    } else {
      help.textContent = "Le secteur sera modifiable après vérification du statut de l'entreprise.";
    }
    syncOtherSectorField(select, otherField, otherInput);
  }

  async function handleSettingsSave(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const errorElement = $("settings-error");
    const button = $("save-settings");
    const toneInput = form.querySelector('input[name="response_tone"]:checked');
    const responseTone = toneInput ? toneInput.value : "";
    if (!RESPONSE_TONES.includes(responseTone)) {
      showAlert(errorElement, "Choisissez un ton de réponse valide.");
      return;
    }

    const body = { response_tone: responseTone };
    if (state.companyValidation.status === "pending" && !state.companyValidation.error) {
      const sector = selectedSectorValue($("settings-sector"), $("settings-sector-other"));
      if (!sector) {
        showAlert(errorElement, "Précisez votre secteur d'activité.");
        $("settings-sector-other").focus();
        return;
      }
      body.secteur = sector;
    }

    showAlert(errorElement, "");
    setBusy(button, true, "Enregistrement…");
    try {
      state.company = await apiRequest(API.companySettings, {
        method: "PATCH",
        body,
      });
      renderIdentity();
      showToast("Vos paramètres ont été enregistrés.");
    } catch (error) {
      let message = error.message || "Impossible d'enregistrer vos paramètres.";
      if (error.status === 409) {
        message = "Votre entreprise est désormais validée. Le secteur doit être modifié via le support.";
        await loadIntegrationStatuses({ silent: true });
      }
      showAlert(errorElement, message);
    } finally {
      setBusy(button, false);
    }
  }

  function setCompanyStatusPill(element, label, tone) {
    element.classList.remove("connected", "neutral", "warning", "error");
    element.classList.add(tone);
    element.textContent = label;
  }

  function getCompanyStatusDisplay(companyValidation = state.companyValidation) {
    if (companyValidation.error) {
      return Object.freeze({ label: "INDISPONIBLE", tone: "error" });
    }
    if (companyValidation.status === "validated") {
      return Object.freeze({ label: "VALIDÉE", tone: "connected" });
    }
    if (companyValidation.status === "pending") {
      return Object.freeze({ label: "EN ATTENTE", tone: "warning" });
    }
    return Object.freeze({ label: "Vérification…", tone: "neutral" });
  }

  function renderCompanyStatuses() {
    const display = getCompanyStatusDisplay();
    ["sidebar-validation-status", "topbar-validation-status"].forEach((id) => {
      setCompanyStatusPill($(id), display.label, display.tone);
    });
    updateSectorEditingState();
  }

  function applyCompanyValidationResult(result) {
    if (result && result.status === "fulfilled") {
      const validationStatus = result.value.validation_status;
      if (["pending", "validated"].includes(validationStatus)) {
        state.companyValidation.status = validationStatus;
        state.companyValidation.error = false;
        return null;
      }
      state.companyValidation.status = null;
      state.companyValidation.error = true;
      return "Validation DIGICRAFT LABS";
    }
    if (result && result.reason.status !== 401) {
      state.companyValidation.status = null;
      state.companyValidation.error = true;
      return "Validation DIGICRAFT LABS";
    }
    return null;
  }

  function setStatus(element, connected, hasError = false) {
    element.classList.remove("connected", "neutral", "error");
    if (hasError) {
      element.classList.add("error");
      element.textContent = "Indisponible";
    } else if (connected) {
      element.classList.add("connected");
      element.textContent = "Connecté";
    } else {
      element.classList.add("neutral");
      element.textContent = "Non connecté";
    }
  }

  function connectedCount(integrations = state.integrations) {
    return ALL_INTEGRATIONS.filter((key) => integrations[key] === true).length;
  }

  function missingRequiredIntegrations(integrations) {
    return REQUIRED_INTEGRATIONS.filter((key) => integrations[key] !== true);
  }

  function getIntegrationDisplayState(integrations) {
    const count = connectedCount(integrations);
    const missingRequired = missingRequiredIntegrations(integrations);
    const statusesAvailable = integrations.statusesAvailable === true;
    return Object.freeze({
      count,
      countLabel: statusesAvailable ? `${count}/3` : "—/3",
      missingRequired,
      ready: statusesAvailable && missingRequired.length === 0,
    });
  }

  function evaluateIntegrationSetup(statusSnapshot, options = {}) {
    const missing = missingRequiredIntegrations(statusSnapshot);
    return {
      missing,
      shouldShow: (
        options.dismissed !== true
        && options.route === "dashboard"
        && missing.length > 0
      ),
    };
  }

  function setIntegrationConfigurationHighlights(missingKeys) {
    const missing = new Set(missingKeys);
    ALL_INTEGRATIONS.forEach((key) => {
      const card = $(`${key}-integration-card`);
      if (card) card.classList.toggle("needs-configuration", missing.has(key));
    });
  }

  function openIntegrationSetupModal() {
    const dialog = $("integration-setup-dialog");
    if (!dialog || dialog.open || typeof dialog.showModal !== "function") return;
    dialog.showModal();
    $("configure-integrations").focus();
  }

  function closeIntegrationSetupModal() {
    const dialog = $("integration-setup-dialog");
    if (dialog && dialog.open) dialog.close();
  }

  function dismissIntegrationSetupModal() {
    state.integrationSetup.dismissed = true;
    closeIntegrationSetupModal();
  }

  function navigateToIntegrationSetup() {
    const missing = [...state.integrationSetup.missing];
    dismissIntegrationSetupModal();
    setIntegrationConfigurationHighlights(missing);
    if (window.location.hash === "#integrations") {
      renderRoute();
    } else {
      window.location.hash = "integrations";
    }
  }

  function maybeShowIntegrationSetupModal(statusSnapshot) {
    if (!statusSnapshot || !state.appReady || !state.token) return;
    const decision = evaluateIntegrationSetup(statusSnapshot, {
      dismissed: state.integrationSetup.dismissed,
      route: currentRoute(),
    });
    if (!decision.shouldShow) return;
    state.integrationSetup.missing = decision.missing;
    openIntegrationSetupModal();
  }

  function updateIntegrationUI() {
    const integrations = state.integrations;
    const telegramUnavailable = (
      integrations.telegramError || integrations.telegramAvailable === false
    );
    const display = getIntegrationDisplayState(integrations);
    setStatus($("gmail-status"), integrations.gmail, integrations.gmailError);
    setStatus($("sheet-status"), integrations.sheet, integrations.sheetError);
    const sheetStatusLabels = {
      disconnected: "Déconnecté",
      oauth_revoked: "Autorisation expirée",
      missing: "Fichier supprimé",
      trashed: "Dans la corbeille",
      ownership_changed: "Propriété modifiée",
      access_revoked: "Accès technique retiré",
      structure_invalid: "Structure modifiée",
      unavailable: "Indisponible",
    };
    if (!integrations.sheet && sheetStatusLabels[integrations.sheetState]) {
      $("sheet-status").textContent = sheetStatusLabels[integrations.sheetState];
    }
    setStatus(
      $("telegram-status"),
      integrations.telegram,
      !integrations.telegram && telegramUnavailable,
    );
    setIntegrationConfigurationHighlights(
      integrations.statusesAvailable ? display.missingRequired : [],
    );
    renderCompanyStatuses();

    $("dashboard-connected-count").textContent = display.countLabel;
    $("integrations-progress").textContent = display.countLabel;

    const banner = $("readiness-banner");
    banner.classList.toggle("ready", display.ready);
    if (!integrations.statusesAvailable) {
      $("readiness-title").textContent = "Statut des intégrations temporairement indisponible";
      $("readiness-text").textContent = "Gmail ou Google Sheets n'a pas pu être confirmé. Réessayez dans quelques instants.";
    } else if (display.ready) {
      $("readiness-title").textContent = "SmartReply est prêt";
      $("readiness-text").textContent = integrations.telegram
        ? "Gmail, Google Sheets et Telegram sont connectés."
        : "Gmail et Google Sheets sont connectés. Telegram reste optionnel.";
    } else {
      $("readiness-title").textContent = "Finalisez les services requis";
      $("readiness-text").textContent = "Connectez Gmail et Google Sheets pour rendre SmartReply disponible. Telegram est optionnel.";
    }

    const gmailButton = $("connect-gmail");
    gmailButton.disabled = integrations.gmail || integrations.gmailError;
    if (integrations.gmailError) {
      gmailButton.innerHTML = "<span>Statut Gmail indisponible</span>";
    } else {
      gmailButton.innerHTML = integrations.gmail
        ? '<svg><use href="#icon-check"></use></svg><span>Gmail connecté</span>'
        : '<span>Connecter Gmail</span><svg><use href="#icon-arrow"></use></svg>';
    }

    const sheetLink = $("open-sheet");
    sheetLink.setAttribute("aria-disabled", String(!integrations.sheet));
    sheetLink.tabIndex = integrations.sheet ? 0 : -1;
    const clientOwnedSheet = integrations.sheetOwnership === "client";
    const reconnectDriveButton = $("connect-drive");
    const repairSheetButton = $("repair-sheet-access");
    const disconnectDriveButton = $("disconnect-drive");
    reconnectDriveButton.hidden = !clientOwnedSheet || integrations.driveConnected;
    repairSheetButton.hidden = !clientOwnedSheet
      || !integrations.driveConnected
      || integrations.sheetState !== "access_revoked";
    disconnectDriveButton.hidden = !clientOwnedSheet || (
      !integrations.driveConnected && integrations.sheetState !== "oauth_revoked"
    );

    const telegramButton = $("generate-telegram-code");
    telegramButton.disabled = integrations.telegram || telegramUnavailable;
    if (integrations.telegram) {
      telegramButton.innerHTML = '<svg><use href="#icon-check"></use></svg><span>Telegram connecté</span>';
    } else if (telegramUnavailable) {
      telegramButton.innerHTML = "<span>Telegram indisponible</span>";
    } else {
      telegramButton.innerHTML = '<span>Générer un code</span><svg><use href="#icon-arrow"></use></svg>';
    }
    if (integrations.telegram) {
      $("telegram-code-panel").hidden = true;
      stopTelegramPolling();
    }
  }

  function applyIntegrationStatusResults(gmailResult, telegramResult, driveResult = null) {
    const errors = [];
    const driveAvailable = Boolean(
      driveResult && driveResult.status === "fulfilled"
    );
    const statusesAvailable = gmailResult.status === "fulfilled" && driveAvailable;
    state.integrations.statusesAvailable = statusesAvailable;

    if (gmailResult.status === "fulfilled") {
      state.integrations.gmail = Boolean(gmailResult.value.connected);
    } else if (gmailResult.reason.status !== 401) {
      state.integrations.gmail = false;
      state.integrations.gmailError = true;
      errors.push("Gmail");
    }

    if (driveAvailable) {
      state.integrations.driveConnected = Boolean(driveResult.value.connected);
      state.integrations.sheetState = driveResult.value.sheet_state || "unavailable";
      state.integrations.sheetOwnership = driveResult.value.ownership || "none";
      state.integrations.sheet = driveResult.value.sheet_state === "ready"
        && driveResult.value.pipeline_access === true;
      state.integrations.sheetError = driveResult.value.sheet_state === "unavailable";
    } else if (driveResult && driveResult.reason.status !== 401) {
      state.integrations.driveConnected = false;
      state.integrations.sheet = false;
      state.integrations.sheetError = true;
      state.integrations.sheetState = "unavailable";
      errors.push("Google Sheets");
    }

    if (telegramResult.status === "fulfilled") {
      state.integrations.telegram = Boolean(telegramResult.value.connected);
      state.integrations.telegramAvailable = telegramResult.value.available !== false;
    } else if (telegramResult.reason.status !== 401) {
      state.integrations.telegram = false;
      state.integrations.telegramError = true;
      errors.push("Telegram");
    }
    return Object.freeze({ errors, statusesAvailable });
  }

  async function loadIntegrationStatuses(options = {}) {
    const { silent = false } = options;
    const requestId = ++integrationRequestSequence;
    state.integrations.sheet = false;
    state.integrations.gmailError = false;
    state.integrations.sheetError = false;
    state.integrations.telegramError = false;
    state.integrations.telegramAvailable = null;
    state.integrations.statusesAvailable = false;
    state.companyValidation.status = null;
    state.companyValidation.error = false;
    renderCompanyStatuses();

    const [gmailResult, driveResult, telegramResult, validationResult] = await Promise.allSettled([
      apiRequest(API.gmailStatus),
      apiRequest(API.driveStatus),
      apiRequest(API.telegramStatus),
      apiRequest(API.companyValidationStatus),
    ]);
    if (requestId !== integrationRequestSequence || !state.token) return null;
    const { errors, statusesAvailable } = applyIntegrationStatusResults(
      gmailResult,
      telegramResult,
      driveResult,
    );
    const validationError = applyCompanyValidationResult(validationResult);
    if (validationError) errors.push(validationError);

    updateIntegrationUI();
    if (!silent && errors.length) {
      showAlert(
        $("integrations-error"),
        `Statut temporairement indisponible : ${errors.join(" et ")}. Réessayez dans quelques instants.`,
      );
    } else {
      showAlert($("integrations-error"), "");
    }
    if (!statusesAvailable) return null;
    return Object.freeze({
      gmail: state.integrations.gmail,
      sheet: state.integrations.sheet,
      telegram: state.integrations.telegram,
    });
  }

  async function checkIntegrationStatuses(
    options = {},
    statusLoader = loadIntegrationStatuses,
  ) {
    const statusSnapshot = await statusLoader(options);
    maybeShowIntegrationSetupModal(statusSnapshot);
    return statusSnapshot;
  }

  function formatCount(value) {
    return new Intl.NumberFormat("fr-FR").format(Number(value) || 0);
  }

  function formatDashboardDate(value, options = {}) {
    const parsed = new Date(value);
    if (!Number.isFinite(parsed.getTime())) return "—";
    const formatOptions = options.dateOnly
      ? { day: "2-digit", month: "short", year: "numeric" }
      : { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" };
    return new Intl.DateTimeFormat("fr-FR", formatOptions).format(parsed);
  }

  function setLoadingContent(element, label) {
    element.replaceChildren();
    const wrapper = document.createElement("span");
    wrapper.className = "data-loading";
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    spinner.setAttribute("aria-hidden", "true");
    wrapper.append(spinner, document.createTextNode(label));
    element.append(wrapper);
  }

  function setDashboardLoading() {
    $("dashboard-stats").setAttribute("aria-busy", "true");
    ["metric-analyzed", "metric-processed", "metric-ignored", "metric-pending"].forEach((id) => {
      $(id).textContent = "—";
    });
    setLoadingContent($("volume-chart"), "Chargement…");
    setLoadingContent($("category-chart"), "Chargement…");
    setLoadingContent($("priority-chart"), "Chargement…");
    $("recent-activity-list").hidden = true;
    $("recent-activity-state").hidden = false;
    $("recent-activity-state").classList.remove("error");
    $("recent-activity-state").replaceChildren();
    const activitySpinner = document.createElement("span");
    activitySpinner.className = "spinner";
    $("recent-activity-state").append(activitySpinner, document.createTextNode("Chargement de l'activité…"));
    $("recent-emails-table-wrap").hidden = true;
    $("recent-emails-state").hidden = false;
    $("recent-emails-state").classList.remove("error");
    $("recent-emails-state").replaceChildren();
    const emailSpinner = document.createElement("span");
    emailSpinner.className = "spinner";
    $("recent-emails-state").append(emailSpinner, document.createTextNode("Chargement des emails…"));
    $("dashboard-empty-state").hidden = true;
    showAlert($("dashboard-data-error"), "");
  }

  function renderVolumeChart(points) {
    const container = $("volume-chart");
    container.replaceChildren();
    const total = points.reduce((sum, point) => sum + Number(point.count || 0), 0);
    if (!points.length || total === 0) {
      const empty = document.createElement("p");
      empty.className = "chart-empty";
      empty.textContent = "Aucun email analysé sur cette période.";
      container.append(empty);
      return;
    }

    const maximum = Math.max(...points.map((point) => Number(point.count || 0)), 1);
    const chart = document.createElement("div");
    chart.className = "volume-chart";
    points.forEach((point) => {
      const count = Number(point.count || 0);
      const column = document.createElement("div");
      column.className = "volume-column";
      column.title = `${formatCount(count)} email${count > 1 ? "s" : ""}`;
      const barWrap = document.createElement("div");
      barWrap.className = "volume-bar-wrap";
      const barSvg = createSvgElement("svg");
      barSvg.classList.add("volume-bar-svg");
      barSvg.setAttribute("viewBox", "0 0 10 100");
      barSvg.setAttribute("preserveAspectRatio", "none");
      barSvg.setAttribute("aria-label", column.title);
      barSvg.setAttribute("role", "img");
      const bar = createSvgElement("rect");
      const barHeight = count === 0 ? 2 : Math.max(8, (count / maximum) * 100);
      bar.classList.add("volume-bar");
      bar.setAttribute("x", "0");
      bar.setAttribute("y", String(100 - barHeight));
      bar.setAttribute("width", "10");
      bar.setAttribute("height", String(barHeight));
      bar.setAttribute("rx", "1.5");
      barSvg.append(bar);
      barWrap.append(barSvg);
      const label = document.createElement("small");
      const day = new Date(`${point.day}T00:00:00`);
      label.textContent = Number.isFinite(day.getTime()) ? String(day.getDate()) : "—";
      column.append(barWrap, label);
      chart.append(column);
    });
    container.append(chart);
  }

  function allocateDonutPercentages(segments, total) {
    const exactUnits = segments.map((segment) => (segment.count / total) * 1000);
    const roundedUnits = exactUnits.map(Math.floor);
    let remainingUnits = 1000 - roundedUnits.reduce((sum, value) => sum + value, 0);
    const remainderOrder = exactUnits
      .map((value, index) => ({ index, remainder: value - roundedUnits[index] }))
      .sort((left, right) => right.remainder - left.remainder || left.index - right.index);

    for (let index = 0; index < remainingUnits; index += 1) {
      roundedUnits[remainderOrder[index % remainderOrder.length].index] += 1;
    }
    return roundedUnits.map((value) => value / 10);
  }

  function prepareDonutData(items, total) {
    const numericTotal = Number(total);
    const hasValidTotal = total !== null
      && total !== undefined
      && Number.isSafeInteger(numericTotal)
      && numericTotal >= 0;
    const analyzedTotal = hasValidTotal ? numericTotal : 0;
    const segments = [];
    let invalidCount = false;

    (Array.isArray(items) ? items : []).forEach((item) => {
      if (!item || item.count === null || item.count === undefined) return;
      const numericCount = Number(item.count);
      if (!Number.isSafeInteger(numericCount) || numericCount < 0) {
        invalidCount = true;
        return;
      }
      if (numericCount === 0) return;
      segments.push({
        key: String(item.key || ""),
        label: String(item.label || item.key || ""),
        count: numericCount,
      });
    });

    const knownTotal = segments.reduce((sum, segment) => sum + segment.count, 0);
    if (
      invalidCount
      || !hasValidTotal
      || !Number.isSafeInteger(knownTotal)
      || knownTotal > analyzedTotal
    ) {
      return {
        state: "inconsistent",
        total: analyzedTotal,
        knownTotal,
        segments: [],
      };
    }

    if (analyzedTotal === 0) {
      return { state: "empty", total: 0, knownTotal: 0, segments: [] };
    }

    const unclassified = analyzedTotal - knownTotal;
    if (unclassified > 0) {
      segments.push({
        key: "unclassified",
        label: "Non classé",
        count: unclassified,
      });
    }

    const percentages = allocateDonutPercentages(segments, analyzedTotal);
    return {
      state: "ready",
      total: analyzedTotal,
      knownTotal,
      segments: segments.map((segment, index) => ({
        ...segment,
        percentage: percentages[index],
        share: segment.count / analyzedTotal,
      })),
    };
  }

  function formatDonutPercentage(value) {
    return `${new Intl.NumberFormat("fr-FR", {
      minimumFractionDigits: Number.isInteger(value) ? 0 : 1,
      maximumFractionDigits: 1,
    }).format(value)} %`;
  }

  function donutColorClass(kind, key, index) {
    const palette = DONUT_PALETTES[kind] || DONUT_PALETTES.category;
    return palette[key] || DONUT_FALLBACK_COLORS[index % DONUT_FALLBACK_COLORS.length];
  }

  function createSvgElement(name) {
    return document.createElementNS(SVG_NAMESPACE, name);
  }

  function renderDonutChart(containerId, items, options) {
    const container = $(containerId);
    container.replaceChildren();
    const chartData = prepareDonutData(items, options.total);

    if (chartData.state === "empty") {
      const empty = document.createElement("p");
      empty.className = "chart-empty";
      empty.textContent = "Aucune donnée enregistrée pour le moment.";
      container.append(empty);
      return;
    }

    if (chartData.state === "inconsistent") {
      const error = document.createElement("p");
      error.className = "chart-empty donut-error";
      error.setAttribute("role", "alert");
      error.textContent = "Données de répartition incohérentes avec le total analysé.";
      container.append(error);
      return;
    }

    const chart = document.createElement("div");
    chart.className = "donut-chart";
    const visual = document.createElement("div");
    visual.className = "donut-visual";
    const svg = createSvgElement("svg");
    svg.classList.add("donut-svg");
    svg.setAttribute("viewBox", "0 0 120 120");
    svg.setAttribute("role", "img");
    const titleId = `${containerId}-donut-title`;
    const descriptionId = `${containerId}-donut-description`;
    svg.setAttribute("aria-labelledby", `${titleId} ${descriptionId}`);

    const title = createSvgElement("title");
    title.id = titleId;
    title.textContent = options.title;
    const description = createSvgElement("desc");
    description.id = descriptionId;
    const segmentDescription = chartData.segments
      .map((segment) => (
        `${segment.label} : ${formatCount(segment.count)} emails, ${formatDonutPercentage(segment.percentage)}`
      ))
      .join(". ");
    description.textContent = `Total : ${formatCount(chartData.total)} emails. ${segmentDescription}.`;
    const track = createSvgElement("circle");
    track.classList.add("donut-track");
    track.setAttribute("cx", "60");
    track.setAttribute("cy", "60");
    track.setAttribute("r", "48");
    track.setAttribute("pathLength", "100");
    svg.append(title, description, track);

    let offset = 0;
    chartData.segments.forEach((segment, index) => {
      const circle = createSvgElement("circle");
      const segmentSize = segment.share * 100;
      circle.classList.add("donut-segment");
      circle.setAttribute("cx", "60");
      circle.setAttribute("cy", "60");
      circle.setAttribute("r", "48");
      circle.setAttribute("pathLength", "100");
      circle.setAttribute("transform", "rotate(-90 60 60)");
      circle.setAttribute("stroke-dasharray", `${segmentSize} ${100 - segmentSize}`);
      circle.setAttribute("stroke-dashoffset", String(-offset));
      circle.classList.add(donutColorClass(options.kind, segment.key, index));
      offset += segmentSize;
      svg.append(circle);
    });

    const totalValue = createSvgElement("text");
    totalValue.classList.add("donut-total-value");
    totalValue.setAttribute("x", "60");
    totalValue.setAttribute("y", "57");
    totalValue.textContent = formatCount(chartData.total);
    const totalLabel = createSvgElement("text");
    totalLabel.classList.add("donut-total-label");
    totalLabel.setAttribute("x", "60");
    totalLabel.setAttribute("y", "72");
    totalLabel.textContent = "emails";
    svg.append(totalValue, totalLabel);
    visual.append(svg);

    const legend = document.createElement("ul");
    legend.className = "donut-legend";
    legend.setAttribute("aria-label", `Légende : ${options.title}`);
    chartData.segments.forEach((segment, index) => {
      const item = document.createElement("li");
      item.className = "donut-legend-item";
      const swatch = document.createElement("span");
      swatch.className = "donut-legend-swatch";
      swatch.classList.add(donutColorClass(options.kind, segment.key, index));
      swatch.setAttribute("aria-hidden", "true");
      const label = document.createElement("span");
      label.className = "donut-legend-label";
      label.textContent = segment.label;
      const values = document.createElement("span");
      values.className = "donut-legend-values";
      const count = document.createElement("strong");
      count.textContent = formatCount(segment.count);
      const percentage = document.createElement("small");
      percentage.textContent = formatDonutPercentage(segment.percentage);
      values.append(count, percentage);
      item.append(swatch, label, values);
      legend.append(item);
    });

    chart.append(visual, legend);
    container.append(chart);
  }

  function renderActivity(items) {
    const list = $("recent-activity-list");
    const stateElement = $("recent-activity-state");
    list.replaceChildren();
    if (!items.length) {
      list.hidden = true;
      stateElement.hidden = false;
      stateElement.classList.remove("error");
      stateElement.textContent = "Aucune activité enregistrée depuis l'activation de la Phase B.";
      return;
    }

    items.forEach((item) => {
      const entry = document.createElement("li");
      const dot = document.createElement("span");
      dot.className = "activity-dot";
      dot.setAttribute("aria-hidden", "true");
      const copy = document.createElement("span");
      copy.className = "activity-copy";
      const label = document.createElement("strong");
      label.textContent = item.label;
      const time = document.createElement("time");
      time.dateTime = item.occurred_at;
      time.textContent = formatDashboardDate(item.occurred_at);
      copy.append(label, time);
      entry.append(dot, copy);
      list.append(entry);
    });
    stateElement.hidden = true;
    list.hidden = false;
  }

  function renderOverview(overview) {
    state.dashboard.overview = overview;
    $("dashboard-stats").setAttribute("aria-busy", "false");
    $("metric-analyzed").textContent = formatCount(overview.metrics.analyzed_emails);
    $("metric-processed").textContent = formatCount(overview.metrics.processed_emails);
    $("metric-ignored").textContent = formatCount(overview.metrics.ignored_emails);
    $("metric-pending").textContent = formatCount(overview.metrics.pending_drafts);
    renderVolumeChart(overview.volume || []);
    renderDonutChart("category-chart", overview.categories || [], {
      kind: "category",
      title: "Répartition des emails par catégorie",
      total: overview.metrics.analyzed_emails,
    });
    renderDonutChart("priority-chart", overview.priorities || [], {
      kind: "priority",
      title: "Répartition des emails par priorité",
      total: overview.metrics.analyzed_emails,
    });
    renderActivity(overview.recent_activity || []);
    $("analytics-history-note").hidden = Boolean(overview.history_started)
      || Number(overview.metrics.analyzed_emails || 0) === 0;
  }

  function badgeClass(value) {
    const normalized = String(value || "").toLocaleLowerCase("fr");
    if (["haute", "envoyé"].includes(normalized)) return "danger";
    if (["moyenne", "brouillon prêt", "analysé"].includes(normalized)) return "warning";
    return "neutral";
  }

  function renderRecentEmails(emails) {
    state.dashboard.emails = emails;
    const body = $("recent-emails-body");
    const stateElement = $("recent-emails-state");
    const tableWrap = $("recent-emails-table-wrap");
    body.replaceChildren();
    if (!emails.length) {
      tableWrap.hidden = true;
      stateElement.hidden = false;
      stateElement.classList.remove("error");
      stateElement.textContent = "Aucun email n'est encore disponible dans votre Sheet.";
      return;
    }

    emails.forEach((email) => {
      const row = document.createElement("tr");
      const values = [
        email.sender,
        email.subject,
        email.received_at ? formatDashboardDate(email.received_at, { dateOnly: true }) : "—",
        email.category || "—",
        email.priority || "—",
        email.status || "—",
      ];
      values.forEach((value, index) => {
        const cell = document.createElement("td");
        if (index === 1) cell.className = "subject-cell";
        if (index >= 3) {
          const badge = document.createElement("span");
          badge.className = `data-badge ${badgeClass(value)}`;
          badge.textContent = value;
          cell.append(badge);
        } else {
          cell.textContent = value;
        }
        row.append(cell);
      });
      body.append(row);
    });
    stateElement.hidden = true;
    tableWrap.hidden = false;
  }

  function normalizedEmailFilterValue(filterName, value) {
    const candidate = String(value || "").trim();
    return EMAIL_FILTER_VALUES[filterName].includes(candidate) ? candidate : "";
  }

  function readEmailCriteria() {
    const elements = getEmailsViewElements();
    return {
      search: String(elements.search.value || "").trim().slice(0, 200),
      category: normalizedEmailFilterValue("category", elements.category.value),
      priority: normalizedEmailFilterValue("priority", elements.priority.value),
      status: normalizedEmailFilterValue("status", elements.status.value),
    };
  }

  function sameEmailCriteria(left, right) {
    return (
      left.search === right.search
      && left.category === right.category
      && left.priority === right.priority
      && left.status === right.status
    );
  }

  function cancelEmailSearchDebounce() {
    if (emailsSearchDebounceId === null) return;
    window.clearTimeout(emailsSearchDebounceId);
    emailsSearchDebounceId = null;
  }

  function restoreActiveEmailCriteriaControls() {
    const elements = getEmailsViewElements();
    elements.search.value = state.emailList.criteria.search;
    elements.category.value = state.emailList.criteria.category;
    elements.priority.value = state.emailList.criteria.priority;
    elements.status.value = state.emailList.criteria.status;
  }

  function applyEmailCriteriaFromControls() {
    const criteria = readEmailCriteria();
    if (sameEmailCriteria(criteria, state.emailList.criteria)) return false;
    state.emailList.criteria = criteria;
    void loadEmailsPage(1);
    return true;
  }

  function scheduleEmailSearch() {
    cancelEmailSearchDebounce();
    const criteria = readEmailCriteria();
    if (sameEmailCriteria(criteria, state.emailList.criteria)) return;
    emailsSearchDebounceId = window.setTimeout(() => {
      emailsSearchDebounceId = null;
      applyEmailCriteriaFromControls();
    }, EMAILS_SEARCH_DEBOUNCE_MS);
  }

  function applyEmailFilters() {
    cancelEmailSearchDebounce();
    applyEmailCriteriaFromControls();
  }

  function validEmailId(emailId) {
    return /^EM-[1-9][0-9]*$/.test(String(emailId || ""));
  }

  function emailDetailIdFromHash() {
    const route = window.location.hash.replace(/^#/, "");
    if (!route.startsWith("emails/")) return null;
    try {
      return decodeURIComponent(route.slice("emails/".length));
    } catch (_) {
      return "";
    }
  }

  function navigateToEmailDetail(emailId) {
    const normalizedId = String(emailId || "");
    if (!validEmailId(normalizedId)) return;
    cancelEmailSearchDebounce();
    restoreActiveEmailCriteriaControls();
    const targetHash = `#emails/${encodeURIComponent(normalizedId)}`;
    if (window.location.hash === targetHash) {
      renderRoute();
      return;
    }
    window.location.hash = targetHash;
  }

  function returnToEmailList() {
    if (window.location.hash === "#emails") {
      renderRoute();
      return;
    }
    window.location.hash = "#emails";
  }

  function setEmailDetailDisplay(displayState) {
    const elements = getEmailsViewElements();
    elements.detailWorkspace.setAttribute(
      "aria-busy",
      String(displayState === "loading"),
    );
    elements.detailLoading.hidden = displayState !== "loading";
    elements.detailNotFound.hidden = displayState !== "not-found";
    elements.detailError.hidden = displayState !== "error";
    elements.detailContent.hidden = displayState !== "ready";
  }

  function setEmailDetailBadge(element, value) {
    const label = String(value || "—");
    element.className = `data-badge ${badgeClass(label)}`;
    element.textContent = label;
  }

  function publicEmailDetail(payload) {
    const source = payload && typeof payload === "object" ? payload : {};
    return {
      sender_name: String(source.sender_name || ""),
      sender_email: String(source.sender_email || ""),
      subject: String(source.subject || ""),
      received_at: source.received_at || null,
      summary: String(source.summary || ""),
      category: String(source.category || ""),
      priority: String(source.priority || ""),
      status: String(source.status || ""),
    };
  }

  function renderEmailDetail(item) {
    const elements = getEmailsViewElements();
    const subject = item.subject || "Sans objet";
    elements.detailTitle.textContent = subject;
    elements.detailSenderName.textContent = item.sender_name || "—";
    elements.detailSenderEmail.textContent = item.sender_email || "—";
    elements.detailSubject.textContent = subject;
    elements.detailReceivedAt.textContent = item.received_at
      ? formatDashboardDate(item.received_at)
      : "—";
    elements.detailSummary.textContent = item.summary || "—";
    setEmailDetailBadge(elements.detailCategory, item.category);
    setEmailDetailBadge(elements.detailPriority, item.priority);
    setEmailDetailBadge(elements.detailStatus, item.status);
    setEmailDetailDisplay("ready");
  }

  async function loadEmailDetail(emailId) {
    const normalizedId = String(emailId || "");
    if (!validEmailId(normalizedId)) {
      emailDetailRequestSequence += 1;
      state.emailDetail = {
        ...createEmailDetailState(normalizedId),
        error: "not-found",
      };
      setEmailDetailDisplay("not-found");
      return;
    }

    const requestId = ++emailDetailRequestSequence;
    state.emailDetail = {
      ...createEmailDetailState(normalizedId),
      loading: true,
    };
    setEmailDetailDisplay("loading");

    try {
      const payload = await apiRequest(
        `${API.dashboardEmails}/${encodeURIComponent(normalizedId)}`,
      );
      if (requestId !== emailDetailRequestSequence) return;

      const item = publicEmailDetail(payload);
      state.emailDetail = {
        emailId: normalizedId,
        item,
        loaded: true,
        loading: false,
        error: null,
      };
      renderEmailDetail(item);
    } catch (error) {
      if (requestId !== emailDetailRequestSequence) return;
      const displayState = error.status === 404 ? "not-found" : "error";
      state.emailDetail = {
        ...createEmailDetailState(normalizedId),
        error: displayState,
      };
      if (error.status !== 401) setEmailDetailDisplay(displayState);
    }
  }

  function showEmailListWorkspace() {
    const elements = getEmailsViewElements();
    elements.workspace.hidden = false;
    elements.detailWorkspace.hidden = true;
    if (state.emailDetail.loading) {
      emailDetailRequestSequence += 1;
      state.emailDetail = createEmailDetailState();
    } else if (state.emailDetail.error) {
      state.emailDetail = createEmailDetailState();
    }
  }

  function showEmailDetailWorkspace(emailId) {
    const elements = getEmailsViewElements();
    elements.workspace.hidden = true;
    elements.detailWorkspace.hidden = false;

    if (!validEmailId(emailId)) {
      void loadEmailDetail(emailId);
      return;
    }
    if (state.emailDetail.emailId === emailId) {
      if (state.emailDetail.loaded && state.emailDetail.item) {
        renderEmailDetail(state.emailDetail.item);
        return;
      }
      if (state.emailDetail.loading) {
        setEmailDetailDisplay("loading");
        return;
      }
      if (state.emailDetail.error) {
        setEmailDetailDisplay(state.emailDetail.error);
        return;
      }
    }
    void loadEmailDetail(emailId);
  }

  function dashboardEmailsPath(page, criteria) {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(EMAILS_PAGE_SIZE),
    });
    if (criteria.search) params.set("search", criteria.search);
    if (criteria.category) params.set("category", criteria.category);
    if (criteria.priority) params.set("priority", criteria.priority);
    if (criteria.status) params.set("status", criteria.status);
    return `${API.dashboardEmails}?${params.toString()}`;
  }

  function setEmailListDisplay(displayState) {
    const elements = getEmailsViewElements();
    elements.workspace.setAttribute(
      "aria-busy",
      String(displayState === "loading"),
    );
    elements.loading.hidden = displayState !== "loading";
    elements.empty.hidden = displayState !== "empty";
    elements.error.hidden = displayState !== "error";
    elements.retry.disabled = displayState === "loading";
    elements.table.hidden = displayState !== "ready";
    elements.pagination.hidden = displayState !== "ready";
  }

  function appendEmailBadgeCell(row, value) {
    const cell = document.createElement("td");
    const badge = document.createElement("span");
    const label = String(value || "—");
    badge.className = `data-badge ${badgeClass(label)}`;
    badge.textContent = label;
    cell.append(badge);
    row.append(cell);
  }

  function renderEmailListPagination(pagination, visibleItems) {
    const elements = getEmailsViewElements();
    const page = Number(pagination.page);
    const pageSize = Number(pagination.page_size);
    const totalItems = Number(pagination.total_items);
    const totalPages = Number(pagination.total_pages);
    const firstItem = totalItems > 0 ? ((page - 1) * pageSize) + 1 : 0;
    const lastItem = totalItems > 0
      ? Math.min(firstItem + visibleItems - 1, totalItems)
      : 0;
    const itemLabel = totalItems === 1 ? "email" : "emails";

    elements.paginationSummary.textContent = totalItems > pageSize
      ? `${formatCount(firstItem)}–${formatCount(lastItem)} sur ${formatCount(totalItems)} ${itemLabel}`
      : `${formatCount(totalItems)} ${itemLabel}`;
    elements.pageIndicator.textContent = `Page ${formatCount(page)} sur ${formatCount(Math.max(totalPages, 1))}`;
    elements.previousPage.disabled = page <= 1;
    elements.nextPage.disabled = totalPages === 0 || page >= totalPages;
  }

  function renderEmailList(items, pagination) {
    const elements = getEmailsViewElements();
    elements.tableBody.replaceChildren();
    renderEmailListPagination(pagination, items.length);

    if (!items.length) {
      setEmailListDisplay("empty");
      return;
    }

    items.forEach((email) => {
      const row = document.createElement("tr");
      const emailId = String(email.email_id || "");
      row.dataset.emailId = emailId;

      const senderCell = document.createElement("td");
      senderCell.className = "emails-sender-cell";
      const senderName = document.createElement("strong");
      senderName.textContent = String(email.sender_name || "Expéditeur");
      const senderEmail = document.createElement("small");
      senderEmail.textContent = String(email.sender_email || "—");
      senderCell.append(senderName, senderEmail);
      row.append(senderCell);

      const subjectCell = document.createElement("td");
      subjectCell.className = "subject-cell";
      const subjectLabel = String(email.subject || "Sans objet");
      if (validEmailId(emailId)) {
        const detailButton = document.createElement("button");
        detailButton.type = "button";
        detailButton.className = "email-detail-link";
        detailButton.textContent = subjectLabel;
        detailButton.setAttribute(
          "aria-label",
          `Ouvrir le détail de l'email : ${subjectLabel}`,
        );
        detailButton.addEventListener("click", (event) => {
          event.stopPropagation();
          navigateToEmailDetail(emailId);
        });
        subjectCell.append(detailButton);
      } else {
        subjectCell.textContent = subjectLabel;
      }
      row.append(subjectCell);

      const dateCell = document.createElement("td");
      dateCell.textContent = email.received_at
        ? formatDashboardDate(email.received_at)
        : "—";
      row.append(dateCell);

      appendEmailBadgeCell(row, email.category);
      appendEmailBadgeCell(row, email.priority);
      appendEmailBadgeCell(row, email.status);
      if (validEmailId(emailId)) {
        row.classList.add("emails-row-link");
        row.addEventListener("click", () => navigateToEmailDetail(emailId));
      }
      elements.tableBody.append(row);
    });

    setEmailListDisplay("ready");
  }

  function normalizedEmailPagination(rawPagination, requestedPage) {
    const source = rawPagination && typeof rawPagination === "object"
      ? rawPagination
      : {};
    const values = {
      page: Number(source.page),
      page_size: Number(source.page_size),
      total_items: Number(source.total_items),
      total_pages: Number(source.total_pages),
    };
    return {
      page: Number.isSafeInteger(values.page) && values.page >= 1
        ? values.page
        : requestedPage,
      page_size: Number.isSafeInteger(values.page_size) && values.page_size >= 1
        ? values.page_size
        : EMAILS_PAGE_SIZE,
      total_items: Number.isSafeInteger(values.total_items) && values.total_items >= 0
        ? values.total_items
        : 0,
      total_pages: Number.isSafeInteger(values.total_pages) && values.total_pages >= 0
        ? values.total_pages
        : 0,
    };
  }

  async function loadEmailsPage(page = 1, options = {}) {
    const { allowPageCorrection = true } = options;
    const requestedPage = Number.isSafeInteger(page) && page >= 1 ? page : 1;
    const criteria = { ...state.emailList.criteria };
    const requestId = ++emailsRequestSequence;
    const elements = getEmailsViewElements();
    state.emailList.loading = true;
    state.emailList.loaded = false;
    elements.tableBody.replaceChildren();
    setEmailListDisplay("loading");

    try {
      const payload = await apiRequest(
        dashboardEmailsPath(requestedPage, criteria),
      );
      if (requestId !== emailsRequestSequence) return;

      const items = Array.isArray(payload.items) ? payload.items : [];
      const pagination = normalizedEmailPagination(
        payload.pagination,
        requestedPage,
      );
      if (
        allowPageCorrection
        && pagination.total_items > 0
        && pagination.total_pages > 0
        && pagination.page > pagination.total_pages
      ) {
        return loadEmailsPage(pagination.total_pages, {
          allowPageCorrection: false,
        });
      }
      state.emailList = {
        items,
        pagination,
        criteria,
        loaded: true,
        loading: false,
      };
      renderEmailList(items, pagination);
    } catch (error) {
      if (requestId !== emailsRequestSequence) return;
      state.emailList = createEmailListState(criteria);
      elements.tableBody.replaceChildren();
      if (error.status !== 401) setEmailListDisplay("error");
    }
  }

  function retryEmailsPage() {
    if (state.emailList.loading) return false;
    void loadEmailsPage(state.emailList.pagination.page);
    return true;
  }

  function loadPreviousEmailsPage() {
    const { page } = state.emailList.pagination;
    if (state.emailList.loading || page <= 1) return;
    void loadEmailsPage(page - 1);
  }

  function loadNextEmailsPage() {
    const { page, total_pages: totalPages } = state.emailList.pagination;
    if (state.emailList.loading || totalPages === 0 || page >= totalPages) return;
    void loadEmailsPage(page + 1);
  }

  function renderOverviewError() {
    $("dashboard-stats").setAttribute("aria-busy", "false");
    ["volume-chart", "category-chart", "priority-chart"].forEach((id) => {
      const element = $(id);
      element.replaceChildren();
      const error = document.createElement("p");
      error.className = "chart-empty";
      error.textContent = "Données temporairement indisponibles.";
      element.append(error);
    });
    $("recent-activity-list").hidden = true;
    $("recent-activity-state").hidden = false;
    $("recent-activity-state").classList.add("error");
    $("recent-activity-state").textContent = "Activité temporairement indisponible.";
  }

  function renderRecentEmailsError() {
    $("recent-emails-table-wrap").hidden = true;
    $("recent-emails-state").hidden = false;
    $("recent-emails-state").classList.add("error");
    $("recent-emails-state").textContent = "Connexion au Google Sheet impossible. Réessayez dans quelques instants.";
  }

  function updateDashboardEmptyState() {
    const overview = state.dashboard.overview;
    const emails = state.dashboard.emails;
    if (!overview || !emails) {
      $("dashboard-empty-state").hidden = true;
      return;
    }
    $("dashboard-empty-state").hidden = !(
      Number(overview.metrics.analyzed_emails || 0) === 0
      && emails.length === 0
      && (overview.recent_activity || []).length === 0
    );
  }

  async function loadDashboardData(options = {}) {
    const { showLoading = true } = options;
    const requestId = ++dashboardRequestSequence;
    if (showLoading) setDashboardLoading();
    const [overviewResult, emailsResult] = await Promise.allSettled([
      apiRequest(API.dashboardOverview),
      apiRequest(API.recentEmails),
    ]);
    if (requestId !== dashboardRequestSequence || !state.token) return;
    const unavailable = [];

    if (overviewResult.status === "fulfilled") {
      renderOverview(overviewResult.value);
    } else if (overviewResult.reason.status !== 401) {
      state.dashboard.overview = null;
      renderOverviewError();
      unavailable.push("les statistiques");
    }

    if (emailsResult.status === "fulfilled") {
      renderRecentEmails(emailsResult.value.emails || []);
    } else if (emailsResult.reason.status !== 401) {
      state.dashboard.emails = null;
      renderRecentEmailsError();
      unavailable.push("les emails récents");
    }

    state.dashboard.loaded = unavailable.length === 0;
    updateDashboardEmptyState();
    showAlert(
      $("dashboard-data-error"),
      unavailable.length
        ? `Impossible de charger ${unavailable.join(" et ")}. Les autres données restent disponibles.`
        : "",
    );
  }

  async function refreshDashboard() {
    const button = $("refresh-dashboard");
    setBusy(button, true, "Actualisation…");
    try {
      await loadDashboardResourcesIndependently();
      if ($("dashboard-data-error").hidden) {
        showToast("Les données du tableau de bord sont à jour.");
      }
    } finally {
      setBusy(button, false);
    }
  }

  async function connectGmail() {
    const button = $("connect-gmail");
    showAlert($("integrations-error"), "");
    setBusy(button, true, "Ouverture de Google…");
    try {
      const response = await apiRequest(API.gmailConnect);
      const authorizationUrl = new URL(response.authorization_url);
      if (authorizationUrl.protocol !== "https:" || authorizationUrl.hostname !== "accounts.google.com") {
        throw new ApiError(500, "L'adresse d'autorisation Google reçue est invalide.");
      }
      window.location.assign(authorizationUrl.href);
    } catch (error) {
      showAlert($("integrations-error"), describeGoogleProblem(error));
      setBusy(button, false);
    }
  }

  async function disconnectDrive() {
    if (!window.confirm("Déconnecter Google Drive ? Le Sheet restera dans votre Drive, mais SmartReply ne pourra plus le modifier.")) return;
    const button = $("disconnect-drive");
    showAlert($("integrations-error"), "");
    setBusy(button, true, "Déconnexion…");
    try {
      const response = await apiRequest(API.driveDisconnect, { method: "DELETE" });
      await checkIntegrationStatuses({ silent: true });
      if (response.manual_action_required) {
        showAlert(
          $("integrations-error"),
          "SmartReply est arrêté pour ce Sheet. Vérifiez aussi les accès du fichier dans Google Drive.",
        );
      } else {
        showToast("Google Drive a été déconnecté. Votre fichier reste dans votre compte.");
      }
    } catch (error) {
      showAlert($("integrations-error"), describeGoogleProblem(error));
    } finally {
      setBusy(button, false);
    }
  }

  async function repairSheetAccess() {
    const button = $("repair-sheet-access");
    showAlert($("integrations-error"), "");
    setBusy(button, true, "Rétablissement…");
    try {
      await apiRequest(API.pipelineAccess, { method: "POST" });
      await checkIntegrationStatuses({ silent: true });
      showToast("L'accès technique au Sheet a été rétabli.");
    } catch (error) {
      showAlert($("integrations-error"), describeGoogleProblem(error));
    } finally {
      setBusy(button, false);
    }
  }

  async function refreshIntegrations() {
    const button = $("refresh-integrations");
    setBusy(button, true, "Actualisation…");
    try {
      await checkIntegrationStatuses();
      if (!$("integrations-error").hidden) return;
      showToast("Les statuts des intégrations sont à jour.");
    } finally {
      setBusy(button, false);
    }
  }

  async function generateTelegramCode() {
    const button = $("generate-telegram-code");
    const requestToken = state.token;
    showAlert($("telegram-code-error"), "");
    showAlert($("integrations-error"), "");
    setBusy(button, true, "Génération…");
    try {
      const response = await apiRequest(API.telegramCode, { method: "POST" });
      if (!state.token || state.token !== requestToken) return;
      $("telegram-code").textContent = response.code;
      $("telegram-command").textContent = `/start ${response.code}`;
      $("telegram-code-panel").hidden = false;
      startTelegramPolling(response.expires_at);
      showToast("Code Telegram généré. Il reste valide pendant 15 minutes.");
    } catch (error) {
      if (error.status === 409) {
        await checkIntegrationStatuses({ silent: true });
        showToast("Ce compte est déjà connecté à Telegram.");
      } else {
        showAlert(
          $("integrations-error"),
          error.status === 503
            ? "La génération du code Telegram est temporairement indisponible. Réessayez plus tard."
            : error.message,
        );
      }
    } finally {
      setBusy(button, false);
      updateIntegrationUI();
    }
  }

  async function copyTelegramCode() {
    const code = $("telegram-code").textContent;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(code);
      } else {
        const input = document.createElement("textarea");
        input.value = code;
        input.setAttribute("readonly", "");
        input.className = "clipboard-fallback-input";
        document.body.append(input);
        input.select();
        document.execCommand("copy");
        input.remove();
      }
      showToast("Code copié.");
    } catch (_) {
      showAlert($("telegram-code-error"), "Copie automatique impossible. Sélectionnez le code manuellement.");
    }
  }

  function startTelegramPolling(expiresAt) {
    stopTelegramPolling();
    const expiration = Date.parse(expiresAt);
    state.telegramExpiresAt = Number.isFinite(expiration)
      ? expiration
      : Date.now() + 15 * 60 * 1000;
    updateTelegramCountdown();
    state.telegramCountdownId = window.setInterval(updateTelegramCountdown, 1000);
    state.telegramPollId = window.setInterval(pollTelegramStatus, TELEGRAM_POLL_INTERVAL_MS);
  }

  function stopTelegramPolling() {
    if (state.telegramPollId) window.clearInterval(state.telegramPollId);
    if (state.telegramCountdownId) window.clearInterval(state.telegramCountdownId);
    state.telegramPollId = null;
    state.telegramCountdownId = null;
    state.telegramPollInFlight = false;
    state.telegramExpiresAt = null;
  }

  function updateTelegramCountdown() {
    if (!state.telegramExpiresAt) return;
    const remaining = Math.max(0, state.telegramExpiresAt - Date.now());
    const totalSeconds = Math.ceil(remaining / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = String(totalSeconds % 60).padStart(2, "0");
    $("telegram-expiry").textContent = remaining > 0
      ? `Expiration dans ${minutes}:${seconds}`
      : "Ce code a expiré";
    if (remaining <= 0) {
      stopTelegramPolling();
      showAlert(
        $("telegram-code-error"),
        "Le code a expiré. Générez-en un nouveau pour poursuivre la connexion.",
      );
    }
  }

  async function pollTelegramStatus() {
    if (state.telegramPollInFlight || !state.token) return;
    const requestToken = state.token;
    state.telegramPollInFlight = true;
    try {
      const response = await apiRequest(API.telegramStatus);
      if (!state.token || state.token !== requestToken) return;
      state.integrations.telegramAvailable = response.available !== false;
      if (response.connected) {
        state.integrations.telegram = true;
        state.integrations.telegramError = false;
        updateIntegrationUI();
        showToast("Telegram est maintenant connecté à SmartReply.");
      }
    } catch (error) {
      if (error.status !== 401) {
        showAlert(
          $("telegram-code-error"),
          "La vérification automatique est momentanément indisponible. Elle va réessayer.",
        );
      }
    } finally {
      state.telegramPollInFlight = false;
    }
  }

  function currentRoute() {
    const route = window.location.hash.replace(/^#/, "");
    if (route.startsWith("emails/")) return "emails";
    return APP_ROUTES.includes(route) ? route : "dashboard";
  }

  function renderRoute() {
    if (!state.appReady) return;
    const route = currentRoute();
    const detailId = route === "emails" ? emailDetailIdFromHash() : null;
    $("dashboard-view").hidden = route !== "dashboard";
    $("emails-view").hidden = route !== "emails";
    $("integrations-view").hidden = route !== "integrations";
    $("settings-view").hidden = route !== "settings";
    $("topbar-title").textContent = ROUTE_TITLES[route];
    document.querySelectorAll("[data-route]").forEach((item) => {
      item.classList.toggle("active", item.dataset.route === route);
      if (item.dataset.route === route) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
    closeSidebar();
    if (route !== "emails" || detailId !== null) {
      cancelEmailSearchDebounce();
      restoreActiveEmailCriteriaControls();
    }
    if (route === "emails" && detailId !== null) {
      showEmailDetailWorkspace(detailId);
    } else {
      showEmailListWorkspace();
    }
    if (
      route === "emails"
      && detailId === null
      && !state.emailList.loaded
      && !state.emailList.loading
    ) {
      void loadEmailsPage(1);
    }
    if (route === "integrations") {
      checkIntegrationStatuses({ silent: true });
    }
    window.setTimeout(() => $("main-content").focus({ preventScroll: true }), 0);
  }

  function openSidebar() {
    $("sidebar").classList.add("open");
    $("sidebar-overlay").classList.add("visible");
    $("open-sidebar").setAttribute("aria-expanded", "true");
    $("close-sidebar").focus();
  }

  function closeSidebar() {
    $("sidebar").classList.remove("open");
    $("sidebar-overlay").classList.remove("visible");
    $("open-sidebar").setAttribute("aria-expanded", "false");
  }

  function handleGmailReturn() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("gmail") !== "connected") return;
    if (window.location.hash !== "#integrations") {
      window.location.hash = "integrations";
    }
    window.history.replaceState(null, "", "/dashboard/#integrations");
    showToast("Gmail a été connecté avec succès.");
    checkIntegrationStatuses({ silent: true });
  }

  function handleDriveReturn() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("drive") !== "connected") return;
    const target = state.company ? "/dashboard/#integrations" : "/dashboard/";
    window.history.replaceState(null, "", target);
    showToast("Google Drive a été connecté avec succès.");
    if (state.company) {
      checkIntegrationStatuses({ silent: true });
    } else {
      refreshOnboardingDriveStatus();
    }
  }

  function bindEvents() {
    $("login-tab").addEventListener("click", () => switchAuth("login"));
    $("register-tab").addEventListener("click", () => switchAuth("register"));
    $("open-register").addEventListener("click", () => switchAuth("register"));
    $("open-login").addEventListener("click", () => switchAuth("login"));
    $("login-form").addEventListener("submit", handleLogin);
    $("register-form").addEventListener("submit", handleRegister);
    $("company-form").addEventListener("submit", handleCompanyCreation);
    $("company-sector").addEventListener("change", () => {
      syncOtherSectorField(
        $("company-sector"),
        $("company-sector-other-field"),
        $("company-sector-other"),
      );
    });
    $("settings-form").addEventListener("submit", handleSettingsSave);
    $("settings-sector").addEventListener("change", () => {
      syncOtherSectorField(
        $("settings-sector"),
        $("settings-sector-other-field"),
        $("settings-sector-other"),
      );
    });
    $("connect-drive-onboarding").addEventListener("click", (event) => connectDrive(event.currentTarget));
    $("onboarding-logout").addEventListener("click", () => logout());
    $("logout-button").addEventListener("click", () => logout());
    $("settings-logout").addEventListener("click", () => logout());
    $("open-sidebar").addEventListener("click", openSidebar);
    $("close-sidebar").addEventListener("click", closeSidebar);
    $("sidebar-overlay").addEventListener("click", closeSidebar);
    $("connect-gmail").addEventListener("click", connectGmail);
    $("connect-drive").addEventListener("click", (event) => connectDrive(event.currentTarget));
    $("repair-sheet-access").addEventListener("click", repairSheetAccess);
    $("disconnect-drive").addEventListener("click", disconnectDrive);
    $("refresh-dashboard").addEventListener("click", refreshDashboard);
    emailsViewElements.search.addEventListener("input", scheduleEmailSearch);
    emailsViewElements.category.addEventListener("change", applyEmailFilters);
    emailsViewElements.priority.addEventListener("change", applyEmailFilters);
    emailsViewElements.status.addEventListener("change", applyEmailFilters);
    emailsViewElements.retry.addEventListener("click", retryEmailsPage);
    emailsViewElements.previousPage.addEventListener("click", loadPreviousEmailsPage);
    emailsViewElements.nextPage.addEventListener("click", loadNextEmailsPage);
    emailsViewElements.detailBack.addEventListener("click", returnToEmailList);
    $("refresh-integrations").addEventListener("click", refreshIntegrations);
    $("generate-telegram-code").addEventListener("click", generateTelegramCode);
    $("copy-telegram-code").addEventListener("click", copyTelegramCode);
    $("dismiss-integration-setup").addEventListener("click", dismissIntegrationSetupModal);
    $("configure-integrations").addEventListener("click", navigateToIntegrationSetup);
    $("integration-setup-dialog").addEventListener("cancel", (event) => {
      event.preventDefault();
      dismissIntegrationSetupModal();
    });
    $("integration-setup-dialog").addEventListener("click", (event) => {
      if (event.target === event.currentTarget) dismissIntegrationSetupModal();
    });
    window.addEventListener("hashchange", renderRoute);
    window.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeSidebar();
    });
  }

  async function initialize() {
    cacheEmailsViewElements();
    bindEvents();
    if (!state.token) {
      showScreen("auth-screen");
      switchAuth("login");
      return;
    }
    await bootstrapAuthenticatedApp();
  }

  document.addEventListener("DOMContentLoaded", initialize);
})();

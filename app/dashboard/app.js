(() => {
  "use strict";

  const API = Object.freeze({
    register: "/api/v1/auth/register",
    login: "/api/v1/auth/login",
    company: "/api/v1/companies/me",
    companies: "/api/v1/companies",
    gmailConnect: "/api/v1/gmail/connect",
    gmailStatus: "/api/v1/gmail/status",
    telegramCode: "/api/v1/telegram/link-code",
    telegramStatus: "/api/v1/telegram/status",
    dashboardOverview: "/api/v1/dashboard/overview",
    recentEmails: "/api/v1/dashboard/recent-emails",
  });

  const TOKEN_STORAGE_KEY = "smartreply_access_token";
  const TELEGRAM_POLL_INTERVAL_MS = 4000;
  const SCREEN_IDS = [
    "loading-screen",
    "auth-screen",
    "company-screen",
    "dashboard-shell",
  ];
  const ALL_INTEGRATIONS = Object.freeze(["gmail", "sheet", "telegram"]);
  const REQUIRED_INTEGRATIONS = Object.freeze(["gmail", "sheet"]);

  const state = {
    token: readStoredToken(),
    user: null,
    company: null,
    appReady: false,
    integrations: {
      gmail: false,
      sheet: false,
      telegram: false,
      gmailError: false,
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
  };

  class ApiError extends Error {
    constructor(status, message) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }

  const $ = (id) => document.getElementById(id);
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
    state.integrationSetup = {
      dismissed: false,
      missing: [],
    };
  }

  function clearSession() {
    stopTelegramPolling();
    closeIntegrationSetupModal();
    removeStoredToken();
    state.token = null;
    state.user = null;
    state.company = null;
    state.appReady = false;
    state.integrations = {
      gmail: false,
      sheet: false,
      telegram: false,
      gmailError: false,
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

  async function handleCompanyCreation(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const button = form.querySelector('button[type="submit"]');
    const errorElement = $("company-error");
    showAlert(errorElement, "");
    setBusy(button, true, "Préparation du Sheet…");
    try {
      state.company = await apiRequest(API.companies, {
        method: "POST",
        body: {
          nom_entreprise: $("company-name").value.trim(),
          secteur: $("company-sector").value.trim(),
        },
      });
      form.reset();
      await enterDashboard();
      showToast("Votre espace d'entreprise est prêt.");
    } catch (error) {
      let message = error.message || "Création de l'entreprise impossible.";
      if (error.status === 409) {
        message = "Une entreprise est déjà associée à ce compte. Rechargez la page pour ouvrir votre espace.";
      } else if (error.status === 502 || error.status === 503) {
        message = describeGoogleProblem(error);
      }
      showAlert(errorElement, message);
    } finally {
      setBusy(button, false);
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
    setStatus($("sheet-status"), integrations.sheet);
    setStatus(
      $("telegram-status"),
      integrations.telegram,
      !integrations.telegram && telegramUnavailable,
    );
    setIntegrationConfigurationHighlights(
      integrations.statusesAvailable ? display.missingRequired : [],
    );

    $("dashboard-connected-count").textContent = display.countLabel;
    $("integrations-progress").textContent = display.countLabel;

    const banner = $("readiness-banner");
    banner.classList.toggle("ready", display.ready);
    if (!integrations.statusesAvailable) {
      $("readiness-title").textContent = "Statut Gmail temporairement indisponible";
      $("readiness-text").textContent = "Google Sheets reste configuré, mais le statut Gmail n'a pas pu être confirmé. Réessayez dans quelques instants.";
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

  function applyIntegrationStatusResults(gmailResult, telegramResult) {
    const errors = [];
    const statusesAvailable = gmailResult.status === "fulfilled";
    state.integrations.statusesAvailable = statusesAvailable;

    if (gmailResult.status === "fulfilled") {
      state.integrations.gmail = Boolean(gmailResult.value.connected);
    } else if (gmailResult.reason.status !== 401) {
      state.integrations.gmail = false;
      state.integrations.gmailError = true;
      errors.push("Gmail");
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
    state.integrations.sheet = Boolean(state.company && state.company.sheet_id);
    state.integrations.gmailError = false;
    state.integrations.telegramError = false;
    state.integrations.telegramAvailable = null;
    state.integrations.statusesAvailable = false;

    const [gmailResult, telegramResult] = await Promise.allSettled([
      apiRequest(API.gmailStatus),
      apiRequest(API.telegramStatus),
    ]);
    const { errors, statusesAvailable } = applyIntegrationStatusResults(
      gmailResult,
      telegramResult,
    );

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
    if (showLoading) setDashboardLoading();
    const [overviewResult, emailsResult] = await Promise.allSettled([
      apiRequest(API.dashboardOverview),
      apiRequest(API.recentEmails),
    ]);
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
    showAlert($("telegram-code-error"), "");
    showAlert($("integrations-error"), "");
    setBusy(button, true, "Génération…");
    try {
      const response = await apiRequest(API.telegramCode, { method: "POST" });
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
    state.telegramPollInFlight = true;
    try {
      const response = await apiRequest(API.telegramStatus);
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
    return window.location.hash === "#integrations" ? "integrations" : "dashboard";
  }

  function renderRoute() {
    if (!state.appReady) return;
    const route = currentRoute();
    $("dashboard-view").hidden = route !== "dashboard";
    $("integrations-view").hidden = route !== "integrations";
    $("topbar-title").textContent = route === "dashboard" ? "Tableau de bord" : "Intégrations";
    document.querySelectorAll("[data-route]").forEach((item) => {
      item.classList.toggle("active", item.dataset.route === route);
      if (item.dataset.route === route) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
    closeSidebar();
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

  function bindEvents() {
    $("login-tab").addEventListener("click", () => switchAuth("login"));
    $("register-tab").addEventListener("click", () => switchAuth("register"));
    $("open-register").addEventListener("click", () => switchAuth("register"));
    $("open-login").addEventListener("click", () => switchAuth("login"));
    $("login-form").addEventListener("submit", handleLogin);
    $("register-form").addEventListener("submit", handleRegister);
    $("company-form").addEventListener("submit", handleCompanyCreation);
    $("onboarding-logout").addEventListener("click", () => logout());
    $("logout-button").addEventListener("click", () => logout());
    $("open-sidebar").addEventListener("click", openSidebar);
    $("close-sidebar").addEventListener("click", closeSidebar);
    $("sidebar-overlay").addEventListener("click", closeSidebar);
    $("connect-gmail").addEventListener("click", connectGmail);
    $("refresh-dashboard").addEventListener("click", refreshDashboard);
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

"""Tests JavaScript du parcours de vérification des brouillons."""

import json
import subprocess
import unittest
from pathlib import Path


class DashboardDraftsUiTests(unittest.TestCase):
    def test_review_copy_and_confirmed_ignore_never_send(self):
        repository_root = Path(__file__).resolve().parents[1]
        script = r"""
const fs = require("fs");
const vm = require("vm");
let source = fs.readFileSync("app/dashboard/app.js", "utf8");
const marker = '  document.addEventListener("DOMContentLoaded", initialize);\n})();';
if (!source.includes(marker)) throw new Error("Dashboard test export marker missing");
source = source.replace(
  marker,
  `  globalThis.__draftsUiTest = {
    cacheDraftsViewElements,
    confirmIgnoreDraft,
    copyDraftContent,
    loadDraftDetail,
    loadDraftsPage,
    openDraftIgnoreDialog,
    state,
  };\n})();`,
);

const innerHtmlValues = [];
class TestElement {
  constructor(tagName = "div") {
    this.tagName = tagName;
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.dataset = {};
    this.children = [];
    this.attributes = {};
    this.open = false;
    this.isConnected = true;
    this._textContent = "";
    this._innerHTML = "";
    this.className = "";
    this.classList = {
      add() {},
      remove() {},
      toggle() {},
    };
  }
  set textContent(value) { this._textContent = String(value); }
  get textContent() { return this._textContent; }
  set innerHTML(value) {
    this._innerHTML = String(value);
    innerHtmlValues.push(this._innerHTML);
  }
  get innerHTML() { return this._innerHTML; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) {
    this.children = [...children];
    this._textContent = "";
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener() {}
  focus() {}
  select() {}
  remove() { this.isConnected = false; }
  showModal() { this.open = true; }
  close() { this.open = false; }
}

const elements = new Map();
const element = (id) => {
  if (!elements.has(id)) elements.set(id, new TestElement());
  return elements.get(id);
};
const body = new TestElement("body");
let copiedText = null;
let fetchHandler = null;
const requests = [];
const context = {
  window: {
    localStorage: {
      getItem: () => null,
      setItem() {},
      removeItem() {},
    },
    location: { hash: "#drafts" },
    history: { replaceState() {} },
    fetch: async (path, options) => {
      requests.push({ path, options });
      return fetchHandler(path, options);
    },
    setTimeout: () => 1,
    clearTimeout() {},
    clearInterval() {},
    isSecureContext: true,
  },
  navigator: {
    clipboard: {
      writeText: async (value) => { copiedText = value; },
    },
  },
  document: {
    body,
    getElementById: (id) => element(id),
    createElement: (tagName) => new TestElement(tagName),
    querySelectorAll: () => [],
    execCommand: () => true,
  },
  console,
  Intl,
  Set,
  URLSearchParams,
};
const response = (status, payload) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: { get: () => "application/json" },
  json: async () => payload,
  text: async () => JSON.stringify(payload),
});
const pendingDetail = {
  draft_ref: "BR-41",
  recipient_name: "<img src=x onerror=alert(1)>",
  recipient_email: "client@example.test",
  subject: "<script>unsafe subject</script>",
  received_at: "2026-10-30T12:00:00Z",
  prepared_at: "2026-10-30T12:05:00Z",
  summary: "Résumé contrôlé",
  category: "Support",
  priority: "Haute",
  status: "En attente",
  content: "<img src=x onerror=alert(2)>\nRéponse proposée",
  ai_generated: true,
};
const listPayload = (status = "En attente") => ({
  items: [{
    draft_ref: "BR-41",
    recipient_name: pendingDetail.recipient_name,
    recipient_email: pendingDetail.recipient_email,
    subject: pendingDetail.subject,
    received_at: pendingDetail.received_at,
    prepared_at: pendingDetail.prepared_at,
    summary: pendingDetail.summary,
    category: pendingDetail.category,
    priority: pendingDetail.priority,
    status,
  }],
  pagination: { page: 1, page_size: 25, total_items: 1, total_pages: 1 },
  metrics: {
    pending: status === "En attente" ? 1 : 0,
    sent: 0,
    ignored: status === "Ignoré" ? 1 : 0,
    errors: 0,
  },
});

vm.createContext(context);
vm.runInContext(source, context);
const subject = context.__draftsUiTest;
subject.cacheDraftsViewElements();
subject.state.token = "test-session-token";

(async () => {
  fetchHandler = async (path) => {
    if (path.includes("?")) return response(200, listPayload());
    return response(200, pendingDetail);
  };
  await subject.loadDraftsPage(1);
  await subject.loadDraftDetail("BR-41");
  const row = element("drafts-table-body").children[0];
  const beforeAction = {
    requestMethods: requests.map(({ options }) => options.method),
    bearerSent: requests.every(({ options }) => (
      options.headers.Authorization === "Bearer test-session-token"
    )),
    recipient: row.children[0].children[0].textContent,
    subject: row.children[1].children[0].textContent,
    content: element("draft-content-preview").textContent,
    status: element("draft-detail-status").textContent,
    ignoreVisible: !element("ignore-draft").hidden,
    noUnsafeInnerHtml: !innerHtmlValues.some((value) => (
      value.includes("unsafe subject") || value.includes("onerror=alert")
    )),
  };

  await subject.copyDraftContent();
  const requestsAfterCopy = requests.length;
  subject.openDraftIgnoreDialog();
  const dialogOpenBeforeConfirmation = element("draft-ignore-dialog").open;
  const requestsBeforeConfirmation = requests.length;

  fetchHandler = async (path, options) => {
    if (path.endsWith("/ignore") && options.method === "POST") {
      return response(200, {
        draft_ref: "BR-41",
        status: "Ignoré",
        sheet_sync_pending: false,
      });
    }
    if (path.includes("?")) return response(200, listPayload("Ignoré"));
    return response(200, { ...pendingDetail, status: "Ignoré" });
  };
  await subject.confirmIgnoreDraft();
  const afterIgnore = {
    dialogClosed: !element("draft-ignore-dialog").open,
    status: element("draft-detail-status").textContent,
    ignoreHidden: element("ignore-draft").hidden,
    methods: requests.map(({ options }) => options.method),
    paths: requests.map(({ path }) => path),
  };

  process.stdout.write(JSON.stringify({
    beforeAction,
    copiedText,
    requestsAfterCopy,
    dialogOpenBeforeConfirmation,
    requestsBeforeConfirmation,
    afterIgnore,
  }));
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)

        self.assertEqual(result["beforeAction"]["requestMethods"], ["GET", "GET"])
        self.assertTrue(result["beforeAction"]["bearerSent"])
        self.assertEqual(
            result["beforeAction"]["recipient"],
            "<img src=x onerror=alert(1)>",
        )
        self.assertEqual(
            result["beforeAction"]["subject"],
            "<script>unsafe subject</script>",
        )
        self.assertEqual(
            result["beforeAction"]["content"],
            "<img src=x onerror=alert(2)>\nRéponse proposée",
        )
        self.assertEqual(result["beforeAction"]["status"], "En attente")
        self.assertTrue(result["beforeAction"]["ignoreVisible"])
        self.assertTrue(result["beforeAction"]["noUnsafeInnerHtml"])
        self.assertEqual(result["copiedText"], result["beforeAction"]["content"])
        self.assertEqual(result["requestsAfterCopy"], 2)
        self.assertTrue(result["dialogOpenBeforeConfirmation"])
        self.assertEqual(result["requestsBeforeConfirmation"], 2)
        self.assertTrue(result["afterIgnore"]["dialogClosed"])
        self.assertEqual(result["afterIgnore"]["status"], "Ignoré")
        self.assertTrue(result["afterIgnore"]["ignoreHidden"])
        self.assertEqual(result["afterIgnore"]["methods"].count("POST"), 1)
        self.assertTrue(result["afterIgnore"]["paths"][2].endswith("/ignore"))
        self.assertFalse(
            any("send" in path.casefold() for path in result["afterIgnore"]["paths"])
        )


if __name__ == "__main__":
    unittest.main()

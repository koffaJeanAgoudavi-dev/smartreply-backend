(() => {
  "use strict";

  const form = document.getElementById("contact-form");
  const error = document.getElementById("contact-error");
  const success = document.getElementById("contact-success");
  const submit = document.getElementById("contact-submit");
  const submitLabel = submit.querySelector("span");

  const setSubmitting = (active) => {
    submit.disabled = active;
    form.setAttribute("aria-busy", String(active));
    submitLabel.textContent = active ? "Envoi en cours…" : "Envoyer le message";
  };

  const showError = (message) => {
    error.textContent = message;
    error.hidden = false;
    error.focus();
  };

  const errorMessageFor = (status) => {
    if (status === 422) {
      return "Vérifiez les informations saisies avant de réessayer.";
    }
    if (status === 429) {
      return "Trop de demandes ont été envoyées. Veuillez réessayer plus tard.";
    }
    return "Votre message n’a pas pu être envoyé. Veuillez réessayer dans quelques instants.";
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hidden = true;
    success.hidden = true;

    if (!form.reportValidity()) {
      return;
    }

    const values = new FormData(form);
    const payload = {
      full_name: values.get("full_name"),
      email: values.get("email"),
      subject: values.get("subject"),
      reason: values.get("reason"),
      message: values.get("message"),
    };

    setSubmitting(true);
    try {
      const response = await fetch("/api/v1/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        showError(errorMessageFor(response.status));
        return;
      }
      const result = await response.json();
      if (result.status !== "sent") {
        showError(errorMessageFor(503));
        return;
      }

      form.reset();
      form.hidden = true;
      success.hidden = false;
      success.focus();
    } catch (_error) {
      showError(errorMessageFor(503));
    } finally {
      setSubmitting(false);
    }
  });
})();

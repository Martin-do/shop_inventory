(() => {
  const forms = document.querySelectorAll("form[data-live-search]");
  if (!forms.length) return;

  forms.forEach((form) => {
    const selector = form.dataset.liveTarget;
    const target = selector ? document.querySelector(selector) : null;
    if (!target) return;

    let timer = null;
    let controller = null;
    const delay = Number(form.dataset.liveDelay || 180);

    const ensureStatus = () => {
      let status = form.querySelector("[data-live-search-status]");
      if (status) return status;
      status = document.createElement("span");
      status.dataset.liveSearchStatus = "";
      status.setAttribute("aria-live", "polite");
      status.style.cssText = "font-size:12px;color:var(--muted);align-self:end;";
      form.appendChild(status);
      return status;
    };

    const status = ensureStatus();

    const buildUrl = () => {
      const url = new URL(form.action || window.location.href, window.location.href);
      const params = new URLSearchParams(new FormData(form));
      params.delete("page");
      url.search = params.toString();
      return url;
    };

    const run = async () => {
      if (controller) controller.abort();
      controller = new AbortController();
      const url = buildUrl();

      target.setAttribute("aria-busy", "true");
      status.textContent = "Searching…";

      try {
        const response = await fetch(url, {
          headers: {"X-Requested-With": "XMLHttpRequest"},
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("Search request failed");

        const html = await response.text();
        const doc = new DOMParser().parseFromString(html, "text/html");
        const incoming = doc.querySelector(selector);
        if (!incoming) throw new Error("Search results container missing");

        target.innerHTML = incoming.innerHTML;
        history.replaceState(null, "", url.pathname + (url.search ? url.search : ""));
        status.textContent = "";
      } catch (error) {
        if (error.name !== "AbortError") {
          status.textContent = "Could not refresh results. Press Search to retry.";
        }
      } finally {
        target.removeAttribute("aria-busy");
      }
    };

    const schedule = (immediate = false) => {
      clearTimeout(timer);
      if (immediate) run();
      else timer = setTimeout(run, delay);
    };

    form.addEventListener("input", (event) => {
      if (event.target.matches("input, textarea")) schedule(false);
    });

    form.addEventListener("change", (event) => {
      if (event.target.matches("select, input[type=checkbox], input[type=radio]")) schedule(true);
    });

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      schedule(true);
    });
  });
})();

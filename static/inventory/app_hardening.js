(() => {
  "use strict";

  const cleanText = (value) => String(value ?? "").replace(/[<>`]/g, "").slice(0, 240);

  function showToast(message, type = "info") {
    let container = document.getElementById("app-toast-container");
    if (!container) {
      container = document.createElement("div");
      container.id = "app-toast-container";
      Object.assign(container.style, {
        position: "fixed", top: "16px", right: "16px", zIndex: "3000",
        display: "flex", flexDirection: "column", gap: "8px", maxWidth: "360px"
      });
      document.body.appendChild(container);
    }
    const toast = document.createElement("div");
    toast.setAttribute("role", "status");
    toast.textContent = String(message);
    Object.assign(toast.style, {
      padding: "12px 14px", borderRadius: "8px", boxShadow: "0 8px 24px rgba(0,0,0,.18)",
      background: type === "error" ? "#b42318" : type === "success" ? "#176b5c" : "#1f2937",
      color: "white", fontWeight: "700", fontFamily: "system-ui, sans-serif"
    });
    container.appendChild(toast);
    window.setTimeout(() => toast.remove(), 4500);
  }

  window.shopToast = showToast;
  window.alert = (message) => showToast(
    message,
    /error|failed|not found|insufficient|cannot|only /i.test(String(message)) ? "error" : "info"
  );

  function sanitizeCachedCart() {
    const key = "pos_active_cart";
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return;
      const cart = JSON.parse(raw);
      if (!Array.isArray(cart)) return;
      const sanitized = cart.map((item) => ({
        ...item,
        name: cleanText(item.name),
        variant: cleanText(item.variant),
        barcode: cleanText(item.barcode),
        image_url: /^(https?:\/\/|\/(?!\/))/i.test(String(item.image_url || "")) ? item.image_url : null
      }));
      const next = JSON.stringify(sanitized);
      if (next !== raw && sessionStorage.getItem("cart_sanitized_reload") !== "1") {
        localStorage.setItem(key, next);
        sessionStorage.setItem("cart_sanitized_reload", "1");
        window.location.reload();
      } else {
        sessionStorage.removeItem("cart_sanitized_reload");
      }
    } catch (_) {
      localStorage.removeItem(key);
    }
  }

  function hardenPOS() {
    const barcodeInput = document.getElementById("barcode-input");
    const checkoutForm = document.getElementById("checkout-form");
    const confirmButton = document.getElementById("btn-modal-confirm");
    if (!barcodeInput) return;

    let scanLocked = false;
    const originalAdd = window.addToCartFromObj;
    if (typeof originalAdd === "function") {
      window.addToCartFromObj = function(product, qty) {
        if (scanLocked) return;
        scanLocked = true;
        const safeProduct = {
          ...product,
          name: cleanText(product?.name),
          variant: cleanText(product?.variant),
          barcode: cleanText(product?.barcode),
          image_url: /^(https?:\/\/|\/(?!\/))/i.test(String(product?.image_url || "")) ? product.image_url : null
        };
        try {
          originalAdd(safeProduct, qty);
        } finally {
          window.setTimeout(() => { scanLocked = false; }, 350);
        }
      };
    }

    if (checkoutForm) {
      checkoutForm.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          event.stopPropagation();
          showToast("Use Complete Sale, then confirm the transaction.", "info");
        }
      }, true);
    }

    if (confirmButton) {
      confirmButton.addEventListener("click", (event) => {
        if (confirmButton.dataset.submitting === "1") {
          event.preventDefault();
          event.stopImmediatePropagation();
          return;
        }
        confirmButton.dataset.submitting = "1";
        confirmButton.disabled = true;
        confirmButton.textContent = "Submitting…";
        window.setTimeout(() => {
          if (document.body.contains(confirmButton)) {
            confirmButton.dataset.submitting = "0";
            confirmButton.disabled = false;
            confirmButton.textContent = "Confirm & Submit";
          }
        }, 12000);
      }, true);
    }

    if (typeof window.renderSuggestions === "function") {
      window.renderSuggestions = function(results) {
        const container = document.getElementById("search-suggestions");
        if (!container) return;
        container.replaceChildren();
        if (!Array.isArray(results) || results.length === 0) {
          container.style.display = "none";
          return;
        }
        results.forEach((product) => {
          const row = document.createElement("div");
          Object.assign(row.style, {
            padding: "10px 14px", borderBottom: "1px solid var(--line)", cursor: "pointer",
            display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "15px"
          });
          const details = document.createElement("div");
          const name = document.createElement("strong");
          name.textContent = cleanText(product.name) || "Unnamed product";
          details.appendChild(name);
          if (product.variant) {
            const variant = document.createElement("span");
            variant.textContent = ` ${cleanText(product.variant)}`;
            variant.style.marginLeft = "6px";
            details.appendChild(variant);
          }
          const meta = document.createElement("small");
          meta.style.display = "block";
          meta.style.marginTop = "4px";
          meta.textContent = `Barcode: ${cleanText(product.barcode) || "-"} | Price: ₦${Number(product.price || 0).toLocaleString()} | Stock: ${product.stock ?? 0}`;
          details.appendChild(meta);
          const button = document.createElement("button");
          button.type = "button";
          button.className = "button primary";
          button.textContent = "Select";
          row.append(details, button);
          row.addEventListener("click", () => {
            const qty = parseInt(document.getElementById("pos-quantity-input")?.value, 10) || 1;
            window.addToCartFromObj(product, qty);
            barcodeInput.value = "";
            container.style.display = "none";
            barcodeInput.focus();
          });
          container.appendChild(row);
        });
        container.style.display = "block";
      };
    }
  }

  sanitizeCachedCart();
  document.addEventListener("DOMContentLoaded", hardenPOS);
})();

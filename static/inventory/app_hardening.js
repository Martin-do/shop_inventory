(() => {
  "use strict";

  function showToast(message, type = "info") {
    let container = document.getElementById("app-toast-container");
    if (!container) {
      container = document.createElement("div");
      container.id = "app-toast-container";
      Object.assign(container.style, {
        position: "fixed",
        top: "16px",
        right: "16px",
        zIndex: "3000",
        display: "flex",
        flexDirection: "column",
        gap: "8px",
        maxWidth: "360px"
      });
      document.body.appendChild(container);
    }

    const toast = document.createElement("div");
    toast.setAttribute("role", "status");
    toast.textContent = String(message);
    Object.assign(toast.style, {
      padding: "12px 14px",
      borderRadius: "8px",
      boxShadow: "0 8px 24px rgba(0,0,0,.18)",
      background: type === "error" ? "#b42318" : type === "success" ? "#176b5c" : "#1f2937",
      color: "white",
      fontWeight: "700",
      fontFamily: "system-ui, sans-serif"
    });
    container.appendChild(toast);
    window.setTimeout(() => toast.remove(), 4500);
  }

  window.shopToast = showToast;
  window.alert = (message) => showToast(message, /error|failed|not found|insufficient|cannot|only /i.test(String(message)) ? "error" : "info");

  function textCell(text, styles = {}) {
    const td = document.createElement("td");
    td.textContent = text;
    Object.assign(td.style, styles);
    return td;
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
        try {
          originalAdd(product, qty);
        } finally {
          window.setTimeout(() => { scanLocked = false; }, 350);
        }
      };
    }

    if (checkoutForm) {
      checkoutForm.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          showToast("Use Complete Sale, then confirm the transaction.", "info");
        }
      }, true);
    }

    if (confirmButton) {
      confirmButton.addEventListener("click", () => {
        if (confirmButton.dataset.submitting === "1") return;
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
            padding: "10px 14px",
            borderBottom: "1px solid var(--line)",
            cursor: "pointer",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            fontSize: "15px"
          });

          const details = document.createElement("div");
          const name = document.createElement("strong");
          name.textContent = product.name || "Unnamed product";
          details.appendChild(name);
          if (product.variant) {
            const variant = document.createElement("span");
            variant.textContent = ` ${product.variant}`;
            variant.style.marginLeft = "6px";
            details.appendChild(variant);
          }
          const meta = document.createElement("small");
          meta.style.display = "block";
          meta.style.marginTop = "4px";
          meta.textContent = `Barcode: ${product.barcode || "-"} | Price: ₦${Number(product.price || 0).toLocaleString()} | Stock: ${product.stock ?? 0}`;
          details.appendChild(meta);

          const button = document.createElement("button");
          button.type = "button";
          button.className = "button primary";
          button.textContent = "Select";
          button.style.padding = "4px 10px";

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

    if (typeof window.renderCart === "function") {
      window.renderCart = function() {
        const body = document.getElementById("cart-table-body");
        if (!body) return;
        body.replaceChildren();
        if (!Array.isArray(window.localCart) || window.localCart.length === 0) {
          const tr = document.createElement("tr");
          const td = textCell("Scan an item to begin.");
          td.colSpan = 5;
          tr.appendChild(td);
          body.appendChild(tr);
          if (typeof window.updateCheckoutSummary === "function") window.updateCheckoutSummary();
          return;
        }

        window.localCart.forEach((item, index) => {
          const tr = document.createElement("tr");
          const imageCell = document.createElement("td");
          if (item.image_url && /^\/(?!\/)|^https?:\/\//i.test(item.image_url)) {
            const img = document.createElement("img");
            img.src = item.image_url;
            img.alt = item.name || "Product";
            Object.assign(img.style, { width: "50px", height: "50px", objectFit: "cover", borderRadius: "6px" });
            imageCell.appendChild(img);
          } else {
            imageCell.textContent = "📦";
          }

          const itemCell = document.createElement("td");
          const itemName = document.createElement("strong");
          itemName.textContent = item.name || "Unnamed product";
          itemCell.appendChild(itemName);
          if (item.variant) {
            const variant = document.createElement("small");
            variant.textContent = ` (${item.variant})`;
            itemCell.appendChild(variant);
          }
          const barcode = document.createElement("small");
          barcode.style.display = "block";
          barcode.textContent = item.barcode || "";
          itemCell.appendChild(barcode);

          const qtyCell = document.createElement("td");
          qtyCell.style.textAlign = "center";
          const minus = document.createElement("button");
          minus.type = "button";
          minus.className = "button";
          minus.textContent = "−";
          minus.addEventListener("click", () => window.updateCartQty(index, -1));
          const amount = document.createElement("span");
          amount.textContent = String(item.quantity);
          amount.style.margin = "0 8px";
          const plus = document.createElement("button");
          plus.type = "button";
          plus.className = "button";
          plus.textContent = "+";
          plus.addEventListener("click", () => window.updateCartQty(index, 1));
          qtyCell.append(minus, amount, plus);

          const totalCell = textCell(`₦${(Number(item.price) * Number(item.quantity)).toLocaleString("en-US", {minimumFractionDigits: 2})}`);
          const removeCell = document.createElement("td");
          const remove = document.createElement("button");
          remove.type = "button";
          remove.className = "link-button";
          remove.textContent = "Remove";
          remove.addEventListener("click", () => window.removeFromCart(index));
          removeCell.appendChild(remove);

          tr.append(imageCell, itemCell, qtyCell, totalCell, removeCell);
          body.appendChild(tr);
        });
        if (typeof window.updateCheckoutSummary === "function") window.updateCheckoutSummary();
      };
      window.renderCart();
    }
  }

  document.addEventListener("DOMContentLoaded", hardenPOS);
})();

document.addEventListener("DOMContentLoaded", () => {
  const currentToggle = document.querySelector("[data-current-action-toggle]");
  const secondaryActions = document.querySelector("#current-action-secondary");
  const currentLabel = currentToggle?.querySelector("[data-current-action-label]");
  if (currentToggle && secondaryActions && currentLabel) {
    currentToggle.addEventListener("click", () => {
      const expanded = currentToggle.getAttribute("aria-expanded") === "true";
      currentToggle.setAttribute("aria-expanded", String(!expanded));
      secondaryActions.hidden = expanded;
      currentLabel.textContent = expanded ? "查看全部" : "收起";
    });
  }

  const notifyToggle = document.querySelector("#notify-different");
  const notifyFields = document.querySelector("#notify-fields");
  const notifyValue = document.querySelector('input[name="notify_party_same_as_consignee"]');
  const updateNotifyParty = () => {
    if (!notifyToggle || !notifyFields || !notifyValue) return;
    notifyFields.classList.toggle("d-none", !notifyToggle.checked);
    notifyValue.value = notifyToggle.checked ? "false" : "true";
  };
  notifyToggle?.addEventListener("change", updateNotifyParty);

  const navigation = document.querySelector(".order-nav");
  const navigationLinks = navigation ? [...navigation.querySelectorAll('a[href^="#"]')] : [];
  const sections = navigationLinks
    .map((link) => document.querySelector(link.getAttribute("href")))
    .filter(Boolean);
  if ("IntersectionObserver" in window && sections.length) {
    const linkById = new Map(navigationLinks.map((link) => [link.hash.slice(1), link]));
    const observer = new IntersectionObserver((entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0];
      if (!visible) return;
      navigationLinks.forEach((link) => link.removeAttribute("aria-current"));
      linkById.get(visible.target.id)?.setAttribute("aria-current", "true");
    }, { rootMargin: "-15% 0px -70%", threshold: [0.05, 0.25, 0.5] });
    sections.forEach((section) => observer.observe(section));
  }

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const revealActivityFromHash = () => {
    if (window.location.hash !== "#activity") return;
    document.querySelector("#activity")?.setAttribute("open", "");
  };
  revealActivityFromHash();
  window.addEventListener("hashchange", revealActivityFromHash);

  const cargoEdit = document.querySelector("[data-cargo-edit]");
  const cargoWorkspace = document.querySelector("[data-cargo-workspace]");
  const shippingGroups = [...document.querySelectorAll("[data-shipping-group]")];
  const setCargoWorkspace = (expanded, { focus = false } = {}) => {
    if (!cargoEdit || !cargoWorkspace) return;
    cargoWorkspace.hidden = !expanded;
    cargoEdit.setAttribute("aria-expanded", String(expanded));
    cargoEdit.textContent = expanded ? "收起 / Collapse" : "编辑 / Edit";
    if (expanded && focus) cargoWorkspace.querySelector("input, select, textarea")?.focus();
  };
  const closeOtherShippingGroups = (current) => {
    shippingGroups.forEach((group) => {
      if (group !== current) group.removeAttribute("open");
    });
    if (current?.id !== "shipping-cargo") setCargoWorkspace(false);
  };
  shippingGroups.forEach((group) => {
    group.addEventListener("toggle", () => {
      if (group.open) {
        const closesOpenPeer = shippingGroups.some((peer) => peer !== group && peer.open);
        const beforeTop = closesOpenPeer ? group.getBoundingClientRect().top : null;
        closeOtherShippingGroups(group);
        if (beforeTop !== null) {
          const delta = group.getBoundingClientRect().top - beforeTop;
          if (delta) window.scrollBy({ top: delta, left: 0, behavior: "auto" });
        }
      }
      if (!group.open && group.id === "shipping-cargo") setCargoWorkspace(false);
    });
  });
  cargoEdit?.addEventListener("click", () => {
    const expanded = cargoEdit.getAttribute("aria-expanded") === "true";
    setCargoWorkspace(!expanded, { focus: !expanded });
  });

  const editorGroups = new Map(Object.entries(window.__v2OrderDetailEditors || {}));
  const orderId = document.querySelector("[data-v2-order-id]")?.dataset.v2OrderId;
  const restoreStorageKey = orderId ? `xhtpi:v2:order-detail:restore:${orderId}` : null;
  const capturePrgRestore = (editorId) => {
    const editor = editorGroups.get(editorId);
    const target = editor ? document.getElementById(editor.targetId) : null;
    if (!restoreStorageKey || !target) return;
    const state = {
      version: 1,
      editor: editorId,
      viewportOffset: Math.round(target.getBoundingClientRect().top),
      createdAt: Date.now(),
    };
    try { window.sessionStorage.setItem(restoreStorageKey, JSON.stringify(state)); } catch (_) { /* Normal PRG fallback remains available. */ }
  };
  document.getElementById("shipment-preparation")?.addEventListener("submit", (event) => {
    capturePrgRestore(event.submitter?.dataset.v2PrgEditor);
  });
  document.querySelectorAll("form[data-v2-prg-editor]").forEach((form) => {
    form.addEventListener("submit", () => capturePrgRestore(form.dataset.v2PrgEditor));
  });

  const transientRestore = window.__v2OrderDetailRestore;
  const openEditor = transientRestore?.editor || new URLSearchParams(window.location.search).get("open_editor");
  const editor = editorGroups.get(openEditor);
  if (editor) {
    const group = document.getElementById(editor.groupId);
    if (group) {
      closeOtherShippingGroups(group);
      group.setAttribute("open", "");
      if (editor.groupId === "shipping-cargo") setCargoWorkspace(true);
    }
    const target = document.getElementById(editor.targetId);
    if (target && transientRestore?.editor === openEditor) {
      window.scrollTo({
        top: Math.max(0, window.scrollY + target.getBoundingClientRect().top - transientRestore.viewportOffset),
        left: 0,
        behavior: "auto",
      });
    } else {
      target?.scrollIntoView({ behavior: "auto", block: "nearest" });
    }
    const url = new URL(window.location.href);
    url.searchParams.delete("open_editor");
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
    if ("scrollRestoration" in window.history) window.history.scrollRestoration = "auto";
  }

  document.querySelectorAll(".order-save-feedback").forEach((feedback) => {
    window.setTimeout(() => {
      feedback.classList.add("is-dismissing");
      window.setTimeout(() => feedback.remove(), 160);
    }, 1600);
  });

  const cargoInputNames = [
    "package_count", "package_unit", "gross_weight", "gross_weight_display_unit", "volume",
  ];
  const cargoDisplayNames = [
    "package_count", "package_unit", "gross_weight_kg", "gross_weight_display_unit", "volume_cbm",
  ];
  document.querySelectorAll("form[data-v2-inplace-save]").forEach((form) => {
    if (form.dataset.v2InplaceScope !== "CARGO") return;
    if (!("fetch" in window) || !("FormData" in window)) return;
    const status = form.querySelector("[data-v2-save-status]");
    const button = form.querySelector("[data-v2-save-button]");
    let clearStatusTimer;
    const showStatus = (message, state) => {
      if (!status) return;
      window.clearTimeout(clearStatusTimer);
      status.textContent = message;
      status.className = `v2-save-status is-${state}`;
      if (state === "success") {
        clearStatusTimer = window.setTimeout(() => {
          status.textContent = "";
          status.className = "v2-save-status";
        }, 1600);
      }
    };
    form.addEventListener("submit", async (event) => {
      if (form.dataset.v2Saving === "true") {
        event.preventDefault();
        return;
      }
      event.preventDefault();
      form.dataset.v2Saving = "true";
      const buttonLabel = button?.textContent;
      if (button) {
        button.disabled = true;
        button.textContent = "Saving…";
      }
      showStatus("Saving…", "saving");
      try {
        const response = await window.fetch(form.action, {
          method: form.method || "POST",
          body: new FormData(form, event.submitter),
          credentials: "same-origin",
          headers: { Accept: "application/json", "X-V2-Inplace-Save": "1" },
        });
        let payload = {};
        try { payload = await response.json(); } catch (_) { /* Non-JSON errors retain a safe local message. */ }
        if (!response.ok || !payload.ok) throw new Error(payload.error || "Could not save. Reload and try again.");
        cargoInputNames.forEach((name) => {
          if (Object.hasOwn(payload.fields || {}, name) && form.elements.namedItem(name)) {
            form.elements.namedItem(name).value = payload.fields[name];
          }
        });
        cargoDisplayNames.forEach((name) => {
          if (!Object.hasOwn(payload.display || {}, name)) return;
          document.querySelectorAll(`[data-v2-inplace-display="${name}"]`).forEach((element) => {
            element.textContent = payload.display[name];
          });
        });
        showStatus(payload.message || "Saved", "success");
      } catch (error) {
        showStatus(error.message || "Could not save. Reload and try again.", "error");
      } finally {
        form.dataset.v2Saving = "false";
        if (button) {
          button.disabled = false;
          button.textContent = buttonLabel;
        }
      }
    });
  });

  navigationLinks.forEach((link) => {
    link.addEventListener("click", (event) => {
      const target = document.querySelector(link.getAttribute("href"));
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
      window.history.replaceState(null, "", link.hash);
    });
  });
});

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
  cargoEdit?.addEventListener("click", () => {
    if (!cargoWorkspace) return;
    const expanded = cargoEdit.getAttribute("aria-expanded") === "true";
    cargoWorkspace.hidden = expanded;
    cargoEdit.setAttribute("aria-expanded", String(!expanded));
    cargoEdit.textContent = expanded ? "编辑 / Edit" : "收起 / Collapse";
    if (!expanded) cargoWorkspace.querySelector("input, select, textarea")?.focus();
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

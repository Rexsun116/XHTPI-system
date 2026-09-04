document.addEventListener("DOMContentLoaded", () => {
  const match = window.location.pathname.match(/^\/v2\/orders\/(\d+)$/);
  if (!match) return;
  const orderPath = `/v2/orders/${match[1]}`;
  document.querySelectorAll('a[href="#document-requirements"]').forEach((link) => {
    link.href = `${orderPath}/document-requirements`;
    link.textContent = "修改文件需求";
  });
  const preparation = document.querySelector("#shipment-preparation");
  if (preparation) {
    const button = preparation.querySelector("button[type=submit]");
    if (button) button.textContent = "Save Shipment Preparation";
    if (!preparation.querySelector(".independent-save-note")) {
      const note = document.createElement("p");
      note.className = "text-muted small mb-0 independent-save-note";
      note.textContent = "This section saves independently. Saving another section does not save changes here.";
      button?.insertAdjacentElement("beforebegin", note);
    }
  }
  if (preparation && !document.querySelector("#booking-hs-codes-link")) {
    const link = document.createElement("a");
    link.id = "booking-hs-codes-link";
    link.className = "btn btn-outline-secondary ms-2";
    link.href = `${orderPath}/booking-hs-codes`;
    link.textContent = "填写 PIItem HS Code";
    preparation.querySelector("button[type=submit]")?.insertAdjacentElement("afterend", link);
  }
  const activity = [...document.querySelectorAll("section.card")]
    .find((section) => section.querySelector("h5")?.textContent.trim() === "Tasks & Activity");
  if (activity) {
    activity.id = "tasks-activity";
    if (window.location.hash === "#tasks-activity") activity.scrollIntoView();
  }
  // ARRIVED → COMPLETED is intentionally outside this batch; do not expose
  // the legacy generic transition while the server-side guard remains strict.
  document.querySelector('input[name="status"][value="COMPLETED"]')?.closest("form")?.remove();
});

document.addEventListener("DOMContentLoaded", () => {
  const match = window.location.pathname.match(/^\/v2\/orders\/(\d+)$/);
  if (!match) return;
  const orderPath = `/v2/orders/${match[1]}`;
  document.querySelectorAll('a[href="#document-requirements"]').forEach((link) => {
    link.href = `${orderPath}/document-requirements`;
    link.textContent = "修改文件需求";
  });
  const preparation = document.querySelector("#shipment-preparation");
  if (preparation && !document.querySelector("#booking-hs-codes-link")) {
    const link = document.createElement("a");
    link.id = "booking-hs-codes-link";
    link.className = "btn btn-outline-secondary ms-2";
    link.href = `${orderPath}/booking-hs-codes`;
    link.textContent = "填写 PIItem HS Code";
    preparation.querySelector("button[type=submit]")?.insertAdjacentElement("afterend", link);
  }
});

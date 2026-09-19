/* Reusable V2 country-name combobox. The server remains authoritative. */
(() => {
  const normalize = (value) => String(value || "").trim().replace(/\s+/g, " ").toLocaleLowerCase();

  function initializeCountryCombobox(root, countries) {
    const input = root.querySelector("[data-country-input]");
    const code = root.querySelector("[data-country-code]");
    const listbox = root.querySelector("[data-country-listbox]");
    if (!input || !code || !listbox) return;

    const exactInputs = new Map();
    const addExactInput = (value, country) => {
      const key = normalize(value);
      if (!key) return;
      const existing = exactInputs.get(key);
      exactInputs.set(key, existing && existing.code !== country.code ? null : country);
    };
    countries.forEach((country) => {
      addExactInput(country.name, country);
      (country.aliases || []).forEach((alias) => addExactInput(alias, country));
    });
    let matches = [];
    let activeIndex = -1;

    const close = () => {
      listbox.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      activeIndex = -1;
    };
    const select = (country) => {
      input.value = country.name;
      code.value = country.code;
      close();
    };
    const render = () => {
      listbox.replaceChildren();
      if (!matches.length) {
        close();
        return;
      }
      matches.forEach((country, index) => {
        const option = document.createElement("li");
        option.id = `${listbox.id}-option-${index}`;
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", String(index === activeIndex));
        option.className = "v2-country-option";
        option.textContent = country.name;
        option.addEventListener("mousedown", (event) => {
          event.preventDefault();
          select(country);
        });
        listbox.appendChild(option);
      });
      listbox.hidden = false;
      input.setAttribute("aria-expanded", "true");
      if (activeIndex >= 0) input.setAttribute("aria-activedescendant", `${listbox.id}-option-${activeIndex}`);
      else input.removeAttribute("aria-activedescendant");
    };
    const update = () => {
      const query = normalize(input.value);
      code.value = "";
      const exact = exactInputs.get(query);
      if (exact) code.value = exact.code;
      matches = query ? countries.map((country) => {
        const name = normalize(country.name);
        const aliases = (country.aliases || []).map(normalize);
        let rank = null;
        if (query === name) rank = 0;
        else if (name.startsWith(query)) rank = 1;
        else if (name.includes(query)) rank = 2;
        else if (aliases.includes(query)) rank = 3;
        else if (aliases.some((alias) => alias.startsWith(query))) rank = 4;
        else if (aliases.some((alias) => alias.includes(query))) rank = 5;
        return rank === null ? null : { country, rank };
      }).filter(Boolean).sort((left, right) => (
        left.rank - right.rank || normalize(left.country.name).localeCompare(normalize(right.country.name)) || left.country.code.localeCompare(right.country.code)
      )).slice(0, 10).map(({ country }) => country) : [];
      activeIndex = -1;
      render();
    };
    const move = (step) => {
      if (!matches.length) return;
      activeIndex = (activeIndex + step + matches.length) % matches.length;
      render();
      listbox.querySelector(`[aria-selected="true"]`)?.scrollIntoView({ block: "nearest" });
    };

    input.addEventListener("input", update);
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        if (!matches.length) update();
        move(1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        move(-1);
      } else if (event.key === "Enter" && activeIndex >= 0) {
        event.preventDefault();
        select(matches[activeIndex]);
      } else if (event.key === "Escape") {
        close();
      }
    });
    input.addEventListener("blur", () => window.setTimeout(close, 120));
  }

  document.addEventListener("DOMContentLoaded", () => {
    const countries = Array.isArray(window.V2_COUNTRIES) ? window.V2_COUNTRIES : [];
    if (!countries.length) return;
    document.querySelectorAll("[data-country-combobox]").forEach((root) => initializeCountryCombobox(root, countries));
  });
})();

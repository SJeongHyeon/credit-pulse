const SORTS = Object.freeze({ recent: "최신순", oldest: "오래된순", name: "가나다순" });

export function bindSortMenu(document, id, onChange) {
  const sortButton = document.getElementById(id);
  const sortLabel = document.getElementById(`${id}-label`);
  const menu = document.getElementById(`${id}-menu`);
  if (!sortButton || !sortLabel || !menu) return;

  const options = [...menu.querySelectorAll("[role=option]")];
  let sort = "recent";
  let open = false;

  const inside = (target) =>
    Boolean(target) && (target === sortButton || target === menu || sortButton.contains?.(target) || menu.contains?.(target));

  function openMenu(focusIndex) {
    if (sortButton.getAttribute("aria-disabled") === "true") return;
    open = true;
    menu.hidden = false;
    sortButton.setAttribute("aria-expanded", "true");
    const selected = options.findIndex((option) => option.getAttribute("data-sort") === sort);
    options[focusIndex ?? Math.max(0, selected)]?.focus?.();
  }

  function closeMenu({ restoreFocus = false } = {}) {
    if (!open) return;
    open = false;
    menu.hidden = true;
    sortButton.setAttribute("aria-expanded", "false");
    if (restoreFocus) sortButton.focus?.();
  }

  function choose(key) {
    if (!Object.hasOwn(SORTS, key) || sortButton.getAttribute("aria-disabled") === "true") return;
    sort = key;
    sortLabel.textContent = SORTS[key];
    sortButton.setAttribute("aria-label", `정렬: ${SORTS[key]}`);
    for (const option of options) {
      const selected = option.getAttribute("data-sort") === key;
      option.setAttribute("aria-selected", selected ? "true" : "false");
    }
    closeMenu({ restoreFocus: true });
    return onChange(key, SORTS[key]);
  }

  function onOptionKey(event, index) {
    const last = options.length - 1;
    const move = { ArrowDown: Math.min(last, index + 1), ArrowUp: Math.max(0, index - 1), Home: 0, End: last }[event.key];
    if (move !== undefined) {
      event.preventDefault?.();
      options[move].focus?.();
    } else if (event.key === "Escape") {
      event.preventDefault?.();
      closeMenu({ restoreFocus: true });
    } else if (event.key === "Tab") {
      closeMenu();
    }
  }

  sortButton.addEventListener("click", () => (open ? closeMenu() : openMenu()));
  sortButton.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault?.();
      openMenu();
    } else if (event.key === "ArrowUp") {
      event.preventDefault?.();
      openMenu(options.length - 1);
    } else if (event.key === "Escape") {
      closeMenu();
    }
  });
  options.forEach((option, index) => {
    option.addEventListener("click", () => choose(option.getAttribute("data-sort")));
    option.addEventListener("keydown", (event) => onOptionKey(event, index));
  });
  document.addEventListener?.("click", (event) => {
    if (open && !inside(event.target)) closeMenu();
  });
}

// 시트·패널 테스트용 아주 작은 가짜 DOM. (*.test.mjs가 아니므로 테스트로 실행되지 않는다.)
export function createElement(tagName = "div", values = {}) {
  const attributes = {};
  const element = {
    tagName: tagName.toUpperCase(),
    children: [],
    hidden: false,
    disabled: false,
    textContent: "",
    className: "",
    value: "",
    listeners: {},
    focused: 0,
    ...values,
    addEventListener(name, listener) {
      (this.listeners[name] ??= []).push(listener);
    },
    append(...nodes) {
      this.children.push(...nodes);
    },
    appendChild(node) {
      this.children.push(node);
      return node;
    },
    replaceChildren(...nodes) {
      this.children = nodes;
    },
    setAttribute(name, value) {
      attributes[name] = String(value);
    },
    getAttribute(name) {
      return name in attributes ? attributes[name] : null;
    },
    removeAttribute(name) {
      delete attributes[name];
    },
    focus() {
      this.focused += 1;
    },
    set innerHTML(_) {
      throw new Error("innerHTML must not be used");
    },
  };
  return element;
}

// 이벤트를 보낸다. 등록된 모든 리스너를 부르고 마지막 리스너의 결과를 기다린다.
export async function fire(element, name, event = {}) {
  const evt = { prevented: false, preventDefault() { this.prevented = true; }, target: element, ...event };
  for (const listener of element.listeners[name] ?? []) await listener(evt);
  return evt;
}

export const descendants = (element) => element.children.flatMap((child) => [child, ...descendants(child)]);
export const allText = (element) => [element.textContent, ...element.children.map(allText)].join(" ");
export const byClass = (element, className) => descendants(element).filter((node) => String(node.className).split(" ").includes(className));
export const byTag = (element, tag) => descendants(element).filter((node) => node.tagName === tag.toUpperCase());
export const hrefs = (element) => byTag(element, "a").map((node) => node.getAttribute("href"));

export const wrap = (nodes) => {
  const root = createElement("div");
  root.append(...nodes);
  return root;
};

export function createDocument(extra = {}) {
  return { createElement: (tag) => createElement(tag), ...extra };
}

export const json = (status, body) => ({ status, json: async () => body });

export const review = (overrides = {}) => ({
  id: "r-1",
  corp_code: "00126380",
  corp_name: "삼성전자",
  fiscal_year: 2025,
  status: "open",
  query_count: 1,
  created_at: "2026-11-04T03:34:00Z",
  ...overrides,
});

// ctx.reviews()가 돌려줄 결과를 만든다.
export const okReviews = (reviews, truncated = false) => async () => ({ status: "ok", reviews, truncated });
export const failReviews = (status, http) => async () => (status === "error" ? { status, http } : { status });

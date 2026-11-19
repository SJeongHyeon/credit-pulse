// 시트의 요약 카드·막대·도넛·빈 상태를 그리는 DOM 빌더. 차트 라이브러리 없이 DOM + CSS만 쓴다.
// 서버가 준 문자열은 모두 textContent로만 넣는다(HTML로 해석하지 않는다). style 속성에는 계산한 숫자와 고정 토큰만 넣는다.

// theme.css 토큰만 쓴다. 색만으로 구분하지 않도록 범례에 이름과 숫자를 함께 적는다.
export const SEGMENT_COLORS = Object.freeze(["var(--gold)", "var(--gold-soft)", "var(--on-dark-muted)", "var(--on-dark)"]);

export function el(document, tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = String(text);
  return element;
}

export function link(document, label, href, { solid = false } = {}) {
  const anchor = el(document, "a", solid ? "sheet-link sheet-link-solid" : "sheet-link", label);
  anchor.setAttribute("href", href);
  return anchor;
}

// items: [{ label, value }]
export function statCards(document, items) {
  const list = el(document, "ul", "sheet-cards");
  for (const item of items) {
    const card = el(document, "li");
    card.append(el(document, "strong", "", item.value), el(document, "span", "", item.label));
    list.append(card);
  }
  return list;
}

export function chartSummary(title, rows) {
  return `${title}: ${rows.map((row) => `${row.label} ${row.value}`).join(", ")}`;
}

function chartBox(document, title, summary) {
  const box = el(document, "div", "sheet-chart");
  box.append(el(document, "h3", "", title));
  const figure = el(document, "div", "sheet-chart-figure");
  figure.setAttribute("role", "img");
  figure.setAttribute("aria-label", summary);
  box.append(figure);
  return { box, figure };
}

// rows: [{ label, value }] — 값이 가장 큰 행이 100%
export function barChart(document, { title, rows, unit = "" }) {
  const { box, figure } = chartBox(document, title, chartSummary(title, rows.map((row) => ({ ...row, value: `${row.value}${unit}` }))));
  const max = Math.max(1, ...rows.map((row) => row.value));
  for (const row of rows) {
    const line = el(document, "div", "sheet-bar");
    const fill = el(document, "i");
    fill.setAttribute("style", `width:${row.value === 0 ? 0 : Math.max(4, Math.round((row.value / max) * 100))}%`);
    const track = el(document, "span", "sheet-bar-track");
    track.append(fill);
    line.append(el(document, "span", "sheet-bar-label", row.label), track, el(document, "em", "", `${row.value}${unit}`));
    figure.append(line);
  }
  return box;
}

// rows: [{ label, value }] — 합이 0이면 호출하지 않는다(빈 상태를 보인다).
export function donutChart(document, { title, rows, unit = "건" }) {
  const { box, figure } = chartBox(document, title, chartSummary(title, rows.map((row) => ({ ...row, value: `${row.value}${unit}` }))));
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  let accumulated = 0;
  const stops = rows.map((row, index) => {
    const start = (accumulated / total) * 100;
    accumulated += row.value;
    return `${SEGMENT_COLORS[index % SEGMENT_COLORS.length]} ${start.toFixed(2)}% ${((accumulated / total) * 100).toFixed(2)}%`;
  });
  const wrap = el(document, "div", "sheet-donut-wrap");
  const donut = el(document, "div", "sheet-donut");
  donut.setAttribute("style", `background:conic-gradient(${stops.join(",")})`);
  const legend = el(document, "ul", "sheet-legend");
  rows.forEach((row, index) => {
    const item = el(document, "li", "", `${row.label} ${row.value}${unit}`);
    item.setAttribute("style", `--c:${SEGMENT_COLORS[index % SEGMENT_COLORS.length]}`);
    legend.append(item);
  });
  wrap.append(donut, legend);
  figure.append(wrap);
  return box;
}

export function emptyState(document, { message, actionLabel, href }) {
  const box = el(document, "div", "sheet-empty");
  box.append(el(document, "p", "", message));
  if (actionLabel && href) box.append(link(document, actionLabel, href, { solid: true }));
  return box;
}

export function leadText(document, text) {
  return el(document, "p", "sheet-lead", text);
}

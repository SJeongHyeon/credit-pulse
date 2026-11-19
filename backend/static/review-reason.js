const UNIT_FACTOR = { 원: 1n, 천원: 1000n, 백만원: 1000000n };
const AMOUNT = "-?(?:\\d{1,3}(?:,\\d{3})+|\\d+)";
const BARE_AMOUNT = new RegExp(`^${AMOUNT}$`);
const COMPANY_AMOUNT = new RegExp(`^(.+?)\\s+(${AMOUNT})$`);

function citedUnit(amount, citations) {
  const escaped = amount.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const exactAmount = new RegExp(`(^|[^\\d,])${escaped}(?=$|[^\\d,])`);
  const units = new Set();
  for (const citation of citations) {
    const quote = typeof citation?.quote === "string" ? citation.quote : "";
    if (!exactAmount.test(quote)) continue;
    const inQuote = [...quote.matchAll(/단위\s*[:：]\s*(백만원|천원|원)/g)].map((match) => match[1]);
    if (new Set(inQuote).size === 1) units.add(inQuote[0]);
    else if (inQuote.length > 1) return null;
  }
  return units.size === 1 ? [...units][0] : null;
}

function won(amount, unit) {
  const value = BigInt(amount.replaceAll(",", "")) * UNIT_FACTOR[unit];
  return `${value.toLocaleString("ko-KR")}원`;
}

/** 원문 숫자를 추측하지 않고, 동일 숫자와 단위가 인용문에 있을 때만 표시용으로 환산한다. */
export function formatReviewReason(reason, citations = []) {
  const raw = String(reason ?? "");
  const quotes = Array.isArray(citations) ? citations : [];
  const trimmed = raw.trim();
  const parts = trimmed.split(/\s*\|\s*/);
  if (parts.length >= 2) {
    const pairs = parts.map((part) => part.match(COMPANY_AMOUNT));
    if (pairs.every(Boolean)) {
      const entries = pairs.map((match) => {
        const amount = match[2];
        const unit = citedUnit(amount, quotes);
        return { company: match[1], amount: unit ? won(amount, unit) : amount };
      });
      return {
        kind: "pairs",
        entries,
        editorText: entries.map(({ company, amount }) => `${company} ${amount}`).join("\n"),
      };
    }
  }
  if (BARE_AMOUNT.test(trimmed) && (trimmed.includes(",") || trimmed.replace("-", "").length >= 5)) {
    const unit = citedUnit(trimmed, quotes);
    if (unit) {
      const editorText = won(trimmed, unit);
      return { kind: "amount", editorText };
    }
  }
  return { kind: "plain", editorText: raw };
}

export function renderReviewReason(document, formatted) {
  const display = document.createElement("div");
  display.className = formatted.kind === "pairs" ? "reason-display reason-pairs" : "reason-display";
  if (formatted.kind === "pairs") {
    for (const { company, amount } of formatted.entries) {
      const pair = document.createElement("span");
      pair.className = "reason-pair";
      const name = document.createElement("span");
      name.className = "reason-pair-name";
      name.textContent = company;
      const value = document.createElement("span");
      value.className = "reason-pair-amount";
      value.textContent = amount;
      pair.append(name, value);
      display.append(pair);
    }
  } else {
    display.textContent = formatted.editorText;
  }
  return display;
}

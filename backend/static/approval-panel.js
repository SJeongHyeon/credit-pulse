import { COMMON_MESSAGES, ITEM_LABELS, UNKNOWN, VERDICT_LABELS, isObject, own } from "./review-shared.js";
import { formatReviewReason, renderReviewReason } from "./review-reason.js";

const MAX_REASON_LENGTH = 1000;
const SUBMIT_LABEL = "승인";
const SAVING_LABEL = "저장 중…";

// 서버 오류 본문(details, request_id)은 쓰지 않는다. 상태 코드만 본다.
const MESSAGES = {
  ...COMMON_MESSAGES,
  badInput: "입력 내용을 확인해 주세요.",
  failed: "처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  reason: "이유를 1~1000자로 입력하세요",
  conflict: "이미 처리된 승인입니다. 화면을 새로 불러옵니다",
  approved: "심사 메모로 저장했습니다.",
  approvedStale: "저장했습니다. 상태를 새로 불러오지 못했습니다.",
  cancelled: "초안을 취소했습니다. 저장된 메모는 없습니다.",
  cancelledStale: "취소했습니다. 상태를 새로 불러오지 못했습니다.",
  noDraft: "승인할 초안을 불러오지 못했습니다. 초안을 취소하고 다시 질문해 주세요.",
  confirmApprove: "수정한 내용을 포함해 승인하고 심사 메모로 저장할까요?",
  confirmCancel: "초안을 취소할까요? 저장된 메모는 만들어지지 않습니다.",
  edited: "수정됨",
  failedCitation: "인용 검증 실패 항목 있음",
};

const hasFailedCitation = (draft) =>
  Array.isArray(draft.citations) &&
  draft.citations.some((citation) => isObject(citation) && isObject(citation.verification) && citation.verification.passed === false);

/**
 * 승인 대기 중인 판정 초안을 검토·수정해 승인하거나 취소하는 패널.
 * 승인자는 요청에 담지 않는다(서버가 세션에서 정한다).
 */
export function createApprovalPanel({ document, request, confirm, renderCitation, approvalsPath, onSettled, onMissing }) {
  const el = (id) => document.getElementById(id);
  const section = el("approval-section");
  const questionBox = el("approval-question");
  const questionText = el("approval-question-text");
  const summary = el("approval-summary");
  const list = el("approval-list");
  const emptyNote = el("approval-empty");
  const errorBox = el("approval-error");
  const doneBox = el("approval-done");
  const submitButton = el("approval-submit");
  const cancelButton = el("approval-cancel");

  let rows = [];
  let shownKey = null;
  let busy = false;
  let asking = false;

  const textElement = (tag, className, text) => {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  };

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  function clearError() {
    errorBox.textContent = "";
    errorBox.hidden = true;
  }

  function showDone(message) {
    doneBox.textContent = message;
    doneBox.hidden = false;
  }

  function clearDone() {
    doneBox.textContent = "";
    doneBox.hidden = true;
  }

  const isEdited = (row) => row.verdict !== row.originalVerdict || row.reason.trim() !== String(row.originalReason).trim();

  function refreshRow(row) {
    const edited = isEdited(row);
    row.badge.hidden = !edited;
    row.comparison.hidden = !edited;
    if (edited) {
      row.currentVerdict.textContent = own(VERDICT_LABELS, row.verdict) ?? UNKNOWN;
      row.currentReason.textContent = row.reason.trim() || "입력한 이유 없음";
    } else {
      row.comparison.open = false;
    }
    if (row.error.hidden === false && row.reason.trim().length >= 1 && row.reason.trim().length <= MAX_REASON_LENGTH) {
      row.error.hidden = true;
      row.error.textContent = "";
    }
    const editedCount = rows.filter(isEdited).length;
    summary.textContent = `수정 ${editedCount}개 / 전체 ${rows.length}개`;
  }

  function renderRow(draft, index) {
    const citations = Array.isArray(draft.citations) ? draft.citations.filter(isObject) : [];
    const formatted = formatReviewReason(draft.reason, citations);
    const row = {
      item: draft.item,
      originalVerdict: draft.verdict,
      rawReason: String(draft.reason ?? ""),
      originalReason: formatted.editorText,
      verdict: draft.verdict,
      reason: formatted.editorText,
    };

    const fieldset = document.createElement("fieldset");
    fieldset.className = "approval-item";
    const legend = document.createElement("legend");
    legend.className = "approval-legend";
    legend.append(textElement("span", "draft-item-name", own(ITEM_LABELS, draft.item) ?? UNKNOWN));
    row.badge = textElement("span", "edited-badge", MESSAGES.edited);
    row.badge.hidden = true;
    legend.append(row.badge);
    if (hasFailedCitation(draft)) legend.append(textElement("span", "citation-check check-fail", MESSAGES.failedCitation));
    fieldset.append(legend);

    const verdicts = document.createElement("div");
    verdicts.className = "approval-verdicts";
    row.inputs = [];
    for (const [value, label] of Object.entries(VERDICT_LABELS)) {
      const wrapper = document.createElement("label");
      wrapper.className = "approval-radio";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = `approval-verdict-${index}`;
      input.value = value;
      input.checked = draft.verdict === value;
      input.addEventListener("change", () => {
        if (input.checked) row.verdict = value;
        refreshRow(row);
      });
      row.inputs.push(input);
      wrapper.append(input, textElement("span", "approval-radio-text", label));
      verdicts.append(wrapper);
    }
    fieldset.append(verdicts);

    const reasonLabel = document.createElement("label");
    reasonLabel.className = "approval-reason-label";
    reasonLabel.append(textElement("span", "approval-reason-text", "이유"));
    row.textarea = document.createElement("textarea");
    row.textarea.className = "approval-reason review-question";
    row.textarea.rows = 3;
    row.textarea.value = row.reason;
    row.textarea.addEventListener("input", () => {
      row.reason = row.textarea.value;
      refreshRow(row);
    });
    if (formatted.kind !== "plain") {
      const display = renderReviewReason(document, formatted);
      const editButton = textElement("button", "reason-edit", "이유 수정");
      editButton.type = "button";
      row.textarea.hidden = true;
      editButton.addEventListener("click", () => {
        display.hidden = true;
        editButton.hidden = true;
        row.textarea.hidden = false;
        row.textarea.focus();
      });
      reasonLabel.append(display, editButton);
    }
    reasonLabel.append(row.textarea);
    fieldset.append(reasonLabel);

    row.comparison = document.createElement("details");
    row.comparison.className = "approval-comparison";
    row.comparison.hidden = true;
    const comparisonColumns = document.createElement("div");
    comparisonColumns.className = "approval-comparison-columns";
    const original = document.createElement("div");
    original.append(
      textElement("h3", "approval-comparison-title", "AI 초안"),
      textElement("p", "approval-comparison-verdict", own(VERDICT_LABELS, row.originalVerdict) ?? UNKNOWN),
      textElement("p", "approval-comparison-reason", row.originalReason || "기록된 이유 없음"),
    );
    const current = document.createElement("div");
    row.currentVerdict = textElement("p", "approval-comparison-verdict", "");
    row.currentReason = textElement("p", "approval-comparison-reason", "");
    current.append(textElement("h3", "approval-comparison-title", "내 수정"), row.currentVerdict, row.currentReason);
    comparisonColumns.append(original, current);
    row.comparison.append(textElement("summary", "", "수정 내용 보기"), comparisonColumns);
    fieldset.append(row.comparison);

    row.error = textElement("p", "item-error", "");
    row.error.setAttribute("role", "alert");
    row.error.hidden = true;
    fieldset.append(row.error);

    const citationList = document.createElement("ul");
    citationList.className = "citation-list";
    citationList.append(...citations.map(renderCitation));
    fieldset.append(citationList);

    row.node = fieldset;
    return row;
  }

  function setBusy(value, label = SUBMIT_LABEL) {
    busy = value;
    submitButton.disabled = value || asking;
    cancelButton.disabled = value || asking;
    submitButton.textContent = value ? label : SUBMIT_LABEL;
    for (const row of rows) {
      row.textarea.disabled = value;
      row.inputs.forEach((input) => {
        input.disabled = value;
      });
    }
  }

  function show(draft, question) {
    const items = Array.isArray(draft) ? draft.filter(isObject) : [];
    const key = JSON.stringify([items, question]);
    section.hidden = false;
    questionText.textContent = typeof question === "string" && question.trim() ? question : "";
    questionBox.hidden = !questionText.textContent;
    if (key === shownKey) return;
    shownKey = key;
    clearError();
    clearDone();

    rows = items.map(renderRow);
    list.replaceChildren(...rows.map((row) => row.node));
    const empty = rows.length === 0;
    emptyNote.textContent = empty ? MESSAGES.noDraft : "";
    emptyNote.hidden = !empty;
    submitButton.hidden = empty;
    summary.hidden = empty;
    cancelButton.hidden = false;
    if (!empty) rows.forEach(refreshRow);
    setBusy(false);
  }

  function hide() {
    section.hidden = true;
    questionBox.hidden = true;
    questionText.textContent = "";
    shownKey = null;
  }

  function validate() {
    let firstInvalid = null;
    for (const row of rows) {
      const length = row.reason.trim().length;
      const valid = length >= 1 && length <= MAX_REASON_LENGTH;
      row.error.textContent = valid ? "" : MESSAGES.reason;
      row.error.hidden = valid;
      if (!valid && !firstInvalid) firstInvalid = row;
    }
    if (firstInvalid) firstInvalid.textarea.focus();
    return firstInvalid === null;
  }

  function failureMessage(status) {
    if (status === 400) return MESSAGES.badInput;
    if (status === 429) return MESSAGES.tooMany;
    if (status >= 500) return MESSAGES.server;
    return MESSAGES.failed;
  }

  // 승인과 취소는 같은 흐름이다. 요청 한 번, 재시도 루프 없음.
  async function run({ path, options, kind, doneMessage, staleMessage, label }) {
    if (busy) return;
    clearError();
    clearDone();
    setBusy(true, label);
    try {
      const response = await request(path, options);
      if (!response) return;
      if (response.status === 404) {
        onMissing();
        return;
      }
      if (response.status === 409) {
        showError(MESSAGES.conflict);
        hide();
        await onSettled("conflict");
        return;
      }
      if (response.status !== 200 && response.status !== 204) {
        showError(failureMessage(response.status));
        return;
      }
      hide();
      const state = await onSettled(kind);
      showDone(state === "ok" ? doneMessage : staleMessage);
    } catch {
      showError(MESSAGES.network);
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (busy || asking || rows.length === 0) return;
    clearError();
    if (!validate()) return;
    if (!confirm(MESSAGES.confirmApprove)) return;
    const decisions = rows.map((row) => ({
      item: row.item,
      verdict: row.verdict,
      reason: isEdited(row) ? row.reason.trim() : row.rawReason,
    }));
    await run({
      path: approvalsPath,
      options: {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ decisions }),
      },
      kind: "approved",
      doneMessage: MESSAGES.approved,
      staleMessage: MESSAGES.approvedStale,
      label: SAVING_LABEL,
    });
  }

  async function cancel() {
    if (busy || asking) return;
    if (!confirm(MESSAGES.confirmCancel)) return;
    await run({
      path: `${approvalsPath}:cancel`,
      options: { method: "POST" },
      kind: "cancelled",
      doneMessage: MESSAGES.cancelled,
      staleMessage: MESSAGES.cancelledStale,
      label: SUBMIT_LABEL,
    });
  }

  submitButton.addEventListener("click", approve);
  cancelButton.addEventListener("click", cancel);

  return {
    show, hide, clearDone,
    isBusy: () => busy,
    setAsking(value) {
      asking = value;
      submitButton.disabled = busy || asking;
      cancelButton.disabled = busy || asking;
    },
  };
}

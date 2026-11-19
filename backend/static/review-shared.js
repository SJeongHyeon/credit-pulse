// 상세 화면과 승인 패널이 함께 쓰는 라벨·도우미.
export const UNKNOWN = "알 수 없음";

export const COMMON_MESSAGES = {
  network: "서버에 연결할 수 없습니다. 네트워크를 확인하고 다시 시도해 주세요.",
  server: "서버에 문제가 있어 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
  tooMany: "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
};

export const ITEM_LABELS = {
  litigation: "소송",
  guarantee_collateral: "보증·담보",
  customer_concentration: "거래처 집중",
  fx_raw_material: "환율·원자재",
  borrowing_structure: "차입 구조",
  contingent_liability: "우발부채",
};

export const VERDICT_LABELS = {
  applicable: "해당",
  not_applicable: "비해당",
  needs_check: "확인필요",
};

export const isObject = (value) => typeof value === "object" && value !== null;

// Object.hasOwn으로만 조회한다(constructor 같은 값 방어).
export const own = (table, key) => (typeof key === "string" && Object.hasOwn(table, key) ? table[key] : undefined);

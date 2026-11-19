// Cloud Run 앞단에서 돌아온 HTTP 상태만 사용한다. 오류 본문은 화면에 전달하지 않는다.
const MESSAGES = {
  502: "서버 연결에 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.",
  503: "서비스를 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
  504: "서버 응답 시간이 초과됐습니다. 잠시 후 다시 시도해 주세요.",
};

export function serviceStatusMessage(status) {
  return Object.hasOwn(MESSAGES, status) ? MESSAGES[status] : null;
}

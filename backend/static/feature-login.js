// 03 패널의 링크 칸: 로그인하지 않은 사람이 누르면 화면을 넘기지 않고 로그인 창을 연다.
// 로그인 상태(login.js가 <html data-auth>에 적는다)에서는 가로채지 않고 링크대로 이동한다.
// 로그인 뒤 도착지는 sessionStorage의 login-next에 적어 두고 login.js가 읽는다(main-page-design 개정 8 AC-64·65).
const NEXT_KEY = "login-next";
// login.js의 허용 목록과 항상 같아야 한다.
const ALLOWED = ["/reviews", "/?open=search", "/?open=reviews", "/?open=reports", "/?open=approvals", "/?open=rules"];

export function createFeatureLoginController({ document, storage }) {
  const tiles = document.querySelectorAll("#features a[data-feature]");
  const openLogin = document.getElementById("open-login");
  const dialog = document.getElementById("login-dialog");

  function remember(href) {
    try {
      if (ALLOWED.includes(href)) storage.setItem(NEXT_KEY, href);
    } catch {
      // 저장소를 쓸 수 없어도 로그인 창은 연다. 도착지는 /reviews가 된다.
    }
  }

  function forget() {
    try {
      storage.removeItem(NEXT_KEY);
    } catch {
      // 지우지 못해도 login.js가 허용 목록만 따른다.
    }
  }

  function isAuthenticated() {
    return document.documentElement?.getAttribute?.("data-auth") === "authenticated";
  }

  function initialize() {
    for (const tile of tiles) {
      tile.addEventListener("click", (event) => {
        if (isAuthenticated()) return;
        event.preventDefault();
        remember(tile.getAttribute("href"));
        openLogin.click();
      });
    }
    // 창을 닫으면 도착지를 잊는다. 나중에 다른 곳에서 로그인하면 /reviews로 간다.
    dialog.addEventListener("close", forget);
  }

  return { initialize };
}

if (typeof window !== "undefined" && window.document?.getElementById("login-dialog")) {
  let storage;
  try {
    storage = window.sessionStorage;
  } catch {
    storage = undefined;
  }
  createFeatureLoginController({ document: window.document, storage }).initialize();
}

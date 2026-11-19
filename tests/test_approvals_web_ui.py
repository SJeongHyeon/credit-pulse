import re

from fastapi.testclient import TestClient

from backend.main import app


def _get(path: str):
    return TestClient(app, raise_server_exceptions=False).get(path)


def _tag_containing(html: str, marker: str) -> str:
    at = html.index(marker)
    start = html.rindex("<", 0, at)
    return html[start : html.index(">", at) + 1]


def _rule(css: str, selector: str) -> str:
    start = css.index(selector)
    return css[start : css.index("}", start)]


def _order(html: str, names: list[str]) -> list[int]:
    return [html.index(name) for name in names]


def test_approvals_route_serves_html_shell():
    """AC-01: 공개 쉘 HTML. 제목·h1·CSS 순서·모듈 스크립트."""
    response = _get("/approvals")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "<title>CREDIT PULSE - 승인 현황</title>" in html
    assert "<h1>승인 현황</h1>" in html
    positions = _order(html, ["login.css", "theme.css", "reviews.css", "approvals.css"])
    assert positions == sorted(positions)
    assert re.search(r'type="module" src="[^"]*/static/approvals\.js"', html)
    assert "innerHTML" not in html
    # url_for가 만든 테스트 서버 주소 말고 외부 주소는 없다
    assert set(re.findall(r"https?://([^/\"]+)", html)) <= {"testserver"}


def test_approvals_page_status_filter_and_regions_are_accessible():
    """AC-11: 상태 선택은 group+aria-pressed, 알림은 status, 오류는 alert."""
    html = _get("/approvals").text

    group = _tag_containing(html, 'role="group"')
    assert 'aria-label="상태 선택"' in group
    pressed = re.findall(r'<button id="filter-([a-z_]+)"[^>]*aria-pressed="(true|false)"', html)
    assert pressed == [("awaiting_approval", "true"), ("open", "false"), ("running", "false")]
    assert 'role="status"' in _tag_containing(html, 'id="approvals-status"')
    assert 'role="alert"' in _tag_containing(html, 'id="approvals-error"')
    assert "<ul" in _tag_containing(html, 'id="approvals-list"')
    assert "<noscript>" in html
    assert '<a href="/reviews">심사 건 전체 보기</a>' in html
    assert "최근 250건만 확인했습니다. 전체는 " in html and "에서 확인하세요." in html


def test_approvals_script_has_only_allowed_calls():
    """How: fetch는 이 파일에서만, innerHTML·외부 주소·eval은 없다. 랜딩 계열 JS는 그대로 fetch 금지."""
    script = _get("/static/approvals.js").text

    assert "fetch(" in script and "same-origin" in script
    for forbidden in ("innerHTML", "http", "eval(", "XMLHttpRequest"):
        assert forbidden not in script, forbidden
    assert "page_size=${PAGE_SIZE}" in script and "const PAGE_SIZE = 50;" in script and "const MAX_PAGES = 5;" in script
    for path in ("/static/landing.js", "/static/stage-tabs.js"):
        assert "fetch(" not in _get(path).text, path


def test_approvals_css_wins_over_global_button_rules():
    """AC-12: 선택된 버튼 규칙은 .reviews-page button.secondary보다 구체적이고 색이 서로 다르다."""
    approvals = _get("/static/approvals.css").text
    reviews = _get("/static/reviews.css").text

    selector = '.approvals-page .status-filter button.secondary[aria-pressed="true"] {'
    selected = _rule(approvals, selector)
    # (id, class+attribute, element) 구체성: 클래스 3(.approvals-page .status-filter .secondary) + 속성 1 + 요소 1
    assert selector.count(".") == 3 and "[aria-pressed" in selector
    assert "background: var(--on-dark)" in selected and "color: #17120d" in selected
    unselected = _rule(reviews, ".reviews-page button.secondary,")
    assert "rgba(255, 255, 255, 0.1)" in unselected and "color: var(--on-dark)" in unselected
    assert "prefers-reduced-motion: reduce" in approvals
    assert "outline" in approvals and ":focus-visible" in approvals
    for moving in ("animation", "@keyframes"):
        assert moving not in approvals

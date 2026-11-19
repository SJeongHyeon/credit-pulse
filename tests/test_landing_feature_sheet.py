"""docs/specs/landing-feature-pages.md: 랜딩 기능 시트의 마크업·정적 파일 계약."""
import re

from fastapi.testclient import TestClient

from backend import auth
from backend.main import app

KEYS = ["search", "reviews", "reports", "approvals", "rules"]
NAMES = ["공시 검색", "리스크 점검", "보고서", "승인 현황", "내 판정 규칙"]
NEW_SCRIPTS = ["feature-sheet.js", "feature-strips.js", "feature-data.js", "sheet-panels.js", "charts.js", "review-data.js"]


def _get(path: str):
    client = TestClient(app, raise_server_exceptions=False)
    if path == "/reviews":
        client.cookies.set(auth.SESSION_COOKIE_NAME, auth.issue_session_token("00000000-0000-0000-0000-0000000000a1"))
    return client.get(path)


def test_landing_has_one_sheet_dialog_with_a_left_list_of_five_and_a_right_body():
    """AC-03·08(E-4): 왼쪽 목록 5개(순서 고정), 오른쪽 위 닫기, 목록 ↔ 본문 순서."""
    html = _get("/").text
    assert html.count('<dialog id="feature-sheet"') == 1
    nav = re.search(r'<nav id="sheet-tablist" class="sheet-nav" role="tablist" aria-orientation="vertical"[^>]*>(.*?)</nav>', html, re.S).group(1)
    assert re.findall(r'data-feature="(\w+)"><span class="sheet-ico"', nav) == KEYS
    assert re.findall(r"<b>([^<]+)</b>", nav) == NAMES
    assert nav.count('role="tab"') == 5 and nav.count('aria-selected="false"') == 5
    assert re.findall(r'<em class="sheet-stat" data-stat="(\w+)"></em>', nav) == KEYS  # 현재 수치 칸
    assert html.index('id="sheet-close"') < html.index('id="sheet-tablist"') < html.index('id="sheet-title"') < html.index('id="sheet-body"')
    assert 'aria-label="닫기"' in html and 'class="sheet-close"' in html
    assert re.search(r'<dialog id="feature-sheet"[^>]*aria-labelledby="sheet-title"', html)
    assert '<h2 id="sheet-title" class="sheet-heading">' in html
    assert 'id="sheet-body" class="sheet-body" role="tabpanel"' in html


def test_feature_strips_have_five_open_links_and_only_reviews_starts_open():
    """AC-15·19: 띠 5개, 「열기」는 a[data-feature]이고 처음에는 리스크 점검만 펼쳐진다."""
    html = _get("/").text
    panel = html[html.index('id="features"') :]
    opens = re.findall(r'<a class="strip-open" href="([^"]+)" data-feature="(\w+)">', panel)
    assert opens == [(f"/?open={key}", key) for key in KEYS]
    assert re.findall(r'<div id="strip-(\w+)" class="strip" data-open="(\w+)">', panel) == [(k, "true" if k == "reviews" else "false") for k in KEYS]
    assert re.findall(r'<button id="strip-rail-(\w+)" class="strip-rail" type="button" aria-expanded="(\w+)" aria-controls="strip-full-\w+">', panel) == [(k, "true" if k == "reviews" else "false") for k in KEYS]
    # 펼치지 않은 면은 조작할 수 없다
    assert len(re.findall(r'class="strip-full" role="region" aria-label="[^"]+" inert>', panel)) == 4
    assert re.findall(r'<span class="strip-name">([^<]+)</span>', panel) == NAMES
    assert re.findall(r'<em id="strip-stat-(\w+)" class="strip-stat"></em>', panel) == KEYS
    assert re.findall(r'<div id="strip-preview-(\w+)" class="strip-preview"></div>', panel) == KEYS
    assert "feature-tile" not in html and "준비 중" not in panel
    assert 'id="login-required-hint"' in panel


def test_landing_loads_sheet_assets_after_login_scripts():
    html = _get("/").text
    assert re.search(r'<link rel="stylesheet" href="[^"]*/static/feature-sheet\.css">', html)
    login = html.index("/static/login.js")
    assert re.search(r'<link rel="stylesheet" href="[^"]*/static/feature-strips\.css">', html)
    assert login < html.index("/static/feature-login.js") < html.index("/static/feature-sheet.js") < html.index("/static/feature-strips.js")


def test_new_scripts_never_write_html_or_call_external_hosts():
    """AC-12: innerHTML/외부 주소/eval 없음. LLM·차트 라이브러리 없음."""
    for name in NEW_SCRIPTS:
        response = _get(f"/static/{name}")
        assert response.status_code == 200, name
        script = response.text
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "http://", "https://", "XMLHttpRequest"):
            assert forbidden not in script, (name, forbidden)
        for lib in ("chart.js", "d3", "openai", "anthropic"):
            assert not re.search(rf'from "[^"]*{lib}', script, re.I), (name, lib)


def test_sheet_only_calls_existing_apis():
    """새 엔드포인트 0개: 시트가 부르는 API는 reviews 목록·rules 목록/삭제뿐이다."""
    calls = set()
    for name in NEW_SCRIPTS:
        calls |= {path.rstrip("/") for path in re.findall(r"/api/v1/[a-z/]+", _get(f"/static/{name}").text)}
    assert calls == {"/api/v1/reviews", "/api/v1/rules"}


def test_sheet_css_uses_theme_tokens_and_has_no_horizontal_overflow_guards():
    css = _get("/static/feature-sheet.css").text
    # 팔레트 색은 theme.css 토큰만(투명 흰색 오버레이 rgba(255,255,255,…)와 배경 막은 예외)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert "var(--gold)" in css and "var(--on-dark)" in css and "var(--glass-bg-strong)" in css
    # E-4: 옅어지는 금빛 세로선, 목록 폭 19rem, 좁으면 목록이 가로 줄
    assert "linear-gradient(to bottom, transparent, var(--gold) 30%, var(--gold) 70%, transparent)" in css
    assert "grid-template-columns: 19rem minmax(0, 1fr)" in css
    assert "@container (max-width: 44rem)" in css
    assert "prefers-reduced-motion" in css
    assert "overflow-x" not in css.replace("overflow-x: auto", "")  # 탭 줄만 가로 스크롤
    assert "@import" not in css and "url(" not in css


def test_sheet_dialog_css_overrides_the_ua_dialog_box():
    css = _get("/static/feature-sheet.css").text
    block = css[css.index(".feature-sheet {") : css.index("}", css.index(".feature-sheet {"))]
    for declaration in ("margin: 0", "max-width: none", "max-height: none", "bottom: 0", "height: 88dvh"):
        assert declaration in block, declaration
    assert re.search(r"\.feature-sheet\[open\]\s*\{[^}]*display: flex", css)


def test_reviews_page_has_the_create_anchor_the_sheet_links_to():
    html = _get("/reviews").text
    assert '<section id="create-section" class="create-section">' in html
    assert html.count('id="create-section"') == 1


def test_login_and_feature_login_allow_lists_match():
    login = _get("/static/login.js").text
    feature = _get("/static/feature-login.js").text
    pick = lambda text, name: re.findall(r'"(/[^"]*)"|REVIEWS_PATH', re.search(rf"const {name} = \[(.*?)\];", text, re.S).group(1))
    expected = ["/?open=search", "/?open=reviews", "/?open=reports", "/?open=approvals", "/?open=rules"]
    assert [item for item in pick(feature, "ALLOWED") if item] == ["/reviews", *expected]
    assert [item for item in pick(login, "ALLOWED_NEXT") if item] == expected  # REVIEWS_PATH 상수 + 5개
    assert "REVIEWS_PATH" in re.search(r"const ALLOWED_NEXT = \[(.*?)\];", login, re.S).group(1)


def test_strips_css_uses_tokens_only_has_book_motion_and_no_white_buttons():
    """AC-19: 흰색 바탕 버튼 없음, 팔레트는 토큰만, 책이 펼쳐지는 모션과 모션 줄이기."""
    css = _get("/static/feature-strips.css").text
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert "@import" not in css and "url(" not in css
    assert "rotateY(-82deg)" in css and "perspective:" in css and "transform-origin: left center" in css
    assert "prefers-reduced-motion" in css
    open_rule = css[css.index(".strip-open {") : css.index("}", css.index(".strip-open {"))]
    assert "background: transparent" in open_rule and "border: 1px solid var(--gold)" in open_rule and "color: var(--gold)" in open_rule
    assert "var(--on-dark)" not in open_rule
    # 시트의 강조 버튼도 같은 모양(흰색 바탕 없음)
    sheet = _get("/static/feature-sheet.css").text
    solid = sheet[sheet.index(".sheet-link-solid {") : sheet.index("}", sheet.index(".sheet-link-solid {"))]
    assert "background: transparent" in solid and "var(--on-dark)" not in solid
    # 펼친 면 폭은 컨테이너 기준으로 고정(띠가 변해도 글자가 흔들리지 않는다)
    assert "container-type: inline-size" in css and "100cqw" in css
    # login.css의 전역 button:hover(파란 바탕)가 띠 버튼에 적용되지 않는다
    assert ".strip-rail:hover:not(:disabled)" in css
    # 크기 규칙: 높이 25rem(배경 그래프 아래 ~ 자동재생 초록 버튼 아래), 닫힌 띠 3rem, 간격 .5rem, 왼쪽으로 8rem 넓힘
    assert "height: 25rem" in css and "margin-bottom: 1.6rem" in css and "--spine: 3rem" in css and "--gap: 0.5rem" in css and "margin-left: -8rem" in css


def test_strips_hide_list_inputs_and_actions_in_the_preview_and_depend_on_auth():
    css = _get("/static/feature-strips.css").text
    for hidden in (".strip-preview .sheet-rows", ".strip-preview .sheet-actions", ".strip-preview form"):
        assert hidden in css, hidden
    assert 'html[data-auth="authenticated"] .strip-note' in css
    assert 'html:not([data-auth="authenticated"]) .strip-preview' in css

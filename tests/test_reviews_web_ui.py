import pytest
from fastapi.testclient import TestClient

from backend import auth
from backend.main import app


def _get(path: str):
    client = TestClient(app, raise_server_exceptions=False)
    if path == "/reviews":
        client.cookies.set(auth.SESSION_COOKIE_NAME, auth.issue_session_token("00000000-0000-0000-0000-0000000000a1"))
    return client.get(path)


def _tag_containing(html: str, marker: str) -> str:
    """marker가 들어 있는 여는 태그 전체를 돌려준다."""
    at = html.index(marker)
    start = html.rindex("<", 0, at)
    return html[start : html.index(">", at) + 1]


def test_reviews_page_is_served_as_html():
    """유효한 세션을 가진 브라우저에 목록 HTML을 보낸다."""
    response = _get("/reviews")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert 'lang="ko"' in response.text


@pytest.mark.parametrize("session_state", ["missing", "invalid", "expired"])
def test_reviews_redirects_before_serving_html_without_a_valid_session(monkeypatch, session_state):
    client = TestClient(app)
    if session_state == "invalid":
        client.cookies.set(auth.SESSION_COOKIE_NAME, "invalid-session")
    elif session_state == "expired":
        monkeypatch.setattr(auth, "SESSION_TTL_SECONDS", -1)
        token = auth.issue_session_token("00000000-0000-0000-0000-0000000000a1")
        client.cookies.set(auth.SESSION_COOKIE_NAME, token)

    response = client.get("/reviews", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/?login_required=reviews"
    assert response.headers["cache-control"] == "no-store"
    assert 'id="reviews-list"' not in response.text
    assert client.get("/api/v1/reviews").status_code == 401

    landing = client.get(response.headers["location"]).text
    assert "로그인 후 이용해주십시오" in landing
    assert 'data-open="true"' in _tag_containing(landing, 'id="review-login-notice"')
    normal_landing = client.get("/").text
    assert 'data-open="true"' not in _tag_containing(normal_landing, 'id="review-login-notice"')


def test_reviews_page_has_list_regions_and_alert_areas():
    """목록·빈 목록·오류·더 보기 영역이 없으면 심사역이 상태를 알 수 없다(AC-01, AC-03, AC-11)."""
    html = _get("/reviews").text

    for element_id in ("reviews-list", "reviews-empty", "more-button", "reload-button", "reviews-loading"):
        assert f'id="{element_id}"' in html, element_id
    assert 'id="reviews-list"' in html and "<ul" in _tag_containing(html, 'id="reviews-list"')
    assert 'role="alert"' in _tag_containing(html, 'id="reviews-error"')
    assert 'role="alert"' in _tag_containing(html, 'id="ask-error"')
    assert 'role="status"' in _tag_containing(html, 'id="reviews-loading"')


def test_reviews_page_has_delete_confirmation_dialog():
    html = _get("/reviews").text

    assert '<dialog id="review-delete-dialog"' in html
    assert "해당 심사건 내역을 삭제하겠습니까?" in html
    assert 'id="review-delete-yes"' in html
    assert 'id="review-delete-no"' in html
    assert 'role="alert"' in _tag_containing(html, 'id="review-delete-error"')

    css = _get("/static/reviews.css").text
    assert ".review-item:hover > .review-delete-button" in css
    assert ".review-item:focus-within > .review-delete-button" in css
    assert "opacity: 0" in css


def test_bulk_delete_is_in_the_heading_with_a_visible_trash_icon():
    html = _get("/reviews").text
    heading = html[html.index('<div class="reviews-heading">') : html.index('id="reviews-error"')]
    button = _tag_containing(heading, 'id="review-delete-all"')
    assert 'aria-label="전체 심사 건 삭제"' in button
    assert "🗑</button>" in heading

    css = _get("/static/reviews.css").text
    rule = _rule(css, ".reviews-heading {")
    assert _declared(rule, "display") == "flex"
    assert _declared(rule, "justify-content") == "space-between"


def test_review_sort_uses_the_company_menu_next_to_the_heading():
    html = _get("/reviews").text
    heading = html[html.index('<div class="reviews-heading-title">') : html.index('id="review-delete-all"')]

    assert heading.index('<h2 id="reviews-history-title">심사 건 내역</h2>') < heading.index('id="reviews-sort"')
    assert 'aria-controls="reviews-sort-menu"' in heading
    assert 'id="reviews-sort-label">최신순<' in heading
    for label in ("최신순", "오래된순", "가나다순"):
        assert f">{label}<" in heading
    assert "/static/company-search.css" in html


def test_reviews_page_has_my_rules_section():
    """틀린 기억을 고치려면 내 판정 규칙을 보고 지울 자리가 있어야 한다(rules-web-ui AC-01)."""
    html = _get("/reviews").text

    section = html[html.index('id="rules-section"') : html.index("</section>", html.index('id="rules-section"'))]
    assert "내 판정 규칙" in section
    assert "<ul" in _tag_containing(html, 'id="rules-list"')
    assert 'role="alert"' in _tag_containing(html, 'id="rules-error"')
    assert " hidden" in _tag_containing(html, 'id="rules-error"')
    assert 'role="status"' in _tag_containing(html, 'id="rules-status"')
    assert (
        html.index("<h1>심사 건</h1>")
        < html.index('id="ask-form"')
        < html.index('id="reviews-history-title"')
        < html.index('id="reviews-list"')
        < html.index('id="rules-section"')
    )


def test_reviews_page_asks_with_a_question_only_and_has_no_company_picker():
    """회사·사업연도를 고르는 칸 없이 질문만 적어 보낸다(SPEC §3.3 바로 질문). 질문 길이는 화면 상한 500자."""
    html = _get("/reviews").text

    assert 'id="ask-form"' in html
    # 브라우저 기본 검증 말풍선 대신 화면 문구를 보이려고 novalidate를 쓴다.
    assert "novalidate" in _tag_containing(html, 'id="ask-form"')
    assert '<label for="ask-question">' in html
    question = _tag_containing(html, 'id="ask-question"')
    assert question.startswith("<textarea")
    assert 'class="review-question"' in question
    assert 'maxlength="500"' in question
    assert "required" in question
    assert html.index('id="ask-question"') < html.index("<summary>질문 예시</summary>") < html.index('id="ask-submit"')
    for label in ("단기차입금", "주요 소송", "지급보증"):
        assert 'type="button"' in _tag_containing(html, f'">{label}</button>')
    assert 'type="submit"' in _tag_containing(html, 'id="ask-submit"')
    for removed in ('id="create-form"', 'id="corp-query"', 'id="fiscal-year"', 'id="candidates"'):
        assert removed not in html, removed


def test_reviews_page_result_area_and_logout_controls_start_in_the_right_state():
    """심사 건 없이 끝난 응답을 보일 자리는 처음에 숨겨 두고, 로그아웃 버튼은 폼을 제출하지 않아야 한다(AC-13)."""
    html = _get("/reviews").text

    assert " hidden" in _tag_containing(html, 'id="ask-result"')
    assert 'role="status"' in _tag_containing(html, 'id="ask-result"')
    assert " hidden" in _tag_containing(html, 'id="ask-result-news"')
    logout = _tag_containing(html, 'id="logout-button"')
    assert 'type="button"' in logout


def test_reviews_page_loads_its_assets_from_static():
    """화면 스크립트와 스타일이 같은 출처의 /static에서 제공돼야 한다."""
    html = _get("/reviews").text

    assert "/static/reviews.js" in html
    assert 'type="module"' in html
    assert "/static/reviews.css" in html
    assert "/static/login.css" in html
    assert _get("/static/reviews.js").status_code == 200
    assert _get("/static/reviews.css").status_code == 200


def test_reviews_script_never_writes_server_strings_as_html():
    """서버 문자열을 HTML로 해석하면 회사명으로 스크립트가 실행될 수 있다(AC-12)."""
    response = _get("/static/reviews.js")

    assert response.status_code == 200
    script = response.text
    assert "innerHTML" not in script
    assert "insertAdjacentHTML" not in script
    assert "outerHTML" not in script


def test_login_page_is_for_signed_out_users_only():
    """첫 화면에는 로그아웃 제어가 없고, 심사 건 목록 링크는 누구나 볼 수 있다."""
    html = _get("/").text

    assert 'id="session-panel"' not in html
    assert 'id="logout-button"' not in html
    assert 'href="/reviews">심사 건 확인</a>' in html


def test_reviews_page_shows_the_user_name_in_the_header():
    """로그인한 사용자가 누구인지 /reviews 상단에서 보여야 한다(AC-14)."""
    html = _get("/reviews").text

    assert 'id="current-user"' in html
    assert html.index('id="current-user"') < html.index('id="logout-button"')


def test_reviews_styles_wrap_long_values_inside_the_card():
    """공백 없는 긴 회사명이 카드 밖으로 밀려 나가면 좁은 화면에서 잘려 보인다."""
    css = _get("/static/reviews.css").text

    assert "overflow-wrap: anywhere" in css
    assert "minmax(0, 1fr)" in css


def test_reviews_header_shows_the_logo_before_the_title():
    """심사 건 화면도 CREDIT PULSE 로고로 같은 서비스임을 보여야 한다(AC-08)."""
    html = _get("/reviews").text

    logo = _tag_containing(html, 'class="app-logo"')
    assert 'alt="CREDIT PULSE"' in logo
    assert "/static/credit-pulse-logo-white.png" in logo
    assert html.index('class="app-logo"') < html.index("<h1>")
    assert "CREDIT PULSE" in html[html.index("<title>") : html.index("</title>")]


def _rule(css: str, selector: str) -> str:
    start = css.index(selector)
    return css[start : css.index("}", start)]


def _declared(rule: str, prop: str) -> str:
    import re

    values = re.findall(rf"(?<![\w-]){prop}:\s*([^;}}]+)", rule)
    assert values, f"{prop}가 없습니다: {rule}"
    return values[-1].strip()


def test_reviews_page_shares_the_theme_with_the_main_page():
    """두 화면이 같은 배경·테마 파일을 써서 한 서비스로 보인다(AC-22)."""
    for path in ("/", "/reviews"):
        html = _get(path).text
        assert "/static/theme.css" in html, path
        body_tag = html[html.index("<body") : html.index(">", html.index("<body")) + 1]
        assert "glass-page" in body_tag, path
        start = html.index('class="page-bg"')
        assert 'aria-hidden="true"' in html[start - 40 : start + 60], path
        assert 'class="bg-photo"' in html, path

    theme = _get("/static/theme.css").text
    assert "--glass-bg:" in theme and "--on-dark:" in theme
    assert "--mono:" in theme
    page_bg = _rule(theme, ".page-bg {")
    assert _declared(page_bg, "position") == "fixed"
    assert _declared(page_bg, "inset") == "0"
    assert "url(" not in theme


def test_reviews_page_uses_glass_panels_and_light_typography():
    """심사 건 화면도 유리 패널과 얇은 제목·모노 라벨을 쓴다(AC-23)."""
    css = _get("/static/reviews.css").text
    for selector in (".app-bar {", ".reviews-page {"):
        rule = _rule(css, selector)
        assert _declared(rule, "background").startswith("var(--glass-bg"), selector
    # 상단 바만 블러를 쓰고, 스크롤되는 큰 카드(.reviews-page)에는 걸지 않는다(AC-40)
    assert _declared(_rule(css, ".app-bar {"), "backdrop-filter") in ("var(--glass-blur)",)
    assert "backdrop-filter" not in _rule(css, ".reviews-page {")
    assert "rgba(255, 255, 255" in _declared(_rule(css, ".review-item {"), "background")
    assert _declared(_rule(css, ".reviews-page h1 {"), "font-weight") == "300"
    for selector in (".review-code,", ".review-meta {"):
        rule = _rule(css, selector)
        assert "font-family" not in rule and "text-transform" not in rule, selector


def test_reviews_controls_are_pills_with_touch_size():
    """버튼과 입력칸을 메인처럼 알약 모양·44px 이상으로 맞춘다(AC-30)."""
    css = _get("/static/reviews.css").text
    for selector in (
        ".reviews-page button:where(:not(.cs-sort button)) {",
        ".reviews-user button {",
        ".reviews-page input,",
        ".reviews-page select {",
    ):
        rule = _rule(css, selector)
        assert _declared(rule, "border-radius") == "999px", selector
        height = _declared(rule, "min-height")
        assert height.endswith("rem") and float(height[:-3]) >= 2.75, selector


def test_reviews_cards_are_separated_by_shadow_and_create_form_is_a_card():
    """목록 카드는 그림자로, 새 심사 건 영역은 별도 카드로 구분한다(AC-30)."""
    css = _get("/static/reviews.css").text
    assert _declared(_rule(css, ".review-item {"), "box-shadow").startswith("0 ")
    create = _rule(css, ".create-section {")
    assert "rgba(255, 255, 255" in _declared(create, "background")
    assert float(_declared(create, "border-radius").removesuffix("px")) >= 16
    assert _declared(create, "box-shadow").startswith("0 ")
    assert "border-top" not in create

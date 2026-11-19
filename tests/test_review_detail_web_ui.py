import re

from fastapi.testclient import TestClient

from backend import auth
from backend.main import app

REVIEW_ID = "0b1f6c1e-8a53-4e0a-9f55-0f2f6f0f9a11"


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


def _rule(css: str, selector: str) -> str:
    start = css.index(selector)
    return css[start : css.index("}", start)]


def _declared(rule: str, prop: str) -> str:
    """같은 규칙 안에서는 나중 선언이 이기므로 마지막 값을 돌려준다."""
    values = re.findall(rf"(?<![\w-]){prop}:\s*([^;}}]+)", rule)
    assert values, f"{prop}가 없습니다: {rule}"
    return values[-1].strip()


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# AC-01
def test_detail_page_is_served_as_html_for_any_id():
    """쉘 HTML은 공개이고 인증·존재 여부는 API가 판단한다. 어떤 id든 화면은 200이다."""
    for path in (f"/reviews/{REVIEW_ID}", "/reviews/not-a-uuid"):
        response = _get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("text/html"), path
        assert 'lang="ko"' in response.text, path
    assert _get("/reviews").status_code == 200


def test_detail_page_has_summary_query_form_and_result_regions():
    html = _get(f"/reviews/{REVIEW_ID}").text

    for element_id in (
        "detail-loading", "detail-error", "detail-reload", "detail-missing", "detail-body",
        "review-name", "review-code", "review-year", "review-status", "review-count", "review-created",
        "query-section", "query-form", "question", "query-submit", "query-error", "query-hint",
        "query-scope", "review-conversation", "review-turn-template",
        "current-user", "logout-button", "back-link",
    ):
        assert f'id="{element_id}"' in html, element_id
    assert 'role="alert"' in _tag_containing(html, 'id="detail-error"')
    assert 'role="alert"' in _tag_containing(html, 'id="query-error"')
    assert 'role="status"' in _tag_containing(html, 'id="detail-loading"')
    assert "<ul" in _tag_containing(html, 'data-result="drafts"')
    assert 'role="log"' in _tag_containing(html, 'id="review-conversation"')
    assert html.index('id="review-conversation"') < html.index('id="query-form"')
    assert "<textarea" in _tag_containing(html, 'id="question"')
    assert 'maxlength="500"' in _tag_containing(html, 'id="question"')
    assert "required" in _tag_containing(html, 'id="question"')
    assert '<label for="question">추가 질문을 입력해 주세요 (최대 500자)</label>' in html
    assert "novalidate" in _tag_containing(html, 'id="query-form"')
    assert 'type="submit"' in _tag_containing(html, 'id="query-submit"')
    assert 'type="button"' in _tag_containing(html, 'id="logout-button"')
    assert 'href="/reviews"' in _tag_containing(html, 'id="back-link"')
    for element_id in ("detail-error", "detail-reload", "detail-missing", "detail-body", "query-error", "query-hint", "review-conversation"):
        assert " hidden" in _tag_containing(html, f'id="{element_id}"'), element_id
    assert 'href="/reviews"' in html[html.index('id="detail-missing"') :]


def test_original_question_has_its_own_card_before_the_company_summary():
    html = _get(f"/reviews/{REVIEW_ID}").text
    question_card = _tag_containing(html, 'id="approval-question"')
    assert question_card.startswith("<section") and 'class="detail-card approval-question"' in question_card
    assert (
        html.index("<h1>심사 건 상세</h1>")
        < html.index('id="approval-question"')
        < html.index('id="detail-body"')
        < html.index('id="approval-section"')
    )


def test_detail_page_loads_assets_theme_and_logo():
    html = _get(f"/reviews/{REVIEW_ID}").text
    list_html = _get("/reviews").text

    assert "/static/review-detail.js" in html
    assert 'type="module"' in html
    for asset in ("review-detail.css", "login.css", "theme.css", "fonts.css"):
        assert f"/static/{asset}" in html, asset
    assert _get("/static/review-detail.js").status_code == 200
    assert _get("/static/review-detail.css").status_code == 200
    body_tag = html[html.index("<body") : html.index(">", html.index("<body")) + 1]
    assert "glass-page" in body_tag and "ui-sans" in body_tag
    logo = _tag_containing(html, 'class="app-logo"')
    assert 'alt="CREDIT PULSE"' in logo
    assert 'credit-pulse-logo-white.png' in logo
    assert logo == _tag_containing(list_html, 'class="app-logo"')
    for font in ("NotoSerifKR-subset.woff2", "CreditPulseSans-subset.woff2"):
        assert font in html and _get(f"/static/fonts/{font}").status_code == 200
    assert html.index('class="app-logo"') < html.index("<h1")
    assert "CREDIT PULSE" in html[html.index("<title>") : html.index("</title>")]
    assert "<noscript>" in html


# AC-02
def test_reviews_script_links_each_review_to_its_detail_page():
    script = _get("/static/reviews.js").text

    assert "/reviews/" in script
    assert "encodeURIComponent" in script


# AC-15
def test_detail_styles_match_the_dark_glass_theme_and_touch_size():
    css = _get("/static/review-detail.css").text

    for selector in (".detail-card {", ".result-card {"):
        rule = _rule(css, selector)
        assert "rgba(255, 255, 255" in _declared(rule, "background"), selector
        assert _declared(rule, "backdrop-filter").startswith("blur("), selector
        assert _declared(rule, "color") == "var(--on-dark)", selector
        assert _declared(rule, "border") == "1px solid var(--glass-line)", selector
    question = _rule(_get("/static/reviews.css").text, ".review-question {")
    assert 'class="review-question"' in _tag_containing(_get("/reviews/r-1").text, 'id="question"')
    for rule in (_rule(css, ".detail-page button {"), question):
        height = _declared(rule, "min-height")
        assert height.endswith("rem") and float(height[:-3]) >= 2.75
    assert _declared(_rule(css, ".detail-page button {"), "border-radius") == "999px"
    assert _declared(question, "color") == "var(--on-dark)"
    assert _declared(question, "background").startswith("rgba(255, 255, 255, 0.08)")
    for selector in (".detail-meta {", ".citation-meta {"):
        rule = _rule(css, selector)
        assert "font-family" not in rule, selector
        assert _declared(rule, "font-variant-numeric") == "tabular-nums", selector
    assert "overflow-wrap: anywhere" in css
    assert "minmax(0, 1fr)" in css or "min-width: 0" in css
    assert "prefers-reduced-motion" in css
    assert "url(" not in css


def test_verdict_badges_have_readable_contrast_and_text_labels():
    css = _get("/static/review-detail.css").text

    for name in ("applicable", "not_applicable", "needs_check", "unknown"):
        selector = f".verdict-{name.replace('_', '-')} {{"
        rule = _rule(css, selector)
        foreground = _declared(rule, "color")
        background = _declared(rule, "background-color")
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", foreground), selector
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", background), selector
        assert _contrast(foreground, background) >= 4.5, selector


def test_hidden_regions_stay_hidden_despite_display_rules():
    css = _get("/static/review-detail.css").text

    assert "[hidden]" in css
    assert "display: none" in css[css.index("[hidden]") :]


# AC-16
def test_detail_script_has_no_html_writes_eval_external_urls_or_llm_calls():
    response = _get("/static/review-detail.js")
    assert response.status_code == 200
    script = response.text

    for banned in ("innerHTML", "insertAdjacentHTML", "outerHTML", "eval(", "new Function", "document.write"):
        assert banned not in script, banned
    assert not re.search(r"https?://", script)
    for banned in ("openrouter", "anthropic", "OPENROUTER", "api_key", "apiKey"):
        assert banned not in script, banned
    css_response = _get("/static/review-detail.css")
    assert css_response.status_code == 200
    css = css_response.text
    assert not re.search(r"https?://", css)
    html = _get(f"/reviews/{REVIEW_ID}").text
    for url in re.findall(r'(?:src|href)="(https?://[^"]+)"', html):
        assert url.startswith("http://testserver/static/"), url
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert "style=" not in html


# approval-ui AC-01 / AC-12
def test_detail_page_has_approval_panel_regions():
    html = _get(f"/reviews/{REVIEW_ID}").text

    for element_id in (
        "approval-section", "approval-summary", "approval-list", "approval-empty",
        "approval-error", "approval-done", "approval-submit", "approval-cancel",
        "approval-rules", "approval-rules-list",
    ):
        assert f'id="{element_id}"' in html, element_id
    for element_id in ("approval-section", "approval-empty", "approval-error", "approval-done", "approval-rules"):
        assert " hidden" in _tag_containing(html, f'id="{element_id}"'), element_id
    assert 'role="alert"' in _tag_containing(html, 'id="approval-error"')
    assert 'role="status"' in _tag_containing(html, 'id="approval-done"')
    assert 'type="button"' in _tag_containing(html, 'id="approval-submit"')
    assert 'type="button"' in _tag_containing(html, 'id="approval-cancel"')
    # 오류·완료 안내는 패널이 닫혀도 보여야 하므로 패널 밖에 둔다.
    panel_start = html.index('id="approval-section"')
    panel_end = html.index("</section>", html.index('id="approval-cancel"'))
    assert 'id="approval-error"' not in html[panel_start:panel_end]
    assert 'id="approval-done"' not in html[panel_start:panel_end]
    assert panel_start < html.index('id="approval-rules"') < panel_end
    assert 'href="/reviews#rules-section"' in html
    assert "준비 중" not in html


def test_approval_assets_are_served():
    for asset in ("approval-panel.js", "review-shared.js"):
        response = _get(f"/static/{asset}")
        assert response.status_code == 200, asset
        assert "javascript" in response.headers["content-type"], asset


def test_approval_styles_keep_touch_size_badge_contrast_and_stacking():
    css = _get("/static/review-detail.css").text

    radio = _rule(css, ".approval-radio {")
    height = _declared(radio, "min-height")
    assert height.endswith("rem") and float(height[:-3]) >= 2.75
    badge = _rule(css, ".edited-badge {")
    assert _contrast(_declared(badge, "color"), _declared(badge, "background-color")) >= 4.5
    assert "minmax(0, 1fr)" in _rule(css, ".approval-list {")
    assert _declared(_rule(css, ".approval-item {"), "min-width") == "0"
    legend = _rule(css, ".approval-legend {")
    assert _declared(legend, "float") == "left"
    assert _declared(legend, "width") == "100%"
    assert _declared(_rule(css, ".approval-verdicts {"), "clear") == "both"


# approval-ui AC-13
def test_approval_scripts_have_no_html_writes_eval_secrets_or_out_of_scope_calls():
    for name in ("approval-panel.js", "review-shared.js", "review-detail.js"):
        script = _get(f"/static/{name}").text
        for banned in ("innerHTML", "insertAdjacentHTML", "outerHTML", "eval(", "new Function", "document.write"):
            assert banned not in script, (name, banned)
        assert not re.search(r"https?://", script), name
        for banned in ("openrouter", "anthropic", "OPENROUTER", "api_key", "apiKey"):
            assert banned not in script, (name, banned)
        for endpoint in ("/rules", "/memos", "/chunks"):
            assert endpoint not in script, (name, endpoint)
    panel = _get("/static/approval-panel.js").text
    for banned in ("approved_by", "user_id", "approver"):
        assert banned not in panel, banned


def test_detail_page_has_a_hidden_consulted_rules_area_inside_the_result():
    """질의 결과에 이번 초안이 참고한 내 규칙을 보이고 관리 화면으로 보낼 자리가 있어야 한다(AC-17)."""
    html = _get(f"/reviews/{REVIEW_ID}").text

    area = _tag_containing(html, 'data-result="rules"')
    assert " hidden" in area
    assert "<ul" in _tag_containing(html, 'data-result="rules-list"')
    block = html[html.index('data-result="rules"') : html.index('data-result="drafts"')]
    assert "이번 초안에 참고한 내 규칙" in block
    assert 'href="/reviews"' in block
    # "적용됨"이라고 쓰지 않는다: 참고로 넘긴 규칙이지 판정을 바꿨다는 증거가 아니다.
    assert "적용" not in block
    assert html.index('data-result="answer"') < html.index('data-result="rules"') < html.index('data-result="drafts"')

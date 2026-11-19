"""docs/specs/landing-company-search.md: 02 기업 검색 패널의 마크업·정적 파일 계약."""
import re

from fastapi.testclient import TestClient

from backend.main import app


def _get(path: str):
    return TestClient(app, raise_server_exceptions=False).get(path)


def _panel():
    html = _get("/").text
    return html[html.index('id="stage-panel-1"') : html.index('id="features"')]


def test_existing_search_form_ids_are_kept_so_the_slide_filter_and_01_jump_still_work():
    """AC-08·09: landing.js(#company-search/#company-query)와 stage-tabs.js(제출 → 01)가 쓰는 id 유지."""
    panel = _panel()
    assert '<form id="company-search" class="search-bar" role="search" novalidate data-rise>' in panel
    assert re.search(r'<input id="company-query" name="q" type="search"[^>]*maxlength="50"[^>]*aria-describedby="company-search-help">', panel)
    assert '<button id="company-search-submit" class="pill pill-dark" type="submit" title="보유 기업 화면에서 결과를 슬라이드로 봅니다">검색</button>' in panel
    assert 'id="company-search-help" class="visually-hidden"' in panel
    assert "LG화학, 00356361" in panel  # 예시는 입력칸 안내 문구


def test_results_area_has_list_chips_summary_and_a_listbox_sort_menu():
    panel = _panel()
    for ident in ("cs-chips", "cs-title", "cs-list", "cs-empty", "cs-status", "cs-count", "cs-count-label", "cs-of", "cs-hist", "cs-latest", "cs-peak"):
        assert panel.count(f'id="{ident}"') == 1, ident
    assert 'id="company-results" class="cs-results" data-rise' in panel
    assert re.search(r'<ul id="cs-list" class="cs-list" tabindex="0" aria-label="보유 기업 목록"></ul>', panel)
    assert 'id="cs-status" class="visually-hidden" role="status" aria-live="polite"' in panel
    assert 'id="cs-hist" class="cs-hist" role="img"' in panel
    # 정렬 드롭다운(시안 ②)
    button = re.search(r'<button id="cs-sort"[^>]*>', panel).group(0)
    assert 'aria-haspopup="listbox"' in button and 'aria-expanded="false"' in button and 'aria-controls="cs-sort-menu"' in button
    menu = re.search(r'<ul id="cs-sort-menu" class="cs-sort-menu" role="listbox"[^>]*hidden>(.*?)</ul>', panel, re.S).group(1)
    options = re.findall(r'<button type="button" role="option" aria-selected="(\w+)" data-sort="(\w+)">([^<]+)<', menu)
    assert options == [("true", "recent", "최신순"), ("false", "oldest", "오래된순"), ("false", "name", "가나다순")]
    assert 'id="cs-sort-label">최신순<' in panel
    # 체크 표시는 모든 항목에 있고 CSS가 선택된 항목만 보이게 한다(선택을 따라 움직인다)
    assert menu.count('class="cs-check" aria-hidden="true"><svg') == 3
    assert "feature-tile" not in panel and "innerHTML" not in panel


def test_search_panel_assets_are_loaded_and_script_never_writes_html():
    html = _get("/").text
    assert re.search(r'<link rel="stylesheet" href="[^"]*/static/company-search\.css">', html)
    assert re.search(r'<script type="module" src="[^"]*/static/company-search\.js"></script>', html)
    script = _get("/static/company-search.js").text
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "fetch(", "XMLHttpRequest", "http://", "https://"):
        assert forbidden not in script, forbidden
    assert 'from "./landing-data.js"' in script


def test_search_panel_css_uses_tokens_keeps_a_fixed_list_height_and_respects_reduced_motion():
    css = _get("/static/company-search.css").text
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)  # 팔레트는 theme.css 토큰만
    assert "@import" not in css and "url(" not in css
    assert "var(--gold)" in css and "var(--glass-bg-strong)" in css and "var(--on-dark)" in css
    list_rule = css[css.index(".cs-list {") : css.index("}", css.index(".cs-list {"))]
    assert "max-height: 10.6rem" in list_rule and "overflow-y: auto" in list_rule and "color-scheme: dark" in list_rule
    # 목록 한 줄은 이름 / corp_code / 공시일 3열
    assert "grid-template-columns: minmax(0, 1fr) 5.3rem 5.9rem" in css
    assert "@media (max-width: 52rem)" in css and "@media (prefers-reduced-motion: reduce)" in css
    assert ".cs-check {" in css and "visibility: hidden" in css[css.index(".cs-check {") : css.index("}", css.index(".cs-check {"))]
    assert '.cs-sort-menu button[aria-selected="true"] .cs-check {' in css
    # 정렬 메뉴는 흰색 바탕을 쓰지 않는다
    menu_rule = css[css.index(".cs-sort-menu {") : css.index("}", css.index(".cs-sort-menu {"))]
    assert "var(--glass-bg-strong)" in menu_rule and "var(--on-dark)" not in menu_rule

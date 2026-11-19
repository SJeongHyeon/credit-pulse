from fastapi.testclient import TestClient

from backend import auth
from backend.main import app


def test_root_serves_login_form():
    """GET /가 심사역이 로그인할 수 있는 HTML 폼을 제공해야 한다."""
    response = TestClient(app, raise_server_exceptions=False).get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'id="login-form"' in response.text
    assert 'id="email"' in response.text
    assert 'id="password"' in response.text


def test_homepage_and_review_pages_link_in_both_directions():
    home = TestClient(app, raise_server_exceptions=False).get("/").text
    actions = home[home.index('class="hero-actions"') : home.index("</div>", home.index('class="hero-actions"'))]
    assert actions.index('id="hero-login"') < actions.index('href="#companies"') < actions.index('href="/reviews"')
    assert 'class="pill pill-ghost" href="/reviews">심사 건 확인</a>' in actions

    for path in ("/reviews", "/reviews/example"):
        html = _get(path).text
        header = html[html.index('class="app-bar"') : html.index("</header>")]
        assert 'class="app-logo-link" href="/"' in header
        assert 'class="app-logo"' in header


def test_first_request_status_is_visible_outside_the_login_dialog():
    html = TestClient(app, raise_server_exceptions=False).get("/").text
    startup = _tag_containing(html, 'id="service-startup"')

    assert 'role="status"' in startup
    assert 'aria-live="polite"' in startup
    assert html.index('id="service-startup"') < html.index('<dialog id="login-dialog"')
    assert '#service-startup { display: none; }' in html


def test_login_page_has_accessible_form_and_no_password_value():
    """입력 접근성 또는 오류 안내를 없애면 심사역이 로그인할 수 없어야 한다."""
    response = TestClient(app, raise_server_exceptions=False).get("/")

    assert '<label for="email">이메일</label>' in response.text
    assert 'name="email"' in response.text
    assert 'autocomplete="username"' in response.text
    assert '<label for="password">비밀번호</label>' in response.text
    assert 'name="password"' in response.text
    assert 'autocomplete="current-password"' in response.text
    assert 'id="login-error"' in response.text
    # 로그인 뒤에는 /reviews로 이동하므로 이 화면에 로그인 상태 영역과 로그아웃 버튼이 없어야 한다(AC-01).
    assert 'id="session-panel"' not in response.text
    assert 'id="logout-button"' not in response.text
    assert 'id="password" type="password" value=' not in response.text


def test_login_form_fallback_uses_post_not_get():
    """JavaScript가 실행되지 않아도 비밀번호가 URL 쿼리로 전송되면 안 된다."""
    response = TestClient(app, raise_server_exceptions=False).get("/")

    assert 'method="post"' in response.text


def test_login_styles_keep_hidden_regions_invisible():
    """로그인 성공 뒤 hidden 상태인 폼은 CSS 때문에 다시 표시되면 안 된다."""
    response = TestClient(app, raise_server_exceptions=False).get("/static/login.css")

    assert response.status_code == 200
    assert "[hidden]" in response.text
    assert "display: none !important" in response.text


def test_error_region_is_an_alert_outside_the_form():
    """폼이 숨겨져도 오류가 보이고 스크린리더가 즉시 읽어야 한다(AC-06, AC-09)."""
    html = TestClient(app, raise_server_exceptions=False).get("/").text

    assert 'id="login-error"' in html
    assert 'role="alert"' in html
    assert html.index('id="login-error"') < html.index('id="login-form"')


def test_form_starts_hidden_with_status_and_noscript_guidance():
    """첫 응답 전에는 폼이 깜빡이지 않고 확인 중만 보이며, JS가 꺼지면 이유를 알려야 한다(AC-08)."""
    html = TestClient(app, raise_server_exceptions=False).get("/").text

    form_tag = _tag_containing(html, 'id="login-form"')
    assert " hidden" in form_tag
    assert 'id="login-status"' in html
    assert 'role="status"' in html
    assert "<noscript>" in html


def test_password_toggle_is_a_button_bound_to_the_input():
    """비밀번호 표시 전환은 폼을 제출하지 않는 버튼이고 입력 칸과 연결돼야 한다(AC-12)."""
    html = TestClient(app, raise_server_exceptions=False).get("/").text

    tag_start = html.index('id="toggle-password"')
    tag = html[html.rindex("<button", 0, tag_start) : html.index(">", tag_start) + 1]
    assert 'type="button"' in tag
    assert 'aria-controls="password"' in tag
    assert "aria-pressed" not in tag


def test_login_styles_fit_narrow_screens():
    """좁은 화면에서 큰 위아래 여백과 고정 폭 때문에 폼이 잘리면 안 된다."""
    css = TestClient(app, raise_server_exceptions=False).get("/static/login.css").text

    assert "margin: 8rem auto" not in css
    assert "box-sizing: border-box" in css


# ---- 메인 화면 디자인(docs/specs/main-page-design.md) ----


def _get(path: str):
    client = TestClient(app, raise_server_exceptions=False)
    if path == "/reviews":
        client.cookies.set(auth.SESSION_COOKIE_NAME, auth.issue_session_token("00000000-0000-0000-0000-0000000000a1"))
    return client.get(path)


def _png_info(data: bytes) -> tuple[int, int, int]:
    """PNG 헤더에서 (가로, 세로, 색 유형)을 읽는다. 색 유형 6이 RGBA(투명 배경)다."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height, data[25]


def _tag_containing(html: str, marker: str) -> str:
    at = html.index(marker)
    start = html.rindex("<", 0, at)
    return html[start : html.index(">", at) + 1]


def test_logo_files_are_transparent_pngs_matching_template_size():
    """배경이 있는 로고는 카드 위에서 상자로 보인다. 알파 채널과 크기 속성이 맞아야 한다(AC-02)."""
    login_html = _get("/").text
    reviews_html = _get("/reviews").text

    assert _get("/static/credit-pulse-logo.png").status_code == 200
    for name, html in (("credit-pulse-logo-white.png", login_html), ("credit-pulse-logo-white.png", reviews_html)):
        response = _get(f"/static/{name}")
        assert response.status_code == 200, name
        assert response.headers["content-type"] == "image/png", name
        width, height, color_type = _png_info(response.content)
        assert color_type == 6, f"{name}은 알파 채널이 있는 PNG여야 한다"
        tag = _tag_containing(html, 'alt="CREDIT PULSE"')  # 상단 로고 <img> (인트로의 data-logo가 아니라)
        assert name in tag and tag.startswith("<img"), name
        assert f'width="{width}"' in tag and f'height="{height}"' in tag, name


def test_pages_and_styles_use_no_external_resources():
    """외부 글꼴·CDN을 쓰면 폐쇄망·평가 환경에서 깨진다(AC-04). 같은 서버의 정적 주소는 허용한다."""
    import re

    bodies = [_get(path).text for path in ("/", "/reviews", "/static/login.css", "/static/landing.css", "/static/reviews.css", "/static/landing.js", "/static/landing-data.js", "/static/hero-stats.js")]

    for body in bodies:
        for url in re.findall(r"https?://[^\s\"')]+", body):
            assert url.startswith("http://testserver/static/"), url


def _rule(css: str, selector: str) -> str:
    """selector로 시작하는 규칙 블록의 본문을 돌려준다."""
    start = css.index(selector)
    return css[start : css.index("}", start)]


def test_main_page_has_info_slider_search_and_dummy_feature_tiles():
    """첫 화면에 정보 슬라이드, 검색, 비활성 기능 칸이 있어야 한다(AC-01, AC-13)."""
    html = _get("/").text

    viewport = _tag_containing(html, 'id="slide-viewport"')
    assert 'aria-live="polite"' in viewport
    for button_id, label in (("slide-prev", "이전 기업"), ("slide-next", "다음 기업")):
        tag = _tag_containing(html, f'id="{button_id}"')
        assert 'type="button"' in tag
        assert f'aria-label="{label}"' in tag
    for element_id in ("slide-position", "slide-progress", "slide-empty"):
        assert f'id="{element_id}"' in html, element_id
    assert 'role="progressbar"' in _tag_containing(html, 'id="slide-progress"')
    assert 'id="slide-progress-fill"' in html
    assert "slide-dots" not in html
    assert " hidden" in _tag_containing(html, 'id="slide-empty"')

    form = _tag_containing(html, 'id="company-search"')
    assert 'role="search"' in form
    assert '<label for="company-query"' in html
    query = _tag_containing(html, 'id="company-query"')
    assert 'type="search"' in query
    assert 'maxlength="50"' in query
    assert 'type="submit"' in _tag_containing(html, 'id="company-search-submit"')

    import re

    labels = ["공시 검색", "리스크 점검", "보고서", "승인 현황", "내 판정 규칙"]  # 개정 8 AC-60
    strips = html[html.index('id="feature-strips"') : html.index('id="login-required-hint"')]  # 03 패널의 띠 5개(시안 D)
    assert len(re.findall(r'<a class="strip-open" href="/\?open=\w+" data-feature="\w+">', strips)) == 5  # 모든 띠에 「열기」
    assert "feature-tile" not in html and 'type="button" disabled' not in strips  # 준비 중·흰색 칸 없음
    assert "재무 요약" not in html and ">설정<" not in strips
    for label in labels:
        assert label in html, label


def test_landing_script_is_loaded_and_never_writes_html():
    """기업 정보와 검색어를 HTML로 해석하면 화면에서 스크립트가 실행될 수 있다(AC-16)."""
    html = _get("/").text

    assert "/static/landing.js" in html
    assert 'type="module"' in html
    for name in ("landing.js", "landing-data.js"):
        response = _get(f"/static/{name}")
        assert response.status_code == 200, name
    script = _get("/static/landing.js").text
    assert "innerHTML" not in script
    assert "insertAdjacentHTML" not in script
    assert "outerHTML" not in script


def test_landing_company_list_matches_the_service_corpus():
    """첫 화면의 보유 기업은 데모 16곳이 아니라 서비스 manifest의 활성 기업과 일치한다."""
    import json
    import re
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "data/manifest.jsonl"
    expected = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("status") != "success" or row.get("report_type") != "annual" or row.get("superseded"):
            continue
        code = row["corp_code"]
        current = expected.get(code)
        if current is None or (row["fiscal_year"], row["filed_at"], row["rcept_no"]) > current[0]:
            expected[code] = (
                (row["fiscal_year"], row["filed_at"], row["rcept_no"]),
                (row["corp_name"], row["fiscal_year"],
                 f"{row['filed_at'][:4]}-{row['filed_at'][4:6]}-{row['filed_at'][6:]}"),
            )
    expected = {code: value for code, (_, value) in expected.items()}

    text = _get("/static/landing-data.js").text
    entries = re.findall(
        r'\{\s*name:\s*"([^"]+)",\s*corp_code:\s*"(\d{8})",\s*fiscal_year:\s*(\d{4}),\s*filed_at:\s*"(\d{4}-\d{2}-\d{2})"',
        text,
    )
    actual = {code: (name, int(year), filed) for name, code, year, filed in entries}
    assert len(entries) == len(actual), "corp_code가 중복돼 있습니다"
    assert len(actual) == len(expected)
    assert actual == expected


def test_main_page_background_is_a_moving_desk_photo():
    """배경은 책상 사진이고 천천히 움직인다. 마우스 반응은 CSS 변수로만 전달한다(AC-15)."""
    html = _get("/").text
    css = _get("/static/theme.css").text

    body_tag = html[html.index("<body") : html.index(">", html.index("<body")) + 1]
    assert "glass-page" in body_tag
    background = _tag_containing(html, 'class="page-bg"')
    assert 'aria-hidden="true"' in background
    start = html.index('class="page-bg"')
    assert start < html.index('class="bg-photo"') < html.index('class="site-bar"')
    photo = _tag_containing(html, 'class="bg-photo"')
    assert "/static/desk-bg.webp" in photo and 'alt=""' in photo
    for layer in ("bg-shade", "bg-light", "bg-spot"):
        assert f'class="{layer}"' in html, layer
    assert _get("/static/desk-bg.webp").status_code == 200
    assert "bg-motion.js" in html
    page_bg = _rule(css, ".page-bg {")
    assert _declared(page_bg, "position") == "fixed"
    assert _declared(page_bg, "inset") == "0"
    assert "@keyframes bg-drift" in css and "@keyframes bg-sweep" in css
    reduced = css[css.index("prefers-reduced-motion") :]
    assert "animation: none" in reduced


def test_dark_theme_text_colors_meet_wcag_aa_on_glass():
    """어두운 유리 바탕 위 글자·포인트 색의 대비가 4.5:1 이상이다(AC-35)."""
    import re

    css = _get("/static/theme.css").text
    colors = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", css))
    for name in ("on-dark", "on-dark-muted", "gold"):
        assert _contrast(colors[name], "#0e1014") >= 4.5, name


def test_pages_use_the_self_hosted_serif_font():
    """본문 글꼴은 같은 서버가 내려주는 Noto Serif KR이다. 외부 글꼴 서비스를 쓰지 않는다(AC-38)."""
    for path in ("/", "/reviews"):
        html = _get(path).text
        assert "/static/fonts.css" in html and "/static/fonts/NotoSerifKR-subset.woff2" in html, path
    face = _get("/static/fonts.css").text
    assert 'font-family: "Noto Serif KR"' in face and "font-display: swap" in face
    font = _get("/static/fonts/NotoSerifKR-subset.woff2")
    assert font.status_code == 200 and font.content[:4] == b"wOF2"
    assert len(font.content) < 1_500_000
    assert _get("/static/fonts/LICENSE.txt").status_code == 200
    root = _get("/static/login.css").text
    assert _declared(_rule(root, ":root {"), "font-family").startswith('"Noto Serif KR"')


def test_pages_use_self_hosted_sans_body_and_serif_titles():
    """정보 영역은 고딕(Pretendard 부분집합), 제목은 세리프(Playfair Display 부분집합) + Noto Serif KR을 같은 서버에서 내려준다(AC-39)."""
    for path in ("/", "/reviews"):
        html = _get(path).text
        body_tag = html[html.index("<body") : html.index(">", html.index("<body")) + 1]
        assert "glass-page" in body_tag and "ui-sans" in body_tag, path
        assert "/static/fonts/CreditPulseSans-subset.woff2" in html, path
    assert "/static/fonts/CreditPulseSerif-subset.woff2" in _get("/").text

    face = _get("/static/fonts.css").text
    assert 'font-family: "Credit Pulse Sans"' in face and 'font-family: "Credit Pulse Serif"' in face
    assert face.count("font-display: swap") >= 3
    assert "http" not in face
    for name in ("CreditPulseSans-subset.woff2", "CreditPulseSerif-subset.woff2"):
        font = _get(f"/static/fonts/{name}")
        assert font.status_code == 200 and font.content[:4] == b"wOF2", name
        assert len(font.content) < 1_500_000, name
    notice = _get("/static/fonts/LICENSE.txt").text
    for word in ("Pretendard", "Playfair Display", "Noto Serif KR", "Open Font License"):
        assert word in notice, word
    # 원본의 예약 이름 선언을 원본 그대로 적고(Playfair는 'Playfair'가 아니라 'Playfair Display'), OFL 전문을 담는다
    assert 'with Reserved Font Name "Playfair Display"' in notice
    assert "with Reserved Font Name 'Playfair'" not in notice
    for declared in ("'Pretendard'", "'Source'", "'Inter'", "'M PLUS 1'"):
        assert f"with Reserved Font Name {declared}" in notice, declared
    assert "PERMISSION & CONDITIONS" in notice and "Credit Pulse Serif" in notice and "Credit Pulse Sans" in notice

    theme = _get("/static/theme.css").text
    root = _declared(_rule(theme, ":root {"), "--font-sans")
    assert root.startswith('"Credit Pulse Sans"') and root.endswith("sans-serif")
    serif = _declared(_rule(theme, ":root {"), "--font-serif")
    assert serif.startswith('"Credit Pulse Serif", "Noto Serif KR"') and serif.endswith("serif")
    sans_rule = _rule(theme, ".ui-sans {")
    assert _declared(sans_rule, "font-family") == "var(--font-sans)"
    assert _declared(sans_rule, "word-break") == "keep-all"
    titles = _rule(theme, ".ui-sans .hero h1,")
    for selector in (".hero h1", ".section-title", ".slide-name", ".reviews-page h1", ".dialog-head h2"):
        assert f".ui-sans {selector}" in theme, selector
    assert _declared(titles, "font-family") == "var(--font-serif)"


def test_review_detail_page_uses_shared_sans_font():
    """상세 화면도 목록과 같은 본문 글꼴을 쓴다(시각 정렬 스펙)."""
    html = _get("/reviews/example").text
    body_tag = html[html.index("<body") : html.index(">", html.index("<body")) + 1]
    assert "ui-sans" in body_tag


def test_background_motion_script_respects_reduced_motion_and_touch():
    """모션 줄이기·터치 기기에서는 마우스 추적을 시작하지 않는다."""
    script = _get("/static/bg-motion.js").text
    assert "prefers-reduced-motion" in script
    assert "hover: hover" in script
    assert "innerHTML" not in script and "fetch(" not in script


def test_panels_are_frosted_glass():
    """정보 패널·로그인 폼·기능 칸은 반투명 + 블러의 유리 패널이다(AC-18)."""
    css = _get("/static/landing.css").text
    for selector in (".info-panel {", ".login-dialog {"):
        rule = _rule(css, selector)
        assert _declared(rule, "background").startswith("var(--glass-bg"), selector
        assert _declared(rule, "backdrop-filter") in ("var(--glass-blur)",) or "blur(" in _declared(rule, "backdrop-filter"), selector
    # 스크롤되는 기능 칸 6개에는 블러를 걸지 않고 진한 유리 배경만 쓴다(AC-40)
    tile = _rule(css, ".feature-tile:disabled {")
    assert _declared(tile, "background").startswith("var(--glass-bg")
    assert "backdrop-filter" not in tile


def test_typography_is_light_with_readable_labels():
    """기업명은 얇고 크게, 라벨·기업 정보는 본문 글꼴로 읽기 쉬운 크기로 쓴다(AC-19)."""
    css = _get("/static/landing.css").text
    assert _declared(_rule(css, ".slide-name {"), "font-weight") == "300"
    assert _rem(_rule(css, ".info-title {"), "font-size") >= 1
    assert _rem(_rule(css, ".slide-code,"), "font-size") >= 1.125
    for selector in (".info-title {", ".slide-position {", ".slide-code {", ".feature-note {"):
        assert "font-family" not in _rule(css, selector), selector
        assert "text-transform" not in _rule(css, selector), selector


def test_controls_have_touch_size_and_gentle_feedback():
    """입력칸·버튼은 44px 이상이고 버튼은 짧은 색 전환을 쓰되 모션 줄이기를 따른다(AC-21)."""
    import re

    landing = _get("/static/landing.css").text
    assert _rem(_rule(landing, ".pill {"), "min-height") >= 2.75
    assert _rem(_rule(landing, ".login-dialog button {"), "min-height") >= 2.75

    login = _get("/static/login.css").text
    button_rule = next(
        block for block in login.split("}") if block.strip().startswith("button {") and "cursor: pointer" in block
    )
    seconds = re.search(r"transition:[^;]*?([\d.]+)s", button_rule)
    assert seconds and float(seconds.group(1)) <= 0.3
    reduced = login[login.index("prefers-reduced-motion") :]
    assert "transition: none" in reduced


def test_site_bar_and_hero_are_built_around_the_logo():
    """카드 대신 로고가 든 상단 바와 히어로로 첫 화면을 시작한다(AC-24)."""
    html = _get("/").text

    assert 'class="credit-card"' not in html
    bar = html[html.index('class="site-bar"') : html.index("</header>")]
    logo = _tag_containing(bar, 'class="site-logo"')
    assert 'alt="CREDIT PULSE"' in logo
    assert "/static/credit-pulse-logo-white.png" in logo
    assert 'width="1200"' in logo and 'height="213"' in logo
    assert 'aria-label="주요 메뉴"' in _tag_containing(html, 'class="site-nav"')
    for target in ("companies", "features"):
        assert f'href="#{target}"' in bar, target
        assert f'id="{target}"' in html, target
    opener = _tag_containing(html, 'id="open-login"')
    assert 'type="button"' in opener
    assert 'aria-haspopup="dialog"' in opener
    assert 'aria-controls="login-dialog"' in opener
    assert html.count("<h1") == 1
    assert html.index('class="site-bar"') < html.index("<h1")
    hero = html[html.index('class="hero"') : html.index('id="companies"')]
    assert "<h1" in hero and 'id="hero-login"' in hero and 'href="#companies"' in hero


def test_login_form_lives_in_a_modal_dialog():
    """로그인은 상단 버튼으로 여는 팝업 안에 있고 처음에는 닫혀 있다(AC-25)."""
    html = _get("/").text

    tag = _tag_containing(html, 'id="login-dialog"')
    assert 'id="login-dialog"' in tag
    assert 'closedby="none"' in tag
    assert 'aria-labelledby="login-title"' in tag
    assert " open" not in tag
    start = html.index(tag)
    dialog = html[start : html.index("</dialog>", start)]
    for element_id in ("login-title", "login-error", "login-status", "login-form", "login-close"):
        assert f'id="{element_id}"' in dialog, element_id
    close = _tag_containing(dialog, 'id="login-close"')
    assert 'type="button"' in close and 'aria-label="닫기"' in close
    assert " hidden" in _tag_containing(dialog, 'id="login-form"')


def test_page_two_is_a_tabbed_stage_with_three_panels():
    """2페이지는 번호 탭 3개와 패널 3개로 이루어지고 옛 구조는 없다(AC-56)."""
    import re

    html = _get("/").text
    body_tag = html[html.index("<body") : html.index(">", html.index("<body")) + 1]
    assert "landing" in body_tag.split('"')[1].split()
    head = html[: html.index("</head>")]
    assert 'document.documentElement.classList.add("js")' in head  # JS가 켜진 브라우저는 첫 페인트부터 탭 레이아웃

    assert html.index('class="hero"') < html.index('id="companies"')
    section = html[html.index('id="companies"') : html.index("</main>")]
    assert "visually-hidden" in _tag_containing(html, 'id="companies-title"') and "보유 기업</h2>" in html
    assert 'aria-labelledby="companies-title"' in _tag_containing(html, 'id="companies"')
    order = [section.index(marker) for marker in ('class="stage-wrap"', 'class="stage-nav"', 'role="tablist"', 'id="stage-toggle"', 'class="stage-body"')]
    assert order == sorted(order)

    tablist = _tag_containing(section, 'role="tablist"')
    assert 'aria-orientation="vertical"' in tablist and 'aria-label="보유 기업 화면 선택"' in tablist
    tabs = re.findall(r'<button id="stage-tab-(\d)" class="stage-tab"([^>]*)>(.*?)</button>', section, re.S)
    assert [tab[0] for tab in tabs] == ["0", "1", "2"]
    controls = ["stage-panel-0", "stage-panel-1", "features"]
    for (index, attrs, inner), control, number, title in zip(tabs, controls, ("01", "02", "03"), ("보유 기업", "기업 검색", "기능")):
        assert 'role="tab"' in attrs and f'aria-controls="{control}"' in attrs, index
        assert ('aria-selected="true"' in attrs) == (index == "0"), index
        assert f'class="stage-num">{number}<' in inner and title in inner and 'class="stage-sub"' in inner, index
    toggle = _tag_containing(section, 'id="stage-toggle"')
    assert " hidden" in toggle and 'type="button"' in toggle
    assert 'role="switch"' in toggle and 'aria-checked="true"' in toggle
    toggle_markup = section[section.index(toggle):section.index("</button>", section.index(toggle))]
    assert "자동 전환" in toggle_markup
    assert 'class="stage-toggle-track" aria-hidden="true"' in toggle_markup
    assert ">ON</span>" in toggle_markup and ">OFF</span>" in toggle_markup

    panels = re.findall(r'<div id="([^"]+)" class="stage-panel" role="tabpanel" aria-labelledby="(stage-tab-\d)"', section)
    assert panels == list(zip(controls, ["stage-tab-0", "stage-tab-1", "stage-tab-2"]))
    body = section[section.index('class="stage-body"') :]
    panel0 = body[body.index('id="stage-panel-0"') : body.index('id="stage-panel-1"')]
    assert 'id="info-panel"' in panel0 and 'id="slide-viewport"' in panel0 and "data-active" in panel0
    panel1 = body[body.index('id="stage-panel-1"') : body.index('id="features"')]
    assert 'class="stage-search company-search"' in panel1 and "<h3" in panel1 and 'id="company-search"' in panel1  # landing-company-search: 설명 문구 대신 결과 목록
    assert "LG화학, 00356361" in panel1 and "data-active" not in panel1  # 예시는 입력칸 안내 문구로 옮김
    panel2 = body[body.index('id="features"') :]
    # landing-feature-pages: 5칸 모두 이름에 맞는 전용 페이지 링크
    assert '<div id="feature-strips" class="strips" data-rise>' in panel2
    assert re.findall(r'<div id="strip-(\w+)" class="strip" data-open="(\w+)">', panel2) == [
        ("search", "false"), ("reviews", "true"), ("reports", "false"), ("approvals", "false"), ("rules", "false"),
    ]  # 처음에는 리스크 점검만 펼쳐진다
    names = re.findall(r'<span class="strip-name">([^<]+)</span>', panel2)
    assert names == ["공시 검색", "리스크 점검", "보고서", "승인 현황", "내 판정 규칙"]
    assert "준비 중" not in panel2
    for key, note in (
        ("search", "회사명·corp_code로 보유 기업 찾기"),
        ("reviews", "심사 건 만들기·질의"),
        ("reports", "승인·저장된 심사 메모 모아 보기"),
        ("approvals", "승인 대기 건 모아 보기"),
        ("rules", "판정 규칙 보기·삭제"),
    ):
        assert f'<a class="strip-open" href="/?open={key}" data-feature="{key}">' in panel2, key
        assert f'<p class="strip-note">{note}</p>' in panel2, note
    assert '<p id="login-required-hint" class="stage-hint" data-rise>로그인 후 사용 가능합니다.</p>' in panel2
    assert "아직 준비 중인 기능입니다." not in html

    for gone in ("stage-core", "feature-row", "feature-track", "feature-list", "feature-grid", "section-grid", "section-intro"):
        assert gone not in html, gone
    assert html.count('id="features"') == 1 and 'id="features" class="section"' not in html
    assert 'href="#features"' in html[html.index('class="site-nav"') : html.index("</header>")]
    assert "<section" not in section  # 별도 섹션이 더 이어지지 않는다
    # 예시 안내의 기업은 보유 기업 데이터에 실제로 있는 값이다
    assert 'name: "LG화학", corp_code: "00356361"' in _get("/static/landing-data.js").text


def test_each_panel_marks_the_pieces_that_rise_one_by_one():
    """전환 때 하나씩 올라올 요소가 패널마다 data-rise로 표시되어 있다(AC-57)."""
    import re

    html = _get("/").text
    body = html[html.index('class="stage-body"') : html.index("</main>")]
    panel0 = body[body.index('id="stage-panel-0"') : body.index('id="stage-panel-1"')]
    panel1 = body[body.index('id="stage-panel-1"') : body.index('id="features"')]
    panel2 = body[body.index('id="features"') :]
    assert len(re.findall(r"data-rise", panel0)) == 3  # 머리줄, 슬라이더, 진행 막대
    assert len(re.findall(r"data-rise", panel1)) == 3  # 제목, 검색 폼, 결과 영역(landing-company-search)
    assert len(re.findall(r"data-rise", panel2)) == 2  # 띠 묶음과 안내
    assert 'class="info-head" data-rise' in panel0 and 'class="slider" data-rise' in panel0 and 'id="slide-progress" class="slide-progress"' in panel0


def test_hero_styles_are_calm_and_responsive():
    """굵은 헤드라인, 알약 버튼, 좁은 화면 1열을 갖춘다(AC-27)."""
    css = _get("/static/landing.css").text

    assert int(_declared(_rule(css, ".hero h1 {"), "font-weight")) >= 600
    pill = _rule(css, ".pill {")
    assert _declared(pill, "border-radius") == "999px"
    assert _rem(pill, "min-height") >= 2.75
    assert _declared(_rule(css, ".pill-light {"), "background") == "var(--on-dark)"
    assert ".section-grid" not in css  # 2열 격자는 없앴다(AC-52)
    assert _declared(_rule(css, "#companies {"), "grid-template-columns") == "minmax(0, 1fr)"
    assert _rem(_rule(css, ".slide {"), "min-height") >= 11


def test_hero_copy_is_plain_without_decorative_labels():
    """히어로는 제목·설명·두 버튼만 둔다. 윗줄 라벨, 기울임 강조, 칩, 화살표 장식은 없다(AC-36)."""
    html = _get("/").text

    hero = html[html.index('class="hero"') : html.index('id="companies"')]
    for decoration in ("hero-visual", "hero-chips", 'class="eyebrow"', "<em>", "pill-arrow"):
        assert decoration not in hero, decoration
    assert hero.count("<h1") == 1 and 'class="hero-lead"' in hero


def test_login_dialog_styles():
    """팝업은 작고 반투명하며 뒷배경을 어둡게 한다(AC-29)."""
    css = _get("/static/landing.css").text

    dialog = _rule(css, ".login-dialog {")
    assert _rem(dialog, "max-width") <= 24
    assert "::backdrop" in css and _rule(css, ".login-dialog::backdrop {")


def test_slider_has_a_pause_toggle_and_panel_hook_for_autoplay():
    """자동 넘김은 멈출 수 있어야 한다. JS가 없으면 쓸모없으므로 버튼은 처음에 숨긴다(AC-31)."""
    html = _get("/").text

    panel = _tag_containing(html, 'id="info-panel"')
    assert 'class="info-panel"' in panel
    toggle = _tag_containing(html, 'id="slide-toggle"')
    assert 'type="button"' in toggle
    assert 'aria-controls="slide-viewport"' in toggle
    assert " hidden" in toggle
    assert html.index('id="info-panel"') < html.index('id="slide-toggle"') < html.index('id="slide-viewport"')
    script = _get("/static/landing.js").text
    assert "setInterval" in script and "prefers-reduced-motion" in script


def _declared(rule: str, prop: str) -> str:
    """같은 규칙 안에서는 나중 선언이 이기므로 마지막 값을 돌려준다."""
    import re

    values = re.findall(rf"(?<![\w-]){prop}:\s*([^;}}]+)", rule)
    assert values, f"{prop}가 없습니다: {rule}"
    return values[-1].strip()


def _rem(rule: str, prop: str) -> float:
    value = _declared(rule, prop)
    assert value.endswith("rem"), f"{prop}={value}"
    return float(value[:-3])


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_text_color_pairs_meet_wcag_aa():
    """글자와 배경 짝의 대비가 4.5:1보다 낮으면 읽기 어렵다(AC-07)."""
    import re

    css = _get("/static/login.css").text
    colors = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", css))
    pairs = [
        ("ink", "surface"),
        ("ink", "paper"),
        ("muted", "surface"),
        ("muted", "paper"),
        ("surface", "navy"),
        ("navy", "surface"),
        ("navy", "navy-50"),
        ("danger", "surface"),
        ("danger", "paper"),
        ("ink", "mist"),
        ("muted", "mist"),
        ("navy", "mist"),
        ("accent", "mist"),
        ("accent", "surface"),
    ]
    for foreground, background in pairs:
        ratio = _contrast(colors[foreground], colors[background])
        assert ratio >= 4.5, f"{foreground}/{background} 대비 {ratio:.2f}"


def _hex_rgb(value: str) -> tuple[float, float, float]:
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


def _over(top: tuple, alpha: float, bottom: tuple) -> tuple:
    return tuple(top[i] * alpha + bottom[i] * (1 - alpha) for i in range(3))


def _rgb_luminance(rgb: tuple) -> float:
    linear = [c / 255 / 12.92 if c / 255 <= 0.03928 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _rgb_contrast(a: tuple, b: tuple) -> float:
    high, low = sorted((_rgb_luminance(a), _rgb_luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_surfaces_are_dense_and_use_tinted_shadows():
    """유리 배경은 알파 0.82/0.92이고 카드 그림자는 순수 검정이 아닌 색조 변수를 쓴다(AC-40)."""
    import re

    theme = _get("/static/theme.css").text
    alphas = {
        name: float(value)
        for name, value in re.findall(r"--(glass-bg(?:-strong)?):\s*rgba\(14, 16, 20, ([0-9.]+)\)", theme)
    }
    assert alphas == {"glass-bg": 0.82, "glass-bg-strong": 0.92}
    assert "--shadow-tint:" in theme and "var(--shadow-tint)" in _declared(_rule(theme, ":root {"), "--glass-shadow")
    for path in ("/static/landing.css", "/static/reviews.css"):
        assert "rgba(0, 0, 0" not in _get(path).text, path


def test_card_text_keeps_aa_contrast_on_the_brightest_photo_area():
    """사진의 가장 밝은 곳(#f7cca4) 위 카드에서 호버 상태까지 글자 대비가 4.5:1 이상이다(AC-41)."""
    import re

    theme = _get("/static/theme.css").text
    colors = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", theme))
    alpha = float(re.search(r"--glass-bg:\s*rgba\(14, 16, 20, ([0-9.]+)\)", theme).group(1))
    card = _over((14, 16, 20), alpha, _hex_rgb("#f7cca4"))
    hovered = _over((255, 255, 255), 0.12, card)  # .review-item:hover의 겹침
    for name in ("on-dark", "on-dark-muted", "gold", "error-on-dark", "gold-soft"):
        assert _rgb_contrast(_hex_rgb(colors[name]), hovered) >= 4.5, name
    badge = _over((227, 184, 102), 0.16, hovered)
    assert _rgb_contrast(_hex_rgb(colors["gold-soft"]), badge) >= 4.5


def test_card_text_sizes_use_shared_variables():
    """카드 안 글자 크기·줄간격·여백이 :root 변수를 쓰고 보조 글자는 0.9375rem 이상이다(AC-42)."""
    theme = _get("/static/theme.css").text
    root = _rule(theme, ":root {")
    assert _rem(root, "--text-sm") >= 0.9375
    for name in ("--text-base", "--text-lg", "--leading-body", "--leading-tight", "--space-card-y", "--space-card-x"):
        assert f"{name}:" in root, name
    landing = _get("/static/landing.css").text
    reviews = _get("/static/reviews.css").text
    assert _declared(_rule(reviews, ".review-meta {"), "font-size") == "var(--text-sm)"
    assert _declared(_rule(reviews, ".review-code,"), "font-size") == "var(--text-sm)"
    assert _declared(_rule(reviews, ".review-item {"), "padding") == "var(--space-card-y) var(--space-card-x)"
    assert _declared(_rule(landing, ".section-lead {"), "line-height") == "var(--leading-body)"
    assert _declared(_rule(landing, ".feature-note {"), "font-size") == "var(--text-sm)"
    assert _declared(_rule(landing, ".slide-toggle {"), "font-size") == "var(--text-sm)"
    balance = _rule(theme, ".ui-sans h1,")
    assert _declared(balance, "text-wrap") == "balance"
    assert ".ui-sans .review-title" in theme


def test_stage_css_gates_the_tab_layout_on_js_and_overlaps_panels_in_one_cell():
    """JS가 켜진 때만 탭 레이아웃이고 패널은 한 칸에 겹치며 숨은 패널은 읽히지 않는다. 크기는 AC-52를 유지한다(AC-52, AC-56)."""
    css = _get("/static/landing.css").text
    page = _rule(css, "#companies {")
    assert _declared(page, "display") == "grid" and _declared(page, "align-content") == "center"
    assert page.index("min-height: 100vh") < page.index("min-height: 100dvh")

    assert _declared(_rule(css, ".stage-wrap {"), "grid-template-columns") == "minmax(0, 1fr)"
    assert _declared(_rule(css, "html.js .stage-wrap {"), "grid-template-columns") == "minmax(0, 25rem) minmax(0, 1fr)"
    assert _declared(_rule(css, ".stage-nav {"), "display") == "none"
    assert _declared(_rule(css, "html.js .stage-nav {"), "display") == "grid"
    panel = _rule(css, "html.js .stage-panel {")
    assert _declared(panel, "grid-area") == "1 / 1" and _declared(panel, "opacity") == "0" and _declared(panel, "visibility") == "hidden"
    active = _rule(css, "html.js .stage-panel[data-active] {")
    assert _declared(active, "opacity") == "1" and _declared(active, "visibility") == "visible"
    leaving = _rule(css, "html.js .stage-panel[data-leaving] {")
    assert _declared(leaving, "visibility") == "visible" and _declared(leaving, "pointer-events") == "none"

    assert _declared(_rule(css, ".stage-title {"), "font-family") == "var(--font-serif)"
    assert _declared(_rule(css, ".stage-tab {"), "opacity") == "0.4"
    assert _declared(_rule(css, '.stage-tab[aria-selected="true"] {'), "opacity") == "1"
    assert _declared(_rule(css, '.stage-tab[aria-selected="true"] .stage-num {'), "color") == "var(--gold)"
    assert _declared(_rule(css, ".stage-features {"), "grid-template-columns") == "repeat(3, minmax(0, 1fr))"
    assert _declared(_rule(css, ".stage-toggle {"), "min-height") == "2.75rem"
    track = _rule(css, ".stage-toggle-track {")
    assert _declared(track, "border-radius") == "999px"
    assert _declared(track, "background") == "#101113"
    assert _declared(track, "color") == "#b3b0ab"
    assert _declared(_rule(css, '.stage-toggle[aria-checked="true"] .stage-toggle-track {'), "color") == "var(--gold)"
    assert _declared(_rule(css, ".stage-toggle[hidden] {"), "display") == "none"

    # 기업 정보 크기는 그대로다(AC-52)
    assert _declared(_rule(css, ".slide-name {"), "font-size") == "clamp(2.75rem, 6.5vw, 5.25rem)"
    assert _declared(_rule(css, ".slide-name {"), "font-weight") == "300"
    assert _rem(_rule(css, ".slide-viewport {"), "min-height") == 13 and _rem(_rule(css, ".slide {"), "min-height") == 13
    assert _declared(_rule(css, ".slide-code,"), "font-size") == "1.625rem"
    assert _declared(_rule(css, ".section-lead {"), "font-size") == "var(--text-sm)"

    narrow = css[css.index("@media (max-width: 52rem)") :]
    assert _declared(_rule(narrow, "html.js .stage-wrap {"), "grid-template-columns") == "minmax(0, 1fr)"
    assert _declared(_rule(narrow, ".stage-sub {"), "display") == "none"
    assert _declared(_rule(narrow, ".stage-features {"), "grid-template-columns") == "repeat(2, minmax(0, 1fr))"
    for gone in ("feature-flow", "feature-row", "feature-track", "stage-core"):
        assert gone not in css, gone


def test_transitions_share_one_easing_and_buttons_press_in():
    """전환 곡선은 --ease-spring 하나로 통일하고 버튼은 눌리면 살짝 줄며, 모션 줄이기에서는 꺼진다(AC-44)."""
    import re

    theme = _get("/static/theme.css").text
    assert _declared(_rule(theme, ":root {"), "--ease-spring") == "cubic-bezier(0.16, 1, 0.3, 1)"
    landing = _get("/static/landing.css").text
    reviews = _get("/static/reviews.css").text
    for css in (landing, reviews):
        normal = css[: css.index("@media (prefers-reduced-motion: reduce)")]
        transitions = re.findall(r"(?<![\w-])transition:\s*([^;]+);", normal)
        assert transitions
        for value in transitions:
            assert "var(--ease-spring)" in value, value
            assert not re.search(r"ease(-in|-out|-in-out)?", value), value
    for css, selector in ((landing, ".pill:active"), (landing, ".slide-nav:active"), (reviews, ".reviews-page button:active")):
        assert _declared(_rule(css, selector), "transform") == "scale(0.98)", selector
    for css in (landing, reviews):
        reduced = css[css.index("@media (prefers-reduced-motion: reduce)") :]
        assert "transition: none" in reduced and "transform: none" in reduced


def test_narrow_screens_keep_names_dates_and_hero_text_readable():
    """375/768px에서 이름·날짜가 끊기지 않고 밝은 사진 위 글자가 읽히며 점은 24px 이상으로 눌린다(AC-45)."""
    import re

    theme = _get("/static/theme.css").text
    page = _rule(theme, ".glass-page {")
    assert page.index("min-height: 100vh") < page.index("min-height: 100dvh")

    landing = _get("/static/landing.css").text
    reviews = _get("/static/reviews.css").text
    user = _rule(reviews, "#current-user {")
    assert _declared(user, "white-space") == "nowrap" and _declared(user, "text-overflow") == "ellipsis"
    assert "overflow-wrap" not in user
    assert _declared(_rule(reviews, ".review-meta > span {"), "white-space") == "nowrap"
    assert _declared(_rule(reviews, ".review-code {"), "white-space") == "nowrap"
    compact = reviews[reviews.index("@media (max-width: 40rem)") :]
    assert _declared(_rule(compact, ".app-logo {"), "height") == "1.5rem"

    assert _declared(_rule(landing, ".section {"), "padding").startswith("clamp(2rem, 7vh, 4.5rem)")
    assert _declared(_rule(landing, ".slide-progress {"), "width") == "100%"
    assert _declared(_rule(landing, ".slide-progress-fill {"), "background") == "var(--gold)"
    assert _declared(_rule(landing, ".slide-progress-fill {"), "transition").startswith("width")
    narrow = landing[landing.index("@media (max-width: 52rem)") :]
    shade = _rule(narrow, ".bg-shade {")
    alphas = [float(a) for a in re.findall(r"rgba\(8, 6, 5, ([0-9.]+)\)", shade)]
    assert len(alphas) == 2 and min(alphas) >= 0.75


def test_hero_fills_the_first_screen():
    """히어로가 첫 화면을 채워 보유 기업 패널이 첫 화면에 보이지 않는다(AC-46)."""
    css = _get("/static/landing.css").text
    assert _declared(_rule(css, ".glass-page {"), "--site-bar-h") == "5.25rem"
    hero = _rule(css, "/* 히어로가 첫 화면을 채워")
    hero = hero[hero.index(".hero {") :]
    assert _declared(hero, "display") == "flex" and _declared(hero, "align-items") == "center"
    assert hero.index("100vh") < hero.index("100dvh")
    assert _declared(hero, "min-height") == "max(30rem, calc(100dvh - var(--site-bar-h)))"
    narrow = css[css.index("@media (max-width: 52rem)") :]
    assert "min-height" not in _rule(narrow, ".hero {")  # 좁은 화면도 히어로 높이를 줄이지 않는다


def test_hero_copy_sits_at_the_book_height_not_the_bottom():
    """문구를 --hero-lift만큼 위로 올려 로고와 가깝게 둔다(AC-47)."""
    css = _get("/static/landing.css").text
    hero = _rule(css, "/* 문구를 배경 책의 가운데 높이로 올린다")
    hero = hero[hero.index(".hero {") :] if ".hero {" in hero else hero
    assert _declared(hero, "--hero-lift") == "28vh"
    assert "var(--hero-lift)" in _declared(hero, "padding")
    narrow = css[css.index("@media (max-width: 52rem)") :]
    assert _declared(_rule(narrow, ".hero {"), "--hero-lift") == "12vh"


def test_hero_has_a_decorative_scroll_cue():
    """첫 화면 맨 아래에 누를 수 없는 스크롤 안내가 있고 모션 줄이기에서는 멈춘다(AC-48)."""
    html = _get("/").text
    hero = html[html.index('class="hero"') : html.index('id="companies"')]
    cue = _tag_containing(hero, 'class="scroll-cue"')
    assert 'aria-hidden="true"' in cue
    cue_block = hero[hero.index('class="scroll-cue"') :]
    assert "<a " not in cue_block and "<button" not in cue_block
    assert hero.count('href="#companies"') == 1

    css = _get("/static/landing.css").text
    cue_rule = _rule(css, ".scroll-cue {")
    assert _declared(cue_rule, "pointer-events") == "none" and _declared(cue_rule, "position") == "absolute"
    line = _rule(css, ".scroll-cue-line {")
    assert "var(--ease-spring)" in _declared(line, "animation")
    frames = css[css.index("@keyframes scroll-cue") : css.index(".hero-actions {")]
    assert "transform" in frames and "opacity" in frames and "top:" not in frames and "height:" not in frames
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)") :]
    assert ".scroll-cue-line" in reduced and "animation: none" in reduced


def test_hero_stats_strip_shows_verifiable_numbers_and_stays_in_sync():
    """핵심 수치 띠의 숫자는 데이터와 SPEC에서 확인되는 값이고 HTML 값과 스크립트가 같은 값을 쓴다(AC-49)."""
    import re

    html = _get("/").text
    hero = html[html.index('class="hero"') : html.index('id="companies"')]
    strip = hero[hero.index('class="hero-stats"') : hero.index('class="scroll-cue"')]
    assert 'aria-label="핵심 수치"' in hero and strip.count("<li>") == 4
    for expected in ("보유 기업", "체크리스트 항목", "사업보고서 기준", "승인 후"):
        assert expected in strip, expected
    assert "hero-stats.js" in html
    assert html.index("landing.js") < html.index("hero-stats.js")

    data = _get("/static/landing-data.js").text
    years = set(re.findall(r"fiscal_year: (\d+)", data))
    count = len(re.findall(r"corp_code: ", data))
    assert re.search(r'id="stat-companies">(\d+)<', strip).group(1) == str(count)
    assert years == {re.search(r'id="stat-year"[^>]*>(\d+)<', strip).group(1)}
    shared = _get("/static/review-shared.js").text
    items = re.findall(r"^\s+(litigation|guarantee_collateral|customer_concentration|fx_raw_material|borrowing_structure|contingent_liability):", shared, re.M)
    assert len(items) == 6 and re.search(r"<strong class=\"stat-value\">6<small>항목", strip)

    script = _get("/static/hero-stats.js").text
    assert 'from "./landing-data.js"' in script and "innerHTML" not in script and "fetch(" not in script


def test_hero_stats_strip_styles_are_hairline_and_do_not_overlap_copy():
    """띠는 유리 바탕 + 금색 얇은 선이고 좁거나 낮은 화면에서는 문구 아래에 쌓인다(AC-49)."""
    css = _get("/static/landing.css").text
    strip = _rule(css, ".hero-stats {")
    assert _declared(strip, "background").startswith("var(--glass-bg")
    assert _declared(strip, "position") == "absolute"
    assert "rgba(227, 184, 102" in _declared(strip, "border-top") and "rgba(227, 184, 102" in _declared(strip, "border-bottom")
    assert _declared(_rule(css, ".hero-stats li + li {"), "border-left") == "1px solid var(--glass-line)"
    assert _declared(_rule(css, ".stat-value {"), "font-family") == "var(--font-serif)"
    assert _declared(_rule(css, ".stat-label {"), "font-size") == "var(--text-sm)"
    flow = css[css.index("@media (max-height: 42rem), (max-width: 52rem)") :]
    assert _declared(_rule(flow, ".hero-stats {"), "position") == "static"
    assert _declared(_rule(flow, ".hero {"), "flex-direction") == "column"
    narrow = css[css.index("@media (max-width: 40rem)", css.index(".hero-stats li {")) :]
    assert "repeat(2, minmax(0, 1fr))" in _rule(narrow, ".hero-stats {")


def test_intro_overlay_is_first_in_body_and_gated_by_an_inline_script():
    """인트로 오버레이는 body 맨 앞의 장식이고, head의 인라인 스크립트가 노출 조건을 정한다(AC-50, AC-51)."""
    import re

    html = _get("/").text
    body_at = html.index("<body")
    intro_tag = _tag_containing(html, 'id="intro"')
    assert body_at < html.index('id="intro"') < html.index('class="page-bg"')
    assert 'class="intro"' in intro_tag and 'aria-hidden="true"' in intro_tag
    assert 'data-logo="/static/credit-pulse-logo-white.png"' in intro_tag
    assert 'class="intro-stage"' in html[html.index('id="intro"') : html.index('class="page-bg"')]

    head = html[: html.index("</head>")]
    assert head.index("landing.css") < head.index("intro.css")
    inline = next(code for code in re.findall(r"<script>(.*?)</script>", head, re.S) if "credit-pulse-intro-seen" in code)
    for needle in ("sessionStorage", "prefers-reduced-motion", "intro-pending", "6000", "catch"):
        assert needle in inline, needle
    key = re.search(r'getItem\("([^"]+)"\)', inline).group(1)
    assert key == "credit-pulse-intro-seen"
    assert f'SEEN_KEY = "{key}"' in _get("/static/intro.js").text  # 두 곳의 키가 같다
    assert re.search(r'<script type="module" src="[^"]*/static/intro\.js"></script>', html)

    for path in ("/reviews", "/reviews/example"):
        other = _get(path).text
        assert 'id="intro"' not in other and "intro-pending" not in other and "intro.css" not in other, path


def test_intro_styles_cover_the_page_only_while_pending():
    """오버레이는 평소 숨고 intro-pending일 때만 화면을 덮으며, 로고는 변수로 받고 금색은 토큰을 쓴다(AC-50)."""
    css = _get("/static/intro.css").text
    assert "url(" not in css and "http" not in css
    overlay = _rule(css, ".intro {")
    assert _declared(overlay, "display") == "none"
    assert _declared(overlay, "position") == "fixed" and _declared(overlay, "inset") == "0"
    assert _declared(_rule(css, "html.intro-pending .intro {"), "display") == "grid"
    assert _declared(_rule(css, "html.intro-pending {"), "overflow") == "hidden"
    stage = _rule(css, ".intro-stage {")
    assert _declared(stage, "aspect-ratio") == "1200 / 213"
    assert _declared(stage, "grid-template-columns") == "repeat(16, 1fr)"
    assert _declared(stage, "grid-template-rows") == "repeat(3, 1fr)"
    assert _declared(_rule(css, ".intro-tile {"), "background-image") == "var(--intro-logo)"
    gold = _rule(css, ".intro-gold {")
    assert _declared(gold, "background") == "var(--gold)"
    assert "var(--intro-logo)" in _declared(gold, "mask") and _declared(gold, "opacity") == "0"
    assert _declared(gold, "pointer-events") == "none"


def test_intro_script_follows_the_spec_and_moves_only_transform_and_opacity():
    """16×3 조각, 2.8초, 금색 0.6, 하나의 곡선이고 안전하지 않은 API나 비용 큰 속성을 쓰지 않는다(AC-50, AC-51)."""
    import re

    js = _get("/static/intro.js").text
    theme = _get("/static/theme.css").text
    expected = {"COLS": "16", "ROWS": "3", "TOTAL_MS": "2800", "FADE_MS": "500", "QUICK_FADE_MS": "150", "GOLD_PEAK": "0.6", "SCATTER_SPAN_MS": "450"}
    for name, value in expected.items():
        assert re.search(rf"const {name} = {re.escape(value)};", js), name
    spring = re.search(r'const SPRING = "([^"]+)"', js).group(1)
    assert spring == _declared(_rule(theme, ":root {"), "--ease-spring")
    for forbidden in ("innerHTML", "fetch(", "http", "filter", "XMLHttpRequest"):
        assert forbidden not in js, forbidden
    assert not re.search(r"(top|left|width|height|margin)\s*:", js)
    for needle in ("sessionStorage", "prefers-reduced-motion", "pointerdown", "keydown", "isSafeLogoPath"):
        assert needle in js, needle


def test_page_glide_script_is_wired_only_on_the_landing_page_and_follows_the_spec():
    """휠 한 번으로 1↔2페이지를 부드럽게 오가는 스크립트는 메인에만 있고 스펙의 조건을 지킨다(AC-54)."""
    import re

    html = _get("/").text
    assert re.search(r'<script type="module" src="[^"]*/static/page-glide\.js"></script>', html)
    for path in ("/reviews", "/reviews/example"):
        assert "page-glide.js" not in _get(path).text, path

    js = _get("/static/page-glide.js").text
    for name, value in {"GLIDE_MS": "1100", "MIN_WHEEL_PX": "4"}.items():
        assert re.search(rf"const {name} = {value};", js), name
    for needle in ("easeInOutCubic", 'behavior: "instant"', '{ passive: false }', "dialog[open]", "event.ctrlKey", "prefers-reduced-motion", "pointerdown", "keydown", "touchstart", "preventDefault"):
        assert needle in js, needle
    for forbidden in ("innerHTML", "fetch(", "http", "XMLHttpRequest", "scrollIntoView", "behavior: \"smooth\""):
        assert forbidden not in js, forbidden


def test_scroll_linked_effects_are_progressive_and_move_only_translate_and_opacity():
    """스크롤 연동 효과는 지원 확인(@supports)과 모션 줄이기 확인 안에만 있고 translate·opacity만 움직인다(AC-55)."""
    css = _get("/static/landing.css").text
    start = css.index("@supports (animation-timeline: view())")
    block = css[start : css.index("@media (prefers-reduced-motion: reduce)", start)]
    assert "@media (prefers-reduced-motion: no-preference)" in block
    assert _declared(_rule(block, ".hero {"), "view-timeline") == "--hero block"
    assert _declared(_rule(block, "#companies {"), "view-timeline") == "--page-two block"

    leaving = _rule(block, ".hero-copy,")
    assert all(name in block[: block.index("{", block.index(".hero-copy,"))] for name in (".hero-stats", ".scroll-cue"))
    assert _declared(leaving, "animation-range") == "exit 10% exit 70%" and _declared(leaving, "animation-timeline") == "--hero"
    entering = _rule(block, ".stage-nav,")
    assert ".stage-body" in block[: block.index("{", block.index(".stage-nav,"))]
    assert _declared(entering, "animation-timeline") == "--page-two"
    assert _declared(_rule(block, ".stage-nav {"), "animation-range") == "entry 0% entry 45%"
    assert _declared(_rule(block[block.rindex(".stage-body {") :], ".stage-body {"), "animation-range") == "entry 10% entry 55%"  # 묶음 규칙이 아닌 개별 규칙

    dim = _rule(block, ".landing .page-bg::after {")
    assert _declared(dim, "background") == "#080605" and _declared(dim, "opacity") == "0"
    assert _declared(dim, "animation-timeline") == "scroll(root)" and _declared(dim, "animation-range") == "0 90vh"

    frames = css[css.index("@keyframes page-out") : start]
    assert "opacity: 0.7" in frames and "translate: 0 -3.5rem" in frames and "translate: 0 4rem" in frames
    for forbidden in ("transform", "filter", "top:", "left:", "width:", "height:"):
        assert forbidden not in frames, forbidden
    assert "translateX(-50%)" in _rule(css, ".scroll-cue {")  # 위치를 잡는 transform은 그대로 두고 translate로만 움직인다


def test_stage_tabs_script_is_wired_only_on_the_landing_page_and_follows_the_spec():
    """탭 전환(B안)과 자동 전환의 수치·조건·금지 사항이 스펙과 같다(AC-57, AC-58)."""
    import re

    html = _get("/").text
    assert re.search(r'<script type="module" src="[^"]*/static/stage-tabs\.js"></script>', html)
    assert html.index("landing.js") < html.index("stage-tabs.js")  # 슬라이드·검색 연결이 먼저 준비된다
    for path in ("/reviews", "/reviews/example"):
        assert "stage-tabs.js" not in _get(path).text, path

    js = _get("/static/stage-tabs.js").text
    theme = _get("/static/theme.css").text
    constants = {
        "AUTO_MS": "6000",
        "HOVER_INTENT_MS": "110",
        "LEAVE_MS": "300",
        "LEAVE_RISE_PX": "-20",
        "FADE_MS": "280",
        "FADE_DELAY_MS": "100",
        "RISE_MS": "700",
        "RISE_PX": "44",
        "RISE_BASE_DELAY_MS": "120",
        "RISE_STEP_MS": "60",
        "VISIBLE_RATIO": "0.35",
    }
    for name, value in constants.items():
        assert re.search(rf"const {name} = {re.escape(value)};", js), name
    spring = re.search(r'const SPRING = "([^"]+)"', js).group(1)
    assert spring == _declared(_rule(theme, ":root {"), "--ease-spring")
    assert 'toggle.setAttribute("aria-checked", String(!userPaused))' in js
    for needle in ("prefers-reduced-motion", "visibilitychange", "focusin", "focusout", "pointerenter", "keydown", "IntersectionObserver", "aria-live", "data-leaving", "[data-rise]", "#features"):
        assert needle in js, needle
    for forbidden in ("innerHTML", "fetch(", "http", "XMLHttpRequest", "filter", "eval("):
        assert forbidden not in js, forbidden
    # 움직이는 속성은 transform·opacity뿐이다(top/left/width/height 애니메이션 없음)
    assert not re.search(r"\b(top|left|width|height|margin)\s*:", js)
    # data-rise로 표시된 요소는 패널마다 3·4·7개이고, 자동 전환 UI(#stage-toggle)가 마크업에 있다(AC-56)
    assert 'id="stage-toggle"' in html


def test_long_company_names_stay_on_one_line_via_slide_fit():
    """긴 기업명은 한 줄에 놓이고 글자 크기는 넘칠 때만 줄어든다(AC-59)."""
    import re

    html = _get("/").text
    assert re.search(r'<script type="module" src="[^"]*/static/slide-fit\.js"></script>', html)
    assert html.index("landing.js") < html.index("slide-fit.js")  # 슬라이드가 먼저 그려지고 그 뒤에 맞춘다
    for path in ("/reviews", "/reviews/example"):
        assert "slide-fit.js" not in _get(path).text, path

    css = _get("/static/landing.css").text
    rule = _rule(css, "html.js .slide-name {")
    assert _declared(rule, "white-space") == "nowrap" and _declared(rule, "overflow-wrap") == "normal"
    assert "overflow-wrap: anywhere" in _rule(css, ".slide-name {")  # JS가 없으면 기존처럼 줄바꿈으로 넘침을 막는다
    assert _declared(_rule(css, ".slide {"), "grid-template-columns") == "minmax(0, 1fr)"  # 열이 글자를 따라 늘어나 잘리지 않게 고정
    assert _declared(_rule(css, ".slide-name {"), "font-size") == "clamp(2.75rem, 6.5vw, 5.25rem)"  # 기본 크기는 그대로

    js = _get("/static/slide-fit.js").text
    assert re.search(r"export const MIN_FONT_PX = 28;", js)
    for needle in ("fitFontSize", "MutationObserver", "ResizeObserver", "fonts?.ready", "removeProperty", "white-space"):
        assert needle in js, needle
    for forbidden in ("innerHTML", "fetch(", "http", "XMLHttpRequest", "eval("):
        assert forbidden not in js, forbidden
    # 기업이 추가돼 가장 긴 이름이 달라져도 축소·줄바꿈 로직을 유지한다.
    names = re.findall(r'name: "([^"]+)"', _get("/static/landing-data.js").text)
    assert names and "한세예스24홀딩스" in names


def test_link_feature_tiles_style_and_priority():
    """링크 칸은 유리 칸 값을 그대로 쓰고, 전역 button 규칙과 부딪치지 않는다(개정 8 AC-61)."""
    landing = _get("/static/landing.css").text
    login = _get("/static/login.css").text
    base = landing[landing.rindex("a.feature-tile {") :]  # 마지막 것이 기본 규칙(앞의 것은 .stage-features 범위 규칙)
    link = base[: base.index("}")]
    assert _declared(link, "text-decoration") == "none"
    assert _declared(link, "cursor") == "pointer"
    assert _declared(link, "box-sizing") == "border-box"  # button은 기본이 border-box라 a도 맞춰야 칸이 넘치지 않는다
    assert "아직 준비 중인 기능</span>" not in _get("/").text
    # 링크 칸과 준비 중 칸의 높이가 같다(준비 중 칸은 .feature-tile:disabled의 min-height를 쓴다)
    same = _declared(_rule(landing, ".stage-features a.feature-tile {"), "min-height")
    assert same == _declared(_rule(landing, ".feature-tile:disabled {"), "min-height") == "5.5rem"
    assert "backdrop-filter" not in link
    assert "outline" in _rule(landing, "a.feature-tile:focus-visible {")
    # 준비 중 칸(button:disabled)의 규칙은 그대로다
    assert _declared(_rule(landing, ".feature-tile:disabled {"), "cursor") == "default"
    # 랜딩은 reviews.css를 불러오지 않고, login.css에는 a 전역 규칙이 없다
    assert "reviews.css" not in _get("/").text
    import re

    assert not re.search(r"(?m)^a[ ,{]", login)


def test_feature_tiles_open_the_login_dialog_with_a_remembered_destination():
    """링크 칸은 로그인 창을 열고, 로그인 뒤 허용 목록의 도착지로 간다(개정 8 AC-64·65)."""
    import re

    html = _get("/").text
    assert re.search(r'<script type="module" src="[^"]*/static/feature-login\.js"></script>', html)
    script = _get("/static/feature-login.js").text
    for forbidden in ("innerHTML", "fetch(", "http", "XMLHttpRequest", "eval("):
        assert forbidden not in script, forbidden
    assert "preventDefault" in script and '"open-login"' in script and "login-next" in script
    allowed = ['"/reviews"', '"/?open=search"', '"/?open=reviews"', '"/?open=reports"', '"/?open=approvals"', '"/?open=rules"']
    login = _get("/static/login.js").text
    assert "login-next" in login and "innerHTML" not in login
    assert all(item in script for item in allowed)
    assert all(item in login or item == '"/reviews"' for item in allowed)  # login.js는 REVIEWS_PATH 상수를 쓴다
    assert all(path in login for path in allowed if path != '"/reviews"')
    # 로그인하지 않은 사용자의 마지막 수단: 링크 자체는 그대로 있다
    assert 'href="/?open=approvals"' in html

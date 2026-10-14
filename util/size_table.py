import re
import time
import base64
import requests
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from openai import OpenAI
from typing import get_args
from schema.size import SizeTable, SizeRow, SizeMeasure, SizeSystemValue, StdPart

SLICE_W = 768          # Gemini 타일 크기. 768x768 조각 1장 ≈ 258 토큰
OVERLAP = 0.2          # 조각 경계에 걸친 표가 잘리지 않도록 겹침
MAX_SLICES = 48
SIZE_HINT = re.compile(r"size|%EC%82%AC%EC%9D%B4%EC%A6%88|사이즈|실측", re.I)
SKIP_HINT = re.compile(r"logo|icon|button|banner|tracker|pixel|sns|\.gif", re.I)

SIZE_WORD = re.compile(r"실측|사이즈|SIZE|치수|총장|총기장|어깨|가슴|허리", re.I)
SIZE_NUM = re.compile(r"(?<![\d.,])\d{2,3}(?:\.\d+)?(?![\d,])")
HTML_WINDOW = 2500
HTML_MAX_CHARS = 8000

IMAGE_INTRO = """너는 이커머스 상세페이지에서 사이즈표를 찾아 구조화하는 도구다.
아래에 [조각 N] 번호가 붙은 이미지 조각들이 주어진다. 긴 상세페이지를 잘라낸 것이라 하나의 표가 연속된 조각에 나뉘거나 겹쳐 보일 수 있다.
"""
HTML_INTRO = """너는 이커머스 상세페이지에서 사이즈표를 찾아 구조화하는 도구다.
아래는 상세페이지 HTML 에서 뽑은 텍스트 일부다. HTML 표는 한 줄이 한 행, 셀은 ' | ' 로 구분했다.
항목명 표와 값 표가 따로 떨어져 있을 수 있으니 순서대로 짝지어라. source_slices 는 빈 배열로 둔다.
"""
RULES = """
규칙:
1. 사이즈별 수치가 적힌 표(사이즈표/SIZE GUIDE/실측사이즈)를 찾아라. 모델 착용 정보(키, 착용사이즈)는 사이즈표가 아니다.
2. kind: 제품 자체를 잰 실측표=measurement, 착용자 신체치수 기준(케어라벨/택의 호칭·신체치수 등)=body,
   호수·국가별 사이즈 환산표(KR/US/JP mm 등)=conversion. 실측표가 있으면 실측표를 우선한다.
3. rows 는 반드시 '사이즈 하나당 한 행'이다. 표가 사이즈를 열로 배치했으면 행/열을 바꿔서 넣어라. 헤더 행(사이즈/사이즈스펙 등)은 행이 아니다.
4. 표가 여러 조각에 걸치거나 겹쳐 보이면 하나로 합치고 중복 행은 한 번만 넣어라.
   단, 상의/하의 세트나 성별·기장별처럼 서로 다른 표는 모두 넣고 group 으로 구분하라.
5. 숫자는 보이는 값 그대로 옮겨라. 보이지 않거나 '-' 인 값은 넣지 마라. 추측 금지.
6. std_name 매핑: 화장(목뒤~소매끝)=화장, 소매길이(어깨~소매끝)=소매길이, 브라 밑가슴/밑둘레=기타. 애매하면 기타.
7. 사이즈표가 없으면 found=false, kind=none, rows=[].
8. kind=conversion(국가별 환산표)이면 값을 measures 가 아니라 systems 에 넣어라. 표의 모든 열(JP/KR mm, US, UK, EU, FR 등)을
   빠짐없이 넣고, label 은 mm 열이 있으면 mm 값으로 한다. 남성/여성 등 표가 나뉘어 있으면 모두 넣고 group 으로 구분하라.
"""


def extract_detail_image_urls(html):
    """상세설명 HTML의 img src 를 순서대로 (중복/아이콘 제외)"""
    urls = []
    for img in BeautifulSoup(html or "", "html.parser").find_all("img"):
        src = (img.get("src") or "").strip()
        if src.startswith("//"):
            src = "https:" + src
        if src.startswith("http") and src not in urls and not SKIP_HINT.search(src):
            urls.append(src)
    return urls


def _slice(url):
    """이미지를 받아 가로 SLICE_W 이하 정사각형 조각(JPEG bytes) 리스트로"""
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content)).convert("RGB")
    except Exception as e:
        print(f"이미지 실패 {url}: {e}")
        return []
    w, h = img.size
    if w < 200 or h < 150:
        return []
    if w > SLICE_W:
        img = img.resize((SLICE_W, int(h * SLICE_W / w)), Image.Resampling.LANCZOS)
        w, h = img.size
    step = int(w * (1 - OVERLAP))
    out, y = [], 0
    while True:
        bottom = min(y + w, h)
        buf = BytesIO()
        img.crop((0, max(0, bottom - w), w, bottom)).save(buf, format="JPEG", quality=90)
        out.append(buf.getvalue())
        if bottom >= h:
            return out
        y += step


def build_slices(urls):
    """조각 리스트 [(url, bytes)]. 파일명 힌트 이미지 → 페이지 끝쪽 이미지 순으로 MAX_SLICES 까지"""
    # shortcut: 조각이 MAX_SLICES 를 넘으면 페이지 앞쪽 이미지는 버림. 앞쪽에 표가 있는 브랜드가 많으면 2단계(탐지→추출)로 전환
    order = [u for u in urls if SIZE_HINT.search(u)] + [u for u in reversed(urls) if not SIZE_HINT.search(u)]
    with ThreadPoolExecutor(8) as ex:
        sliced = list(ex.map(_slice, order))
    picked = []
    for url, parts in zip(order, sliced):
        for p in parts:
            if len(picked) >= MAX_SLICES:
                return picked
            picked.append((url, p))
    return picked


def html_size_text(html):
    """HTML 텍스트(표는 ' | ' 행으로)에서 사이즈 키워드 뒤에 치수 숫자가 몰린 구간들만. 없으면 None"""
    soup = BeautifulSoup(html or "", "html.parser")
    for t in soup.find_all("table"):
        rows = [" | ".join(c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])) for tr in t.find_all("tr")]
        t.replace_with("\n" + "\n".join(rows) + "\n")
    text = re.sub(r"\n\s*\n+", "\n", soup.get_text("\n", strip=True))
    segs, end = [], -1
    for m in SIZE_WORD.finditer(text):
        if m.start() < end:
            continue
        seg = text[max(0, m.start() - 200): m.start() + HTML_WINDOW]
        if len(SIZE_NUM.findall(seg)) >= 6:
            segs.append(seg)
            end = m.start() + HTML_WINDOW
    return "\n...\n".join(segs)[:HTML_MAX_CHARS] or None


def _llm(parts, api_key, model_name, base_url=None):
    """parts: str(텍스트) / bytes(JPEG) 리스트 → (SizeTable 후처리본|None, in_tok, out_tok)"""
    if "gemini" in model_name.lower():
        table, in_tok, out_tok = _call_gemini(parts, api_key, model_name)
    else:
        table, in_tok, out_tok = _call_openai(parts, api_key, model_name, base_url)
    return (clean_conversion(apply_half_rule(fix_transposed(table))) if table else None), in_tok, out_tok


def clean_conversion(table):
    """환산표는 systems 만 사용: 모델이 mm 등을 measures(총장 등)에 중복으로 넣는 것 제거"""
    if table.kind == "conversion":
        for r in table.rows:
            r.measures = []
            for v in r.systems:
                if re.search(r"\bmm\b", v.raw_name, re.I):   # 'JP (mm)', 'KR(mm)' 은 길이값 → MM
                    v.system = "MM"
    return table


GENDER_GROUP = {"women": re.compile(r"WOMEN|WOMAN|여성|여자|레이디", re.I),
                "men": re.compile(r"(?<!WO)MEN\b|(?<!WO)MAN\b|남성|남자", re.I)}


def gender_first(rows, gender):
    """상품 성별과 맞는 group 행을 앞으로 (남녀 표가 나뉜 환산표용). 성별 미상이면 그대로"""
    pat = GENDER_GROUP.get(gender)
    return sorted(rows, key=lambda r: 0 if pat and pat.search(r.group or "") else 1) if pat else rows


def extract_size_table(html, api_key, model_name="gpt-6-luna", base_url=None):
    """상세 HTML 에서 사이즈표 추출: HTML 텍스트 표 → (실측표 못 얻으면) 이미지.
    return (SizeTable|None, meta dict). meta['source'] = 'html' | 'image' | None"""
    t0 = time.time()
    meta = {"source": None, "n_slices": 0, "in_tok": 0, "out_tok": 0}
    html_table = None

    text = html_size_text(html)
    if text:
        html_table, i, o = _llm([HTML_INTRO + RULES, text], api_key, model_name, base_url)
        meta.update(html_chars=len(text), html_text=text, in_tok=i, out_tok=o)
        if html_table and html_table.kind == "measurement":
            meta.update(source="html", sec=round(time.time() - t0, 1))
            return html_table, meta

    slices = build_slices(extract_detail_image_urls(html))
    meta.update(n_slices=len(slices), slices=slices)
    table = None
    if slices:
        parts = [IMAGE_INTRO + RULES]
        for i, (_, b) in enumerate(slices):
            parts += [f"[조각 {i}]", b]
        table, i, o = _llm(parts, api_key, model_name, base_url)
        meta.update(in_tok=meta["in_tok"] + i, out_tok=meta["out_tok"] + o)
    found = lambda t: t is not None and t.found and t.kind != "none"
    if found(table):
        meta["source"] = "image"
    elif found(html_table):
        table, meta["source"] = html_table, "html"
    meta["sec"] = round(time.time() - t0, 1)
    return table, meta


def _call_gemini(parts, api_key, model_name):
    contents = [types.Part.from_bytes(data=p, mime_type="image/jpeg") if isinstance(p, bytes) else p for p in parts]
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=model_name,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=SizeTable,
        ),
    )
    u = resp.usage_metadata
    return resp.parsed, u.prompt_token_count, u.candidates_token_count


def _call_openai(parts, api_key, model_name, base_url=None):
    """base_url: LiteLLM 게이트웨이 주소 (None 이면 OpenAI 직접)"""
    content = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(p).decode(),
                                                   "detail": "high"}}
               if isinstance(p, bytes) else {"type": "text", "text": p} for p in parts]
    resp = OpenAI(api_key=api_key, base_url=base_url).beta.chat.completions.parse(
        model=model_name,
        messages=[{"role": "user", "content": content}],
        response_format=SizeTable,
        # gpt-5/6 계열(추론 모델)은 temperature 고정값만 허용 → gpt-4 계열에만 0 지정
        **({"temperature": 0} if model_name.startswith(("gpt-4", "gpt-3")) else {}),
    )
    return resp.choices[0].message.parsed, resp.usage.prompt_tokens, resp.usage.completion_tokens


def norm_label(s):
    """'00M' / 'M (100)' / '066' / '085(085 (90))' → 'M' / 'M' / '66' / '85'"""
    s = re.sub(r"\(.*", "", str(s)).strip().upper().replace(" ", "")
    return s.lstrip("0") or s


def label_keys(s):
    """표 라벨의 매칭 후보: 'M (OME)' → {'M', 'OME'}"""
    return {norm_label(s)} | {norm_label(x) for x in re.findall(r"\(([^()]+)\)", str(s))}


def match_row(label, rows):
    """옵션 라벨에 해당하는 표 행. 본 라벨이 같은 행 우선, 없으면 괄호 병기값으로.
    ('085(085 (90))' 의 (90) 때문에 옵션 090 이 085 행에 잘못 붙는 것 방지)"""
    k = norm_label(str(label).split("/")[-1])
    return next((r for r in rows if norm_label(r.label) == k), None) or next((r for r in rows if k in label_keys(r.label)), None)


GIRTH_PARTS = {"가슴둘레", "허리둘레", "엉덩이둘레", "허벅지둘레", "밑단둘레", "소매통", "발볼"}


def apply_half_rule(table):
    """'-'(모델이 0/-1 로 넣음) 값 제거. 항목명이 표준 부위명 그대로면 std_name 고정.
    단면 여부는 LLM 대신 규칙으로: '단면' > '둘레' > 표 안내문구 (둘레 계열만)"""
    for r in table.rows:
        r.measures = [m for m in r.measures if m.value > 0]
        for m in r.measures:
            if m.raw_name.replace(" ", "") in get_args(StdPart):
                m.std_name = m.raw_name.replace(" ", "")
            m.is_half = m.std_name in GIRTH_PARTS and (
                "단면" in m.raw_name or ("둘레" not in m.raw_name and table.half_note))
    return table


def _is_part_name(s):
    """'허리둘레' 처럼 숫자 없는 한글 항목명"""
    return bool(re.search("[가-힣]", s)) and not re.search(r"\d", s)


def fix_transposed(table):
    """모델이 행=부위, 열=사이즈로 넣은 표를 행=사이즈로 되돌림"""
    rows = [r for r in table.rows if not re.search("사이즈|size", r.label, re.I)]
    if not rows or sum(map(_is_part_name, (r.label for r in rows))) * 2 < len(rows):
        return table
    sizes = list(dict.fromkeys(m.raw_name for r in rows for m in r.measures))
    if sum(_is_part_name(s) for s in sizes) * 2 >= len(sizes):
        return table
    new_rows = [SizeRow(group=g, label=s, measures=[
        SizeMeasure(raw_name=r.label, std_name=m.std_name, is_half=m.is_half, value=m.value)
        for r in rows if r.group == g for m in r.measures if m.raw_name == s])
        for g in dict.fromkeys(r.group for r in rows) for s in sizes]
    return table.model_copy(update={"rows": [r for r in new_rows if r.measures]})


def option_size_labels(product):
    """상품 API data 의 옵션에서 사이즈 라벨만"""
    labels = []
    for o in product.get("optionItem") or []:
        if re.search("사이즈|SIZE", o.get("optItemNm", ""), re.I):
            for v in o.get("optValueList", []):
                lb = norm_label(v.get("optValueNm", "").split("/")[-1])
                if lb not in labels:
                    labels.append(lb)
    return labels


UNIT_TO_CM = {"cm": 1, "mm": 0.1, "inch": 2.54, "in": 2.54}


def measure_attrs(table):
    """실측표 → 범위 필터용 값 [{group, label, 표준부위: cm}]. 둘레 계열 단면은 ×2(둘레 기준), mm/inch → cm.
    '기타' 부위는 제외, 같은 부위가 여럿이면 첫 값"""
    if not table or table.kind != "measurement":
        return []
    f = UNIT_TO_CM.get((table.unit or "cm").strip().lower(), 1)
    out = []
    for r in table.rows:
        d = {"group": r.group, "label": r.label}
        for m in r.measures:
            if m.std_name != "기타":
                d.setdefault(m.std_name, round(m.value * f * (2 if m.is_half and m.std_name in GIRTH_PARTS else 1), 1))
        out.append(d)
    return out


def validate(table, option_labels):
    """규칙 검증. 문제 목록(빈 리스트면 통과)"""
    if not table or not table.found or not table.rows:
        return ["사이즈표 없음"]
    issues = []
    rows = table.rows
    if option_labels:
        tl = set().union(*(label_keys(r.label) for r in rows))
        miss = [l for l in option_labels if l not in tl]
        if miss:
            issues.append(f"옵션 라벨 미매칭 {miss}")
    for r in rows:
        bad = [m.raw_name for m in r.measures if not 0 < m.value <= 300]
        if bad:
            issues.append(f"{r.label} 범위밖 {bad}")
    if table.kind == "measurement":
        # 사이즈가 커질수록 같은 부위 값은 줄지 않아야 함 (측정 오차 1cm 허용)
        # 46S/48R(수트 기장), 75A/75B(브라 컵) 처럼 숫자+영문1자 라벨은 접미 문자별로 따로 비교
        key = lambda r: (r.group, m.group(1) if (m := re.fullmatch(r"\d+([A-Z])", norm_label(r.label))) else "")
        for g in {key(r) for r in rows}:
            grows = [r for r in rows if key(r) == g]
            for n in {m.raw_name for r in grows for m in r.measures}:
                vals = [m.value for r in grows for m in r.measures if m.raw_name == n]
                if any(b < a - 1 for a, b in zip(vals, vals[1:])):
                    issues.append(f"{''.join(g)} {n} 단조증가 위반 {vals}".strip())
    return issues


if __name__ == "__main__":
    import os, sys, json, tomllib, pathlib

    # self-check
    assert norm_label("00M") == "M" and norm_label("085(085 (90))") == "85" and norm_label(" M (100)") == "M"
    _m = lambda v: SizeMeasure(raw_name="허리", std_name="허리둘레", is_half=False, value=v)
    _t = SizeTable(found=True, kind="measurement", unit="cm", half_note=False, source_slices=[0],
                   rows=[SizeRow(group="", label="066", measures=[_m(67)]), SizeRow(group="", label="069", measures=[_m(65)])])
    assert any("단조증가" in i for i in validate(_t, ["66", "69"]))
    assert any("미매칭" in i for i in validate(_t, ["66", "72"]))
    assert label_keys("M (OME)") == {"M", "OME"}
    _lf_rows = [SizeRow(group="", label="085(085 (90))", measures=[]), SizeRow(group="", label="090(090 (95))", measures=[])]
    assert match_row("Navy/090", _lf_rows).label.startswith("090") and match_row("085", _lf_rows).label.startswith("085")
    assert match_row("OME", [SizeRow(group="", label="M (OME)", measures=[])]).label == "M (OME)"
    _w = lambda n, v: SizeMeasure(raw_name=n, std_name="허리둘레", is_half=False, value=v)
    _tt = fix_transposed(_t.model_copy(update={"rows": [
        SizeRow(group="", label="사이즈스펙", measures=[_w("066", 1), _w("069", 2)]),
        SizeRow(group="", label="허리둘레", measures=[_w("066", 67), _w("069", 70)])]}))
    assert [r.label for r in _tt.rows] == ["066", "069"] and _tt.rows[1].measures[0].raw_name == "허리둘레"
    assert fix_transposed(_t) == _t
    _h = lambda n, std: SizeMeasure(raw_name=n, std_name=std, is_half=False, value=50)
    _ht = apply_half_rule(SizeTable(found=True, kind="measurement", unit="cm", half_note=True, source_slices=[0], rows=[
        SizeRow(group="", label="M", measures=[_h("가슴단면", "가슴둘레"), _h("허리둘레", "총장"), _h("허벅지", "허벅지둘레"), _h("어깨", "어깨너비")])]))
    assert [m.is_half for m in _ht.rows[0].measures] == [True, False, True, False]
    _suit = SizeTable(found=True, kind="measurement", unit="cm", half_note=False, source_slices=[], rows=[
        SizeRow(group="", label=lb, measures=[_m(v)]) for lb, v in [("46S", 69.7), ("48R", 72.1), ("48S", 70.1), ("50R", 72.5)]])
    assert validate(_suit, []) == []
    assert apply_half_rule(SizeTable(found=True, kind="measurement", unit="cm", half_note=False, source_slices=[], rows=[
        SizeRow(group="", label="S", measures=[_m(-1), _m(70)])])).rows[0].measures == [_m(70)]
    assert _ht.rows[0].measures[1].std_name == "허리둘레"
    _lf = ("<h4>실측 사이즈</h4><table><tr><th>원피스</th></tr><tr><td>A 총길이</td></tr><tr><td>B 가슴둘레</td></tr></table>"
           "<table><tr><td>085(085 (90))</td><td>090(090 (95))</td></tr><tr><td>116.8</td><td>117.4</td></tr>"
           "<tr><td>91.4</td><td>96.4</td></tr></table>")
    assert "085(085 (90)) | 090(090 (95))\n116.8 | 117.4" in html_size_text(_lf)
    assert html_size_text("<p>사이즈 교환 문의 2024년</p>") is None
    _ma = measure_attrs(SizeTable(found=True, kind="measurement", unit="CM", half_note=True, source_slices=[], rows=[
        SizeRow(group="", label="S", measures=[SizeMeasure(raw_name="가슴단면", std_name="가슴둘레", is_half=True, value=50),
                                              SizeMeasure(raw_name="총장", std_name="총장", is_half=True, value=68),
                                              SizeMeasure(raw_name="밑가슴", std_name="기타", is_half=False, value=70)])]))
    assert _ma == [{"group": "", "label": "S", "가슴둘레": 100, "총장": 68}], _ma
    assert measure_attrs(_ht.model_copy(update={"kind": "body"})) == []
    _cv = clean_conversion(SizeTable(found=True, kind="conversion", unit="mm", half_note=False, source_slices=[], rows=[
        SizeRow(group=g, label="230", measures=[_m(230)], systems=[SizeSystemValue(system="US", raw_name="US", value="5"),
                                                                  SizeSystemValue(system="JP", raw_name="JP (mm)", value="230")])
        for g in ("MEN / UNI (남성 / 유니)", "WOMEN (여성)")]))
    assert _cv.rows[0].measures == [] and _cv.rows[0].systems[0].value == "5" and _cv.rows[0].systems[1].system == "MM"
    assert gender_first(_cv.rows, "women")[0].group == "WOMEN (여성)"
    assert gender_first(_cv.rows, "men")[0].group.startswith("MEN")

    # 사용법: python -m util.size_table <out_dir> <prdNo...>
    out_dir = pathlib.Path(sys.argv[1]); out_dir.mkdir(parents=True, exist_ok=True)
    model = os.environ.get("SIZE_MODEL", "gpt-6-luna")
    secrets = tomllib.load(open(".streamlit/secrets.toml", "rb"))
    # 기본은 게이트웨이 경유. SIZE_DIRECT=1 이면 OpenAI 직접(게이트웨이 미허용 모델 평가용)
    direct = os.environ.get("SIZE_DIRECT") == "1" or "gemini" in model
    key = secrets["GOOGLE_API_KEY_LSS" if "gemini" in model else "OPENAI_API_KEY" if direct else "AI_GATEWAY_KEY"]
    base_url = None if direct else secrets["AI_GATEWAY_URL"]
    for prd_no in sys.argv[2:]:
        d = requests.get(f"https://hapix.halfclub.com/product/products/withoutPrice/{prd_no}",
                         params=dict(countryCd="001", langCd="001", siteCd="1", deviceCd="001", mandM="halfclub"),
                         timeout=10).json()["data"]
        if not d:   # 판매종료 등으로 상품 데이터 없음
            print(json.dumps({"prdNo": prd_no, "error": "상품 데이터 없음"}, ensure_ascii=False))
            continue
        html = (d.get("productDesc") or {}).get("prdDescContClob")
        try:
            table, meta = extract_size_table(html, key, model, base_url)
        except Exception as e:
            table, meta = None, {"error": str(e)}
        labels = option_size_labels(d)
        rec = {"prdNo": prd_no, "brand": d["brandMainNmKr"], "name": d["prdNm"], "option_labels": labels,
               "issues": validate(table, labels), "table": table.model_dump() if table else None,
               **{k: v for k, v in meta.items() if k not in ("slices", "slice_urls")}}
        # 검수용: 모델이 표를 봤다고 한 조각 저장
        for i in (table.source_slices if table else []):
            if 0 <= i < len(meta.get("slices", [])):
                (out_dir / f"{prd_no}_s{i}.jpg").write_bytes(meta["slices"][i][1])
        (out_dir / f"{prd_no}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
        print(json.dumps({k: rec[k] for k in ("prdNo", "brand", "name", "issues")} |
                         {"kind": table.kind if table else None, "rows": len(table.rows) if table else 0,
                          **{k: meta.get(k) for k in ("source", "html_chars", "n_slices", "sec", "in_tok", "out_tok", "error")}},
                         ensure_ascii=False))

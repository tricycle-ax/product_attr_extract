"""옵션 사이즈 라벨 정규화: (카테고리군, 라벨) → 기준값 + 화면표기 + 보조속성.

카테고리군별 기준값(canonical)
  상의(top)          가슴 호칭 cm   표기 'S (90)'
  하의(bottom)       허리 cm        표기 '26인치 (66)' / 알파면 'S'
  정장재킷(suit)     EU 가슴 호칭   표기 '48R'  (+기장 S/R/L)
  신발(shoes/kids_shoes) 발길이 mm  표기 '255mm'
  아동복(kids)       키 cm          표기 '110호'
  브라(bra)          밑가슴 밴드 cm + 컵  표기 '75B'
  속옷 하의(underwear) 엉덩이 호칭   표기 'M (95)'
대응값은 업계 통용 근사치. 상품 자체 사이즈표가 있으면 그쪽이 우선.
"""
import re

ALPHA_ORDER = ["XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL", "5XL"]
# 알파 ↔ 가슴(상의)/엉덩이(속옷) 호칭. 남녀 공통으로 씀 (캘빈클라인 M(95), 29CM S(90) 관측)
ALPHA_TO_CHEST = {"XS": 85, "S": 90, "M": 95, "L": 100, "XL": 105, "2XL": 110, "3XL": 115, "4XL": 120, "5XL": 125}
CHEST_TO_ALPHA = {v: k for k, v in ALPHA_TO_CHEST.items()}
# 여성 옛 호칭(1981 규격) → 가슴 호칭
LEGACY_TO_CHEST = {44: 85, 55: 90, 66: 95, 77: 100, 88: 105, 99: 110}
ALPHA_ALIAS = {"XXS": "XS", "SS": "S", "XXL": "2XL", "XXXL": "3XL", "XXXXL": "4XL",
               "OME": "M", "OLA": "L", "OXL": "XL", "OSM": "S"}  # O**: 팬암 등 내부코드
ONE_SIZE = re.compile(r"^(F|FREE|FR|FFF|OS|ONE ?SIZE|원사이즈|프리|프리사이즈|000|XXX)$")
LENGTH_WORDS = {"숏": "short", "기본": "regular", "롱": "long"}
WIDTH = {"B": "standard_w", "D": "standard_m", "2E": "wide", "EE": "wide", "4E": "extra_wide", "EEEE": "extra_wide"}


def size_group(ctgr, prd_nm=""):
    """전시카테고리 [대,중,소] (+상품명) → (카테고리군, 성별 women|men|unisex)"""
    c = " ".join(x for x in ctgr if x)
    t = c + " " + (prd_nm or "")
    gender = "women" if re.search("여성|우먼|레이디", c) else "men" if "남성" in c else \
        "women" if re.search("여성|우먼", prd_nm or "") else "men" if "남성" in (prd_nm or "") else "unisex"
    if re.search("아동|키즈|유아|주니어", c):
        return ("kids_shoes" if re.search("슈즈|신발|아동화|운동화", c) else "kids"), gender
    # 신발은 상품명도 봄: 카테고리 누락·의류 카테고리에 등록된 골프화 등. '부츠컷'(바지)은 제외
    if re.search("슈즈|신발|운동화|런닝화|스니커즈|부츠(?!컷)|샌들|구두|정장화|골프화|슬리퍼", t):
        return "shoes", gender
    if "브라" in c:
        return "bra", "women"
    if re.search("언더웨어|속옷|팬티", c):
        return "underwear", gender
    if re.search("잡화|장갑|모자|양말|벨트|가방|ACC|선글라스|주얼리|시계", c):
        return "acc", gender
    if re.search("수트|셋업", c) and re.search("자켓|재킷", c):
        return "suit", gender
    if re.search("팬츠|바지|데님|하의|스커트|레깅스|슬랙스", c):
        return "bottom", gender
    return "top", gender


def _alpha(s):
    """'00S'/'0XL'/'XXL'/'OME' → 'S'/'XL'/'2XL'/'M', 아니면 None"""
    s = s.lstrip("0") if re.fullmatch(r"0+[SMLX]+", s) else s
    s = ALPHA_ALIAS.get(s, s)
    return s if s in ALPHA_ORDER else None


def _clean(raw):
    s = str(raw).replace("（", "(").replace("）", ")").strip().upper()
    return s.strip(".").strip()


def parse_size_label(raw, group, gender="unisex"):
    """라벨 1개 정규화. 반환 dict:
    raw, kind(alpha|chest|waist|eu_suit|mm|height|band_cup|one_size|code|unknown), value(기준값), alpha, display,
    systems(국가/병기 값), length_variant, width, age_range, band, cup"""
    out = {"raw": raw, "kind": "unknown", "value": None, "alpha": None, "display": str(raw), "systems": {},
           "length_variant": None, "width": None, "age_range": None, "band": None, "cup": None}
    segs = [x for x in _clean(raw).split("/") if x.strip()]
    if not segs:
        return out
    # 'S/44~55' (알파/옛호칭)은 앞 조각, 그 외 '컬러/085' 는 뒤 조각부터 파싱되는 첫 값
    alpha_first = len(segs) == 2 and _alpha(segs[0].strip()) and re.fullmatch(r"\d+(~\d*)?", segs[1].strip())
    for s in [segs[0]] if alpha_first else reversed(segs):
        r = _parse_seg(s.strip(), group, gender)
        if r:
            out.update(r)
            return out
    return out


def _parse_seg(s, group, gender):
    if ONE_SIZE.match(s.replace(" ", "")):
        return {"kind": "one_size", "display": "FREE"}
    length = None
    if m := re.match(r"^(숏|기본|롱)\s*(.+)$", s):
        length, s = LENGTH_WORDS[m.group(1)], m.group(2).strip()
    r = {"shoes": _shoes, "kids_shoes": _shoes, "kids": _kids, "bra": _bra, "suit": _suit,
         "bottom": _bottom, "acc": _acc}.get(group, _top)(s, gender)
    if r and length:
        r["length_variant"] = length
    return r


def _top(s, gender):
    """상의/속옷/잡화: 알파·가슴호칭·옛호칭과 병기 'M(95)' '85(S)' 'S(55)' 'M95'"""
    m = re.fullmatch(r"([0-9A-Z]+)\s*\(([^()]+)\)", s) or re.fullmatch(r"(\d?[A-Z]+)\s*(\d{2,3})", s)
    parts = [m.group(1), m.group(2)] if m else [s]
    alpha = chest = None
    for p in parts:
        p = p.replace(" ", "")
        if a := _alpha(p):
            alpha = alpha or a
        elif re.fullmatch(r"\d{2,3}", p):
            n = int(p)
            if n in LEGACY_TO_CHEST and not (p.startswith("0") and n > 77):
                chest = chest or LEGACY_TO_CHEST[n]   # 044/055 처럼 0 붙은 옛호칭도 허용
            elif 75 <= n <= 150:   # 빅사이즈 와이셔츠 140 관측
                chest = chest or n
    if chest is None and alpha is None:
        return None
    if chest is None:
        chest = ALPHA_TO_CHEST[alpha]
    alpha = alpha or CHEST_TO_ALPHA.get(chest)
    return {"kind": "chest", "value": chest, "alpha": alpha, "display": f"{alpha} ({chest})" if alpha else str(chest)}


def _bottom(s, gender):
    """하의: 허리 cm('067','76'), 인치('28'), 옛호칭(여성 '55'), 알파, 'W30L32'"""
    if m := re.fullmatch(r"W?(\d{2})\s*[L/X]\s*L?(\d{2})", s.replace(" ", "")):
        w = int(m.group(1))
        return {"kind": "waist", "value": round(w * 2.54), "display": f"{w}인치 ({round(w * 2.54)})",
                "systems": {"INCH_WAIST": w, "INSEAM_INCH": int(m.group(2))}}
    if a := _alpha(s.replace(" ", "")):
        return {"kind": "alpha", "value": None, "alpha": a, "display": a}
    if m := re.fullmatch(r"(\d{2,3})\((\d{2})\)", s.replace(" ", "")):   # '76(30)': 허리 cm(인치) 병기
        cm, inch = int(m.group(1)), int(m.group(2))
        return {"kind": "waist", "value": cm, "display": f"{inch}인치 ({cm})", "systems": {"INCH_WAIST": inch}}
    if not (m := re.fullmatch(r"(\d{2,3})(인치|IN)?", s.replace(" ", ""))):
        return None
    n = int(m.group(1))
    if gender == "women" and n in LEGACY_TO_CHEST and not m.group(1).startswith("0"):
        a = CHEST_TO_ALPHA[LEGACY_TO_CHEST[n]]   # 여성 하의 '55/66' 은 허리값이 아니라 옛호칭
        return {"kind": "alpha", "value": None, "alpha": a, "display": f"{a} ({n})", "systems": {"KR_LEGACY": n}}
    if 22 <= n <= 46:
        return {"kind": "waist", "value": round(n * 2.54), "display": f"{n}인치 ({round(n * 2.54)})",
                "systems": {"INCH_WAIST": n}}
    if 55 <= n <= 130:
        inch = round(n / 2.54)
        return {"kind": "waist", "value": n, "display": f"{inch}인치 ({n})", "systems": {"INCH_WAIST": inch}}
    return None


def _suit(s, gender):
    if m := re.fullmatch(r"(\d{2})([SRL])", s.replace(" ", "")):
        eu = int(m.group(1))
        return {"kind": "eu_suit", "value": eu, "display": s.replace(" ", ""), "systems": {"EU": eu},
                "length_variant": {"S": "short", "R": "regular", "L": "long"}[m.group(2)]}
    return _top(s, gender)


def _shoes(s, gender):
    """'255', '255MM', '255MM(US7.5)', 'EU 39 (250MM)', 'EU40(255~260MM)', '5.5 B (225MM)', '250(39)',
    '140MM(8C)', '210MM(2Y)', '170MM (PS)'"""
    t = s.replace(" ", "")
    systems, width = {}, None
    mm = re.search(r"(\d{3})(?:~(\d{3}))?MM", t) or re.fullmatch(r"(\d{3})(?:\((\d{2}(?:\.5)?)\))?", t)
    if m := re.search(r"US(\d+(?:\.5)?)", t):
        systems["US_W" if gender == "women" else "US_M"] = float(m.group(1))
    if m := re.search(r"EU(\d{2}(?:\.5)?)", t):
        systems["EU"] = float(m.group(1))
    if m := re.search(r"\b(\d{1,2}(?:\.5)?)([CY])\b|\((\d{1,2}(?:\.5)?)([CY])\)", s):
        num, cy = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        systems["US_KIDS"] = f"{num}{cy}"
    if m := re.search(r"\((PS|GS|TD)\)", t):
        systems["NIKE_KIDS"] = m.group(1)
    if m := re.match(r"(\d{1,2}(?:\.5)?)(B|D|2E|EE|4E|EEEE)\(", t):   # '5.5 B (225mm)': US + 발볼
        systems["US_W" if m.group(2) == "B" else "US_M"] = float(m.group(1))
        width = WIDTH[m.group(2)]
    if not mm:
        return None
    lo = int(mm.group(1))
    if not 90 <= lo <= 320:
        return None
    if mm.re.pattern.startswith("(\\d{3})(?:~") and mm.group(2):
        systems["MM_MAX"] = int(mm.group(2))
    elif mm.lastindex and mm.group(2) and "MM" not in t:   # '250(39)' → 버켄스탁식 EU 병기
        systems["EU"] = float(mm.group(2))
    return {"kind": "mm", "value": lo, "display": f"{lo}mm", "systems": systems, "width": width}


def _kids(s, gender):
    """'110호', '65호[110][5-6세]', '110', 'S'"""
    t = s.replace(" ", "")
    age = None
    if m := re.search(r"\[(\d+)-(\d+)세\]", t):
        age = (int(m.group(1)), int(m.group(2)))
    if m := re.search(r"\[(\d{2,3})\]", t):    # 대괄호 안이 키(cm), 앞 '65호'는 브랜드 고유 호수
        h = int(m.group(1))
    elif m := re.search(r"(\d{2,3})호?$", t):
        h = int(m.group(1))
    else:
        return _top(s, gender)
    if not 50 <= h <= 175:
        return None
    return {"kind": "height", "value": h, "display": f"{h}호", "age_range": age}


def _acc(s, gender):
    """잡화: 알파 외 숫자는 브랜드 코드(모자 058, 장갑 019, 벨트 064 — 의미가 브랜드마다 다름)"""
    if a := _alpha(s.replace(" ", "")):
        return {"kind": "alpha", "alpha": a, "display": a}
    if re.fullmatch(r"\d{2,3}|[A-Z] ?TYPE", s):
        return {"kind": "code", "display": s}
    return None


def _bra(s, gender):
    if m := re.fullmatch(r"\.?(\d{2})([A-G]{1,3})", s.replace(" ", "")):
        band, cup = int(m.group(1)), m.group(2)
        return {"kind": "band_cup", "value": band, "band": band, "cup": cup, "display": f"{band}{cup}"}
    return _top(s, gender)


def size_option_values(option_items):
    """상품 API optionItem → 사이즈 옵션 원본 라벨 (중복 제거, 괄호 병기 유지)"""
    vals = [v.get("optValueNm", "") for o in option_items or [] if re.search("사이즈|SIZE|치수|호수", o.get("optItemNm", ""), re.I)
            for v in o.get("optValueList", [])]
    return list(dict.fromkeys(vals))


def normalize_options(labels, ctgr, prd_nm=""):
    """상품 옵션 라벨 목록 → 정규화 결과 목록"""
    group, gender = size_group(ctgr, prd_nm)
    return [dict(parse_size_label(l, group, gender), group=group, gender=gender) for l in labels]


def _selftest():
    P = lambda raw, g, gd="unisex": parse_size_label(raw, g, gd)
    assert size_group(["여성의류", "데님팬츠", "와이드"]) == ("bottom", "women")
    assert size_group(["언더웨어", "여성속옷", "브라팬티세트"]) == ("bra", "women")
    assert size_group(["스포츠", "스포츠슈즈", "아동화"]) == ("kids_shoes", "unisex")
    assert size_group(["남성의류", "수트/셋업", "정장자켓"]) == ("suit", "men")
    assert size_group(["골프", "골프잡화", "골프장갑"])[0] == "acc"
    # 상의
    assert P("Navy/085", "top")["display"] == "XS (85)"
    assert P("Dark Brown/00S", "top")["display"] == "S (90)"
    assert P("M(95)", "top")["value"] == 95 and P("85(S)", "top")["alpha"] == "S"
    assert P("S(55)", "top")["value"] == 90 and P("M95", "top")["value"] == 95
    assert P("S/44~55", "top")["alpha"] == "S"
    assert P("Light Grey/OME", "top")["display"] == "M (95)"
    assert P("2XL(110)", "top")["value"] == 110 and P("XXL", "top")["alpha"] == "2XL"
    assert P("FFF", "top")["kind"] == "one_size" and P("C7623_블랙/FREE", "top")["kind"] == "one_size"
    assert P("55.", "top", "women")["value"] == 90
    # 하의
    assert P("Grey/067", "bottom", "women")["value"] == 67
    assert P("28", "bottom")["display"] == "28인치 (71)"
    assert P("55", "bottom", "women")["alpha"] == "S"
    assert P("숏 S", "bottom")["length_variant"] == "short" and P("기본2XL", "bottom")["alpha"] == "2XL"
    assert P("104", "bottom", "men")["value"] == 104
    assert P("104(41)", "bottom", "men")["display"] == "41인치 (104)"
    assert P("W30L32", "bottom")["systems"] == {"INCH_WAIST": 30, "INSEAM_INCH": 32}
    # 정장
    assert P("Dark Navy/48R", "suit", "men")["length_variant"] == "regular"
    # 신발
    assert P("255mm(US7.5)", "shoes", "men")["systems"] == {"US_M": 7.5}
    e = P("EU 40 (255~260mm)", "shoes")
    assert e["value"] == 255 and e["systems"] == {"EU": 40.0, "MM_MAX": 260}
    h = P("5.5 B (225mm)", "shoes", "women")
    assert h["value"] == 225 and h["width"] == "standard_w" and h["systems"]["US_W"] == 5.5
    assert P("250(39)", "shoes")["systems"] == {"EU": 39.0}
    assert P("설명참조/230", "shoes")["value"] == 230
    assert P("140mm(8C)", "kids_shoes")["systems"] == {"US_KIDS": "8C"}
    assert P("170mm （PS）", "kids_shoes")["systems"] == {"NIKE_KIDS": "PS"}
    # 아동·브라
    k = P("비비드65호[110][5-6세]", "kids")
    assert k["value"] == 110 and k["age_range"] == (5, 6)
    assert P("티셔츠BLACK/바지BLACK/120호", "kids")["value"] == 120
    assert P(".80B", "bra")["display"] == "80B" and P("Black/75A", "bra")["cup"] == "A"
    assert P("가나다", "top")["kind"] == "unknown"
    assert size_group(["여성의류", "데님팬츠", "부츠컷"])[0] == "bottom"
    assert size_group([None, None, None], "골드 스퀘어링 구두")[0] == "shoes"
    assert P("2XL110", "top")["value"] == 110 and P("140", "top")["value"] == 140
    assert P("Black/058", "acc")["kind"] == "code" and P("베이지/S type", "acc")["kind"] == "code"


_selftest()

if __name__ == "__main__":
    # 회귀: python -m util.size_label labels_all.json  (조사 상품 라벨 파싱률)
    import sys, json, collections
    rows = json.load(open(sys.argv[1]))
    tot, ok, bad = collections.Counter(), collections.Counter(), collections.defaultdict(list)
    for r in rows:
        for x in normalize_options(r["labels"], r["ctgr"], r["prdNm"]):
            tot[x["group"]] += 1
            if x["kind"] != "unknown":
                ok[x["group"]] += 1
            elif len(bad[x["group"]]) < 8:
                bad[x["group"]].append(x["raw"])
    for g in tot:
        print(f"{g:11s} {ok[g]:4d}/{tot[g]:4d} = {ok[g] / tot[g]:.0%}  미파싱 예: {bad[g]}")
    print(f"전체 {sum(ok.values())}/{sum(tot.values())} = {sum(ok.values()) / sum(tot.values()):.1%}")

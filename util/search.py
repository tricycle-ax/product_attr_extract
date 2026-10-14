import requests
from requests.exceptions import HTTPError


# 상품api 에서 상품정보 추출
def getPrdListByKeyword(siteCd, keyword):

    domain = 'hapix.halfclub.com'

    if str(siteCd) == '2':  # 호출부는 int 로 넘김
        domain = 'apix.boribori.co.kr'

    url = f"https://{domain}/searches/prdList/"

    payload = {
        "keyword": keyword,
        "siteCd": siteCd,
        "device": "pc",
        "limit": "0,10",
        "sortSeq": "12"
    }

    try:
        response = requests.get(url, params=payload, timeout=5)
        response.raise_for_status()
        data = response.json()

        # result = getPrdInfoByJson(data)
        return data
    
    except HTTPError as http_err:
        print(f"HTTP error ocurred:, {http_err}")
    except Exception as err:
        print(f"Orther error occured: {err}")

# es 데이터
def process_es_hit_to_display(hit_item):
    """
    [수정됨] Elasticsearch 'hits' 구조에서 리스트 출력용 정보를 추출
    구조: hit -> _source -> {pcode, brandNm, appPrdImgUrl...}
    """
    # ES 데이터는 _source 안에 실제 정보가 있음
    source = hit_item.get('_source', {})
    
    # 1. 브랜드
    brand = source.get('brandNm', '브랜드 없음')
    
    # 2. 상품명 (prdNm이 없으면 appPrdNm 등 대체 필드 사용 고려)
    name = source.get('prdNm') or source.get('appPrdNm', '상품명 없음')
    
    # 3. 이미지 URL (ES 필드명: appPrdImgUrl)
    img_url = source.get('appPrdImgUrl')
    
    # 4. 상품 번호 (ES 필드명: pcode)
    prdNo = source.get('prdNo')

    return {
        "prdNo": prdNo,
        "brand": brand,
        "name": name,
        "img_url": img_url,
        "raw_data": source # 상세 분석을 위해 source 통째로 저장
    }

def map_es_to_internal_schema(es_source):
    """
    [신규] ES 데이터를 분석 함수(process_product_data_final)가 이해할 수 있는 
    기존 API 포맷으로 변환해주는 어댑터 함수
    """
    # 기존 로직이 기대하는 키 이름으로 매핑
    return {
        'prdNo': es_source.get('prdNo'),
        'prdNm': es_source.get('prdNm'),
        'brandNm': es_source.get('brandNm'),
        
        # 이미지는 객체 형태로 변환
        'appPrdImgUrl': {'basicExtNm': es_source.get('appPrdImgUrl')},
    }
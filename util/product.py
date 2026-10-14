import requests
import pandas as pd
import json
import ast
from util.image import encode_image_to_base64_chunk, extract_all_valid_images
from bs4 import BeautifulSoup
from requests.exceptions import HTTPError
from ai.model import call_ai_service

# 상품api 에서 상품정보 추출
def getProductInfo(prd_no):
    url = f"https://hapix.halfclub.com/product/products/withoutPrice/{prd_no}"

    payload = {
        "countryCd": "001",
        "langCd": "001",
        "siteCd": "1",
        "deviceCd": "001",
        "mandM": "halfclub"
    }

    try:
        response = requests.get(url, params=payload, timeout=5)
        response.raise_for_status()
        data = response.json()

        result = getPrdInfoByJson(data)
        return result
    
    except HTTPError as http_err:
        print(f"HTTP error ocurred:, {http_err}")
    except Exception as err:
        print(f"Orther error occured: {err}")

# json 데이터 정규화
def getPrdInfoByJson(data):
    """
    제공된 '네파' 상품 데이터 구조에 최적화된 추출 함수
    """
    
    # [1] 데이터 꺼내기 ('data' 키 안에 실제 정보가 있음)
    if isinstance(data, dict) and 'data' in data:
        raw_data = data['data']
    else:
        raw_data = data # 'data' 키가 없으면 그대로 사용

    # 데이터프레임 변환
    if isinstance(raw_data, dict):
        df = pd.DataFrame([raw_data])
    else:
        df = raw_data.copy()

    if df.empty:
        return "데이터 없음"

    # ==========================================================
    # [2] 안전한 파싱 (혹시 문자열로 되어있을 경우 대비)
    # ==========================================================
    def robust_parse(val):
        if isinstance(val, dict): return val
        if isinstance(val, list): return val # 리스트도 그대로 반환
        if pd.isna(val) or val == "": return {}
        try:
            return json.loads(val)
        except:
            try:
                return ast.literal_eval(val)
            except:
                return {}

    target_cols = ['productDesc', 'productImage', 'notiItemMap', 'optionItem', 'attributes', 'dispCtgr']
    for col in target_cols:
        if col in df.columns:
            df[col] = df[col].apply(robust_parse)
        else:
            df[col] = [{}] * len(df)

    # ==========================================================
    # [3] 데이터 추출 (여기가 핵심!)
    # ==========================================================
    result = pd.DataFrame()
    
    # 1. 기본 정보
    result['prdNo'] = df['prdNo']
    result['prdNm'] = df['prdNm']
    result['brandNm'] = df['brandMainNmKr']

    # 2. 상세설명 (HTML)
    # 키 이름: prdDescContClob
    result['prdDesc'] = df['productDesc'].apply(lambda x: x.get('prdDescContClob') if isinstance(x, dict) else None)

    # 3. 이미지 URL 
    # 키 이름: basicExtNm (주의: 앞에 도메인이 없을 수 있음)
    result['prdImg'] = df['productImage'].apply(extract_all_valid_images)
    
    # 4. 옵션 정보 (리스트 -> 보기 좋은 문자열로 변환)
    # 예: "색상: BLACK, SAND / 사이즈: 95, 100"
    def parse_options(opt_list):
        if not isinstance(opt_list, list): return ""
        summary = []
        for item in opt_list:
            name = item.get('optItemNm', '옵션')
            # 옵션 값들만 추출해서 중복 제거 후 합치기
            values = [v.get('optValueNm') for v in item.get('optValueList', [])]
            unique_vals = sorted(list(set(values))) # 중복제거
            summary.append(f"{name}: {', '.join(unique_vals)}")
        return " / ".join(summary)

    result['options'] = df['optionItem'].apply(parse_options)
    result['optionItem'] = df['optionItem']  # 사이즈표 검증(option_size_labels)용 원본

    # 5. 상품 고시 정보 (리스트 -> 딕셔너리로 변환)
    # 예: [{'title': '소재', 'value': '면'}] -> {'소재': '면'}
    def parse_notices(noti_list):
        if not isinstance(noti_list, list): return {}
        return {item.get('notiItemTitle'): item.get('notiItemValue') for item in noti_list}

    result['notices'] = df['notiItemMap'].apply(parse_notices)

    # 6. 전시카테고리
    result['category_L'] = df['dispCtgr'].apply(lambda x: x.get('dispCtgrNm1') if isinstance(x, dict) else None)
    result['category_M'] = df['dispCtgr'].apply(lambda x: x.get('dispCtgrNm2') if isinstance(x, dict) else None)
    result['category_S'] = df['dispCtgr'].apply(lambda x: x.get('dispCtgrNm3') if isinstance(x, dict) else None)

    return result

# 추론에 필요한 상품정보 정리
def format_product_metadata(rowData):
    """
    DataFrame의 한 행(row)에서 브랜드, 상품명, 고시정보, 옵션을 추출하여
    AI에게 전달할 텍스트 덩어리로 변환합니다.
    """

    if isinstance(rowData, pd.DataFrame):
        if rowData.empty:
            return "데이터 없음"
        else:
            row = rowData.iloc[0]

    #1. 브랜드 & 상품평
    meta_text = f"[기본 정보]\n"
    meta_text += f"- 브랜드: {row.get('brandNm', '정보없음')}\n"
    meta_text += f"- 상품명: {row.get('prdNm', '정보없음')}\n\n"

    # 2. 정보고시 (notiItemMap) - 리스트 형태 처리
    # 예: [{'notiItemTitle': '소재', 'notiItemValue': '면 100%'}]
    meta_text += f"[정보고시]\n"
    notices = row.get('notices') # 이전 단계에서 notices 컬럼으로 파싱

    if isinstance(notices, dict):
        for key, value in notices.items():
            meta_text += f"- {key}: {value}\n"
    elif isinstance(notices, list):
        for item in notices:
            title = item.get('notiItemTitle', '')
            val = item.get('notiItemValue', '')
            if title:
                meta_text += f"- {title}: {val}\n"
    else:
        meta_text += "(고시정보 없음)\n"
    
    meta_text += "\n"

    # 3. 옵션 정보 (optionItem)
    # 예: [{'optItemNm': '색상', 'optValueList': [...]}]
    meta_text += f"[구매 가능 옵션]\n"
    # 이전 단계에서 만든 'options' 문자열을 그대로 쓰거나, 원본을 다시 파싱
    raw_options = row.get('optionItem')

    if isinstance(raw_options, list):
        for opt in raw_options:
            opt_name = opt.get('optItemNm', '옵션')
            # 옵션 값 리스트 추출
            vals = []
            if 'optValueList' in opt:
                vals = [v.get('optValueNm') for v in opt['optValueList']]
                #중복 제거 및 정렬
                vals = sorted(list(set(vals)))

            val_str = ", ".join(vals)
            meta_text += f"- {opt_name}: {val_str}\n"
    else:
        meta_text += f"{row.get('options', '옵션 정보 없음')}\n"

    return meta_text

# 상품정보 기반 스타일, 속성, 카테고리 등 추론
def analyze_product_with_full_context(html_content, model_name="gemini-2.5-flash-lite", max_images=6, use_images=True, system_prompt=None):
    """
    이미지 + HTML설명 + 메타데이터(브랜드, 스펙, 옵션)를 모두 통합하여 분석
    """
    
    # 1. 메타데이터 텍스트 생성
    metadata_text = format_product_metadata(html_content)
    
    # 2. HTML 상세설명 (기존 로직)
    row = html_content.iloc[0]

    # 대표이미지(추가이미지 포함)
    basic_ext_nm = row.get('prdImg', '')
    # basic_ext_nm = f"https://cdn2.halfclub.com/rimg/330x440/contain/{basic_ext_nm}?format=webp"

    html_desc = row.get('prdDesc', '')
    if html_desc:
        soup = BeautifulSoup(html_desc, 'html.parser')
        # 텍스트 추출 (구조감을 살리기 위해 줄바꿈 유지)
        clean_desc = soup.get_text(separator="\n", strip=True)[:6000] # 컨텍스트 조금 더 확보
    else:
        clean_desc = "(상세설명 없음)"

    # 유저 메시지에 메타데이터와 상세설명을 구분해서 주입
    user_content = f"""
        다음은 상품에 대한 텍스트 데이터이다. 이 내용을 분석의 핵심 근거로 삼아라.
        
        {metadata_text}
        
        ----------------
        [상세 페이지 문구]
        {clean_desc}
        """
    
    ai_image_inputs = []
    used_image_urls = [] # ★ 실제로 사용된(Base64 변환 성공한) 이미지 URL 저장용

    if use_images:
        # 이미지 추가 (Base64 변환 로직은 기존과 동일하므로 함수 호출로 대체)
        # --- 2. 스마트 이미지 추출 및 필터링 ---
        # found_images = extract_img_for_html(soup, basic_ext_nm)
        found_images = basic_ext_nm

        # 외부 이미지 제한정책으로 인한 로컬 다운로드
        if found_images:
            valid_image_count = 0

            for img_url in found_images:
                # 최대 6장까지만 처리 (비용 및 속도 고려)
                if valid_image_count >= max_images:
                    break
                    
                # ★ 핵심: URL을 그냥 보내지 않고, Base64로 변환해서 보냄
                base64_image = encode_image_to_base64_chunk(img_url, model_name)
                
                if base64_image:
                    ai_image_inputs.extend(base64_image)    
                    used_image_urls.append(base64_image)
                    valid_image_count += 1
                    print(f"✅ 이미지 변환 성공: {img_url}")
                else:
                    print(f"❌ 이미지 변환 실패 (건너뜀): {img_url}")

    # --- 4. AI 호출 --- (예외는 app.py 의 except 에서 화면에 표시)
    response = call_ai_service(
        system_prompt=system_prompt,
        user_text=user_content,
        image_list=ai_image_inputs,
        model_name=model_name
    )
    if response is None:
        raise RuntimeError(f"{model_name} 응답이 없습니다 (API 오류 — 콘솔 로그 확인)")

    # (1. 결과, 2. 원본 URL들, 3. 크롭 이미지들, 4. 상세설명에서 추출한 text)
    return response, used_image_urls, ai_image_inputs, clean_desc
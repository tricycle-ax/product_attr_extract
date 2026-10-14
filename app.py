import streamlit as st
import json
from prompts.product import DEFAULT_SYSTEM_PROMPT
from util.product import getProductInfo, analyze_product_with_full_context
from ai.model import gateway_models
from util.search import getPrdListByKeyword, process_es_hit_to_display
from util.size_table import extract_size_table, option_size_labels, validate, measure_attrs, match_row, gender_first
from util.size_label import normalize_options, size_option_values, size_group, parse_size_label

# 사이즈표 모델: LiteLLM 게이트웨이 경유. secrets 의 SIZE_MODEL 로 교체 (게이트웨이 키 허용 모델이어야 함)
SIZE_MODEL = st.secrets.get("SIZE_MODEL", "gpt-6-luna")



# # --- 설정 ---
# # 실제 운영 시에는 환경 변수 등으로 관리하세요.
# OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
# client = openai.OpenAI(api_key=OPENAI_API_KEY)

# ==========================================
# Streamlit UI 메인
# ==========================================
def main():
    st.set_page_config(page_title="AI 상품 분석기", layout="wide")
    
    # ★ [수정 1] CSS 스타일 업데이트 (작은 카드에 맞게 폰트/버튼 축소)
    st.markdown("""
    <style>
    /* 전체 버튼 스타일 조정 */
    .stButton > button {
        width: 100%;
        padding: 0.25rem 0.5rem;  /* 패딩을 줄여서 버튼을 납작하게 */
        font-size: 12px;          /* 버튼 글씨 크기 축소 */
        height: auto;
        min_height: 0px;
    }
    img {
        border-radius: 6px;
        margin-bottom: 5px;
    }
    /* 카드 내 텍스트 줄 간격 및 크기 조정 */
    div[data-testid="stCaptionContainer"] {
        font-size: 11px;
        line-height: 1.2;
        margin-bottom: 0px;
    }
    div[data-testid="stText"] {
        font-size: 12px;
        font-weight: bold;
        line-height: 1.3;
        white-space: nowrap;      /* 한 줄로 표시 */
        overflow: hidden;         /* 넘치면 숨김 */
        text-overflow: ellipsis;  /* ... 처리 */
    }
    </style>
    """, unsafe_allow_html=True)

    # ==========================================
    # [수정 1] 사이드바: AI 모델 선택 기능 (Gemini Flash 추가)
    # ==========================================
    with st.sidebar:
        st.header("🛠️ 설정")
        st.markdown("---")
        # 게이트웨이 키로 사용 가능한 모델만 선택지로 (Nova·Gemini 직접 호출은 이 환경에서 불가: VPC 전용/키 무효)
        models = gateway_models() or ["gpt-4o-mini"]
        if not gateway_models():
            st.warning("게이트웨이 모델 목록을 가져오지 못해 기본 모델만 표시합니다.")
        selected_sidebar_model = st.radio(
            "상품 분석 모델", models, index=models.index("gpt-4o-mini") if "gpt-4o-mini" in models else 0,
            help="LiteLLM 게이트웨이(company 팀 키)에서 허용된 모델")
        size_model = st.selectbox(
            "사이즈표 모델", models, index=models.index(SIZE_MODEL) if SIZE_MODEL in models else 0,
            help="상세 HTML/이미지에서 사이즈표를 추출할 때 사용")
        st.info(f"분석: **{selected_sidebar_model}** · 사이즈표: **{size_model}**")

        st.markdown("---")
        # ★ [추가] 이미지 분석 포함 여부 토글
        use_image_analysis = st.toggle("📸 이미지 포함하여 분석", value=True)
        
        if use_image_analysis:
            st.caption("✅ 텍스트 + 이미지 모두 분석합니다.")
        else:
            st.caption("⚡ 텍스트만 빠르게 분석합니다. (이미지 제외)")

    # 메인 타이틀
    st.title("🛍️ 이커머스 상품 정보 AI 분석기")
    
    # 세션 상태 초기화
    if "search_results" not in st.session_state: st.session_state.search_results = []
    if "selected_product" not in st.session_state: st.session_state.selected_product = None
    if "ai_result" not in st.session_state: st.session_state.ai_result = None

    # [설정] 세션 상태 초기화
    if "is_analyzing" not in st.session_state: st.session_state.is_analyzing = False
    if "analyzing_product_name" not in st.session_state: st.session_state.analyzing_product_name = ""
    # 초기 모델값을 사이드바 선택값으로 설정
    if "current_model" not in st.session_state: st.session_state.current_model = selected_sidebar_model

    # ----------------------------------------------
    # Step 1: 검색 (Mock Data for ES)
    # ----------------------------------------------
    st.subheader("1. 상품 검색")
    with st.form(key="search_form"):
        col1, col2 = st.columns([4, 1], vertical_alignment="bottom")
        with col1:
            search_query = st.text_input("검색어 입력", "원피스")
        with col2:
            search_btn = st.form_submit_button("검색", type="primary", width="stretch")

    if search_btn:
        # ES 검색 결과 가져오기
        es_response = getPrdListByKeyword(1, search_query)
        
        # 실제 데이터 바인딩 (API 실패 시 None → 빈 결과)
        try:
            hits = es_response['data']['result']['hits']['hits']
        except (TypeError, KeyError):
            hits = []
            st.error("❌ 상품 검색에 실패했습니다. 잠시 후 다시 시도해주세요.")
        st.session_state.search_results = [process_es_hit_to_display(hit) for hit in hits]
        st.session_state.selected_product = None
        st.session_state.ai_result = None
        st.session_state.is_analyzing = False

    # =========================================================
    # ★ [위치 이동] 프롬프트 설정 영역 (검색 결과 바로 아래)
    # =========================================================
    # 검색 결과가 있을 때만 보여주거나, 항상 보여줄 수 있습니다. 
    # 여기서는 항상 보이도록 설정했지만, if search_results: 안으로 넣으셔도 됩니다.
    
    st.markdown("### 📝 분석 프롬프트 설정 (Advanced)")

    # 세션 상태에 값이 없을 때만 기본값으로 초기화 (강제 덮어쓰기 방지)
    if "system_prompt_input" not in st.session_state:
        st.session_state["system_prompt_input"] = DEFAULT_SYSTEM_PROMPT
    
    with st.expander("프롬프트 수정하기 (클릭하여 열기/닫기)", expanded=True):
        st.info("💡 상품 분석 시 AI에게 적용될 지침입니다. 내용을 수정하면 다음 분석부터 바로 적용됩니다.")

        # st.text_area의 key가 중요합니다.
        st.text_area(
            label="시스템 프롬프트 내용",
            value=DEFAULT_SYSTEM_PROMPT,
            height=300,
            key="system_prompt_input", # 이 key가 세션 저장소의 이름이 됩니다.
            help="AI의 페르소나와 분석 규칙을 정의합니다."
        )    

    # ----------------------------------------------
    # Step 2: 리스트 출력 (5개씩 그리드 + 선택 버튼)
    # ----------------------------------------------
    search_results = st.session_state.search_results

    if search_results:
        st.divider()
        st.subheader(f"2. 검색 결과 ({len(search_results)}건)")
        
        # 5개씩 끊어서 행 만들기
        cols_per_row = 10
        for i in range(0, len(search_results), cols_per_row):
            cols = st.columns(cols_per_row)
            
            # 각 Row 안에서 Column 채우기
            for j in range(cols_per_row):
                if i + j < len(search_results):
                    item = search_results[i + j]
                    with cols[j]:
                        # 이미지 링크 처리
                        if item['img_url']:
                            link_url = f"https://www.halfclub.com/product/{item['prdNo']}"
                            st.markdown(
                                f"""<a href="{link_url}" target="_blank"><img src="{item['img_url']}" style="width:100%; border-radius:8px; cursor:pointer;"></a>""",
                                unsafe_allow_html=True
                            )
                        else:
                            st.write("No Image")
                        
                        # 상품 정보 (브랜드/상품명)
                        st.caption(item['brand'])
                        short_name = (item['name'][:20] + '..') if len(item['name']) > 20 else item['name']
                        st.text(short_name)
                        
                        # ==========================================
                        # [수정 2] 버튼 통합
                        # ==========================================
                        analyze_btn = st.button("✨ 분석", key=f"btn_analyze_{item['prdNo']}", type="secondary", width="stretch")

                        if analyze_btn:
                            # 1. 데이터 준비
                            processed_df = getProductInfo(item['prdNo'])
                            row = processed_df

                            st.session_state.selected_product = row

                            # [핵심] 사이드바에서 선택한 모델명을 세션에 저장
                            # (gpt-4o, gpt-4o-mini, gemini-1.5-flash 중 하나)
                            st.session_state.current_model = selected_sidebar_model
                            st.session_state.analyzing_product_name = item['name']
                            st.session_state.is_analyzing = True
                            
                            # 3. 화면 갱신
                            st.rerun()

                        # 상품 분석 없이 사이즈표만 추출
                        if st.button("📏 사이즈표", key=f"btn_sizeonly_{item['prdNo']}", type="secondary", width="stretch"):
                            st.session_state.selected_product = getProductInfo(item['prdNo'])
                            st.session_state.ai_result = None
                            st.session_state.is_analyzing = False
                            st.session_state.size_request = item['prdNo']
                            st.rerun()
                    
        # ----------------------------------------------
        # [중간] 분석 로딩 및 실행 영역
        # ----------------------------------------------
        if st.session_state.is_analyzing and st.session_state.selected_product is not None:
            st.divider()
            target_name = st.session_state.analyzing_product_name
            model_name = st.session_state.current_model # 저장된 모델명 사용
            
            error_placeholder = st.empty()
            
            # 스피너 문구에도 모델명 표시
            with st.spinner(f"🤖 AI({model_name})가 '{target_name}' 상품을 정밀 분석 중입니다..."):
                try:
                    row = st.session_state.selected_product

                    # [핵심] UI에서 입력된 최신 값을 세션 상태에서 직접 가져옵니다.
                    # 만약 입력창이 비어있을 경우를 대비해 get()과 기본값을 사용합니다.
                    current_final_prompt = st.session_state.get("system_prompt_input", DEFAULT_SYSTEM_PROMPT)
                    
                    # 선택된 모델로 분석 실행
                    # 주의: analyze_product_with_full_context 함수 내부에서
                    # gemini-2.5-flash 모델명을 처리할 수 있어야 합니다.
                    result, used_images, ai_chunks, clean_desc = analyze_product_with_full_context(
                        row, 
                        model_name=model_name,
                        use_images = use_image_analysis,
                        system_prompt=current_final_prompt
                    )
                    # 결과 출력 및 확인을 위해 UI에 표시 (선택 사항)
                    st.write("사용된 프롬프트 확인용:", current_final_prompt[:50] + "...")
                    st.session_state.ai_result = result

                    # ★ [추가] 사용된 이미지 리스트도 세션에 저장
                    st.session_state.analyzed_images = used_images

                    # ★ 크롭된 이미지 조각들 저장
                    st.session_state.ai_chunks = ai_chunks  

                    # ★ 상세설명 html에서 추출해낸 텍스트
                    st.session_state.clean_desc = clean_desc  
                    
                except Exception as e:
                    st.session_state.ai_result = None
                    error_placeholder.error(f"❌ 분석 중 오류가 발생했습니다: {e}")
                finally:
                    # 분석 상태 종료
                    st.session_state.is_analyzing = False
                    
                    # ★ [핵심 수정] 
                    # 결과가 있을 때만(성공 시) 재실행하여 결과를 보여줌.
                    # 실패 시에는 재실행하지 않아서 에러 메시지가 화면에 유지됨.
                    if st.session_state.ai_result is not None:
                        st.rerun()

    # ----------------------------------------------
    # Step 3: 상세 정보 및 분석 결과
    # ----------------------------------------------
    if st.session_state.selected_product is not None:
        row = st.session_state.selected_product
        
        st.divider()
        st.subheader("3. 상품 분석 결과")
        
        # [상단 레이아웃] 좌: 이미지 / 우: 텍스트 정보 + 디버그
        c_left, c_right = st.columns([1, 2], gap="medium")
        
        # === 왼쪽 컬럼: 상품 이미지 ===
        with c_left:
            st.markdown("#### 🖼️ 상품 이미지")
            # 1. 데이터 가져오기 (이제 리스트 형태임)
            img_data = row.iloc[0]['prdImg']
            target_url = None

            # 2. 데이터 타입에 따라 URL 추출
            if isinstance(img_data, list):
                # 리스트라면 첫 번째 요소(basicExtNm)가 대표 이미지
                if len(img_data) > 0:
                    target_url = img_data[0]
            elif isinstance(img_data, str):
                # (혹시 모를 하위 호환) 문자열이면 그대로 사용
                target_url = img_data

            # 3. 이미지 출력
            if target_url:
                try:
                    # URL이 http로 시작하지 않으면 도메인 붙이기 (기존 로직 유지)
                    if not target_url.startswith("http"):
                        target_url = f"https://cdn2.halfclub.com/{target_url.lstrip('/')}"
                    
                    st.image(target_url, caption="대표 이미지", width=350)
                except:
                    st.error("이미지 로드 실패")
            else:
                st.write("이미지 없음")

        # === 오른쪽 컬럼: 텍스트 정보 + 디버그 정보 ===
        with c_right:
            # 1. 텍스트 정보
            st.markdown("#### 📝 상품 정보")
            st.markdown(f"**상품명:** {row.iloc[0]['prdNm']}")
            st.markdown(f"**브랜드:** {row.iloc[0]['brandNm']}")
            opt_txt = row.iloc[0]['options'] if row.iloc[0]['options'] != "옵션 정보 없음" else "정보 없음 (ES 데이터)"
            st.markdown(f"**옵션:** {opt_txt}")

            # 옵션 사이즈 라벨 정규화 (규칙 기반, LLM 미사용)
            p0 = row.iloc[0]
            norm = normalize_options(size_option_values(p0['optionItem']),
                                     [p0.get('category_L'), p0.get('category_M'), p0.get('category_S')], p0['prdNm'])
            # 이 상품의 환산표를 추출했으면 그 값(성별 맞는 그룹 우선)으로 병기·국가 채움 — 공통 조견표보다 우선
            sr = st.session_state.get("size_result")
            if norm and sr and str(sr["prdNo"]) == str(p0['prdNo']) and sr["table"] and sr["table"].kind == "conversion":
                conv = gender_first(sr["table"].rows, norm[0]["gender"])
                for n in norm:
                    hit = match_row(n["raw"], conv)
                    if hit:
                        n["systems"] = {**{(s.system if s.system != "기타" else s.raw_name): s.value for s in hit.systems}, **n["systems"]}
            if norm:
                with st.expander(f"📐 옵션 사이즈 정규화 — 규칙 기반, LLM 미사용 ({norm[0]['group']} · {norm[0]['gender']})", expanded=True):
                    tbl = [{"원본": n["raw"], "표기": n["display"], "기준값": n["value"], "종류": n["kind"],
                            "병기·국가": ", ".join(f"{k} {v}" for k, v in n["systems"].items()) or None,
                            "기장": n["length_variant"], "발볼": n["width"],
                            "연령": f"{n['age_range'][0]}~{n['age_range'][1]}세" if n["age_range"] else None}
                           for n in sorted(norm, key=lambda n: (n["value"] is None, n["value"] or 0))]
                    empty = [k for k in tbl[0] if all(t[k] is None for t in tbl)]   # 전부 빈 열은 숨김
                    st.dataframe([{k: v for k, v in t.items() if k not in empty} for t in tbl], hide_index=True)
            
            # 2. 디버그 정보 (접이식)
            st.write("") 
            with st.expander("🔍 분석용 데이터 원본 (JSON Code)"):
                try:
                    debug_data = row.iloc[0].to_dict()
                    json_str = json.dumps(debug_data, indent=2, ensure_ascii=False)
                    st.code(json_str, language="json")
                except:
                    st.write("데이터 변환 오류")

            # 3. [추가] AI가 생성한 최종 JSON 결과물
            if st.session_state.ai_result:
                with st.expander("✨ AI 분석 최종 JSON 결과", expanded=True): # 바로 보이게 expanded=True 권장
                    res = st.session_state.ai_result
                    
                    # Pydantic 모델인 경우 dict 변환, 아니면 그대로 사용
                    json_res = res.model_dump() if hasattr(res, 'dict') else res
                    
                    st.json(json_res) 
                    st.caption("AI가 프롬프트 지침에 따라 생성한 최종 구조화 데이터입니다.")


            # ★ [추가] 4. 분석에 사용된 이미지 갤러리
            # if "analyzed_images" in st.session_state and st.session_state.analyzed_images:
            #     with st.expander(f"📸 분석 이미지 전체 보기 ({len(st.session_state.analyzed_images)}장)"):
            #         st.write("")
            #         st.markdown("#### 📸 분석에 사용된 이미지")
                    
            #         img_cols = st.columns(3)
            #         for idx, img_data in enumerate(st.session_state.analyzed_images):
            #             with img_cols[idx % 3]:
            #                 # ★ [핵심 수정] 여기가 오류 원인입니다.
            #                 # img_data가 문자열이 아니라 ['url1', 'url2'...] 리스트로 들어오고 있습니다.
            #                 target_img = img_data
                            
            #                 if isinstance(target_img, list):
            #                     # 리스트라면 첫 번째 이미지만 꺼냅니다.
            #                     target_img = target_img[0] if len(target_img) > 0 else None
                            
            #                 # 이미지가 유효할 때만 출력
            #                 if target_img:
            #                     st.image(target_img, caption=f"이미지 {idx+1}", width="content")        


            # 5. 실제 AI가 분석한 이미지 
            # ai_chunks가 있으면(이미지 분석 옵션을 켰으면) 이걸 우선 보여줍니다.
            if "ai_chunks" in st.session_state and st.session_state.ai_chunks:
                with st.expander(f"🧩 실제 AI가 본 이미지 ({len(st.session_state.ai_chunks)}장)"):
                    st.info("💡 긴 상세페이지는 AI가 인식하기 좋게 자동으로 잘라서(Chunking) 전송됩니다.")
                    
                    # 3열 그리드로 예쁘게 출력
                    cols = st.columns(3)
                    for idx, b64_img in enumerate(st.session_state.ai_chunks):
                        with cols[idx % 3]:
                            # 리스트인지 확인하여 방어 코드 추가
                            if isinstance(b64_img, list):
                                # 만약 리스트라면 첫 번째 것만 가져오거나 무시
                                if b64_img: b64_img = b64_img[0]
                                else: continue
                            
                            st.image(b64_img, caption=f"image #{idx+1}", width="content")

            
            # 6. 상세설명 HTML에서 뽑아낸 텍스트
            if "clean_desc" in st.session_state and st.session_state.clean_desc:
                with st.expander(f"🧩 상세설명 HTML에서 뽑아낸 텍스트"):
                    st.code(st.session_state.clean_desc)
            
            
        
        # ---------------------------------------------------------
        # [사이즈표] 상세설명 HTML 표 → 이미지 순으로 추출
        # ---------------------------------------------------------
        st.divider()
        st.markdown("#### 📏 사이즈표")
        prd_no = row.iloc[0]['prdNo']
        # 카드의 "📏 사이즈표" 버튼으로 들어온 경우 자동 실행
        if st.button("📏 사이즈표 추출", key=f"btn_size_{prd_no}") or str(st.session_state.pop("size_request", None)) == str(prd_no):
            with st.spinner(f"사이즈표 추출 중입니다 ({size_model})..."):
                try:
                    table, meta = extract_size_table(row.iloc[0]['prdDesc'], st.secrets["AI_GATEWAY_KEY"], size_model,
                                                     base_url=st.secrets["AI_GATEWAY_URL"])
                    labels = option_size_labels({"optionItem": row.iloc[0]['optionItem']})
                    st.session_state.size_result = {"prdNo": prd_no, "table": table, "meta": meta,
                                                    "labels": labels, "issues": validate(table, labels)}
                except Exception as e:
                    st.session_state.size_result = None
                    st.error(f"❌ 사이즈표 추출 중 오류가 발생했습니다: {e}")
            if st.session_state.get("size_result"):
                st.rerun()   # 위쪽 '정규화 사이즈'에 환산값을 반영하려고 한 번 다시 그림 (재추출 없음)

        size_res = st.session_state.get("size_result")
        if size_res and size_res["prdNo"] == prd_no:
            table, meta = size_res["table"], size_res["meta"]
            st.caption(f"출처: `{meta.get('source')}` · 종류: `{table.kind if table else None}` · "
                       f"단위: `{table.unit if table else '-'}` · {meta.get('sec')}초 · "
                       f"토큰 {meta.get('in_tok')}/{meta.get('out_tok')} · 옵션 라벨: {', '.join(size_res['labels']) or '없음'}")
            if table and table.found and table.rows:
                p0 = row.iloc[0]
                group, gender = size_group([p0.get('category_L'), p0.get('category_M'), p0.get('category_S')], p0['prdNm'])
                opts = normalize_options(size_option_values(p0['optionItem']),
                                         [p0.get('category_L'), p0.get('category_M'), p0.get('category_S')], p0['prdNm'])
                # 옵션별로 가장 맞는 행 하나만 판매 표시 (남녀 표가 나뉜 환산표는 상품 성별 그룹 우선)
                sold_rows = {id(match_row(o["raw"], gender_first(table.rows, gender))) for o in opts}
                sold = lambda r: "✓" if id(r) in sold_rows else ""
                if table.kind == "conversion":   # 국가별 환산표: 체계별 열, 상품 성별 그룹 먼저
                    grid = [{"그룹": r.group, "사이즈": r.label, "판매 옵션": sold(r),
                             **{(s.system if s.system != "기타" else s.raw_name): s.value for s in r.systems}} for r in gender_first(table.rows, gender)]
                else:
                    grid = [{"그룹": r.group, "사이즈": r.label,
                             **{(m.std_name if m.std_name != "기타" else m.raw_name) + (" (단면)" if m.is_half else ""): m.value
                                for m in r.measures}} for r in table.rows]
                if not any(g["그룹"] for g in grid):
                    for g in grid: g.pop("그룹")
                st.dataframe(grid, hide_index=True)
                # 필터용 실측값: 둘레 기준 cm + 정규화된 옵션 사이즈 연결
                attrs = measure_attrs(table)
                if attrs:
                    rows_out = []
                    for a, r in zip(attrs, table.rows):   # measure_attrs 는 table.rows 순서 그대로
                        n = parse_size_label(a["label"], group, gender)   # 표 라벨도 같은 규칙으로 정규화
                        rows_out.append({**({"그룹": a["group"]} if a["group"] else {}), "사이즈": a["label"],
                                         "정규화": n["display"] if n["kind"] != "unknown" else None,
                                         "판매 옵션": sold(r),
                                         **{k: v for k, v in a.items() if k not in ("group", "label")}})
                    with st.expander("🔎 필터용 실측값 (cm, 둘레 기준 — 단면은 ×2)"):
                        st.dataframe(rows_out, hide_index=True)
            for issue in size_res["issues"]:
                st.warning(issue)
            if not size_res["issues"]:
                st.success("검증 통과")
            # 추출에 사용된 입력: 이미지 조각(모델이 표를 봤다고 답한 조각 강조) / HTML 텍스트
            slices = meta.get("slices") or []
            found_idx = [i for i in (table.source_slices if table and meta.get("source") == "image" else []) if 0 <= i < len(slices)]
            if found_idx:
                st.markdown("**🖼️ 사이즈표가 있던 이미지**")
                st.image([slices[i][1] for i in found_idx], caption=[f"조각 {i}" for i in found_idx], width=400)
            elif meta.get("source") == "html":
                st.caption("HTML 표 텍스트에서 추출했습니다 (이미지 미사용).")
            if slices:
                urls = list(dict.fromkeys(u for u, _ in slices))
                with st.expander(f"🧩 AI에 보낸 이미지 조각 전체 ({len(slices)}장 · 원본 이미지 {len(urls)}장)"):
                    st.image([b for _, b in slices], width=180,
                             caption=[f"조각 {i}" + (" ✅" if i in found_idx else "") for i in range(len(slices))])
                    st.markdown("**원본 이미지**\n" + "\n".join(f"- [{u.rsplit('/', 1)[-1]}]({u})" for u in urls))
            if meta.get("html_text"):
                with st.expander(f"📄 AI에 보낸 HTML 텍스트 ({meta.get('html_chars')}자)"):
                    st.code(meta["html_text"])
            with st.expander("사이즈표 JSON"):
                st.json(table.model_dump() if table else {})

        # ---------------------------------------------------------
        # [하단 레이아웃] AI 분석 리포트
        # ---------------------------------------------------------
        st.divider()

        # 사용된 모델명을 타이틀에 표시
        used_model = st.session_state.get('current_model', 'Unknown')
        st.markdown(f"#### 🤖 AI 분석 리포트 (Model: `{used_model}`)")
        
        if st.session_state.ai_result:
            res = st.session_state.ai_result
            
            # 탭으로 결과 보여주기
            tab1, tab2 = st.tabs(["📋 스펙 분석", "✍️ Description"])
            
            with tab1:
                st.success(f"AI({used_model})가 분석한 스타일 및 속성입니다.")
                try:
                    # 카테고리 등 속성 정보 그리드 배치
                    col_a, col_b, col_c = st.columns(3)
                    with col_a:
                        try:
                            st.write(f"**카테고리:**\n{res.ai_category_L} > {res.ai_category_M} > {res.ai_category_S}")
                        except:
                            st.write(f"**카테고리:**\n{getattr(res, 'ai_category_L', '')} > {getattr(res, 'ai_category_M', '')} > {getattr(res, 'ai_category_S', '')}")
                        st.write(f"**성별:**\n{res.ai_gender}")
                        st.write(f"**상의기장:**\n{res.ai_top_length}")
                        st.write(f"**소매기장:**\n{res.ai_sleeve_length}")
                    
                    with col_b:
                        st.write(f"**계절:**\n{', '.join(res.ai_season)}")
                        st.write(f"**스타일:**\n{', '.join(res.ai_style)}")
                        st.write(f"**바지기장:**\n{res.ai_pants_length}")
                        st.write(f"**두께감:**\n{res.ai_thickness}")
                    
                    with col_c:
                        st.write(f"**핏:**\n{res.ai_fit}")
                        st.write(f"**패턴:**\n{res.ai_pattern}")
                        st.write(f"**치마기장:**\n{res.ai_skirt_length}")
                        st.write(f"**신축성:**\n{res.ai_stretch}")
                except:
                    st.error("결과 표시 중 오류가 발생했습니다.")
            
            with tab2:
                st.info(f"{used_model} 모델이 작성한 상품 소개 문구입니다.")
                st.markdown(f"> {res.description}")
        else:
            # 아직 분석 결과가 없을 때
            st.info("상품 분석 결과가 없습니다. 검색 결과에서 ✨ 분석 버튼을 누르면 분석합니다.")

if __name__ == "__main__":
    main()
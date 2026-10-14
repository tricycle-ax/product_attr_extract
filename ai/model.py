import json
import streamlit as st
from openai import OpenAI
from ai import gpt, gemini, qwen, nova_lite
from schema.product import ProductSchema


@st.cache_data(ttl=600, show_spinner=False)
def gateway_models():
    """LiteLLM 게이트웨이 키로 호출 가능한 모델 목록 (10분 캐시, 조회 실패 시 빈 목록)"""
    try:
        gw = OpenAI(base_url=st.secrets["AI_GATEWAY_URL"], api_key=st.secrets["AI_GATEWAY_KEY"])
        return sorted(m.id for m in gw.models.list())
    except Exception as e:
        print(f"게이트웨이 모델 조회 실패: {e}")
        return []


def _to_product_schema(res):
    """Nova 는 dict 또는 (코드펜스 포함) 문자열을 돌려줌 → 다른 모델과 같은 ProductSchema 로 통일"""
    if res is None or isinstance(res, ProductSchema):
        return res
    if isinstance(res, str):
        res = json.loads(res.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
    return ProductSchema.model_validate(res)

# ==========================================
# [1] AI 통신 전담 함수 (핵심 변경 부분)
# ==========================================
def call_ai_service(system_prompt, user_text, image_list, model_name):
    """
    모델 이름에 따라 적절한 AI 서비스를 호출하고, 결과를 ProductSchema 형태로 반환합니다.
    """
    
    # 0. 게이트웨이에 등록된 모델이면 이름과 무관하게 게이트웨이로 (예: 게이트웨이의 gemini/* 별칭)
    if model_name in gateway_models():
        client = OpenAI(base_url=st.secrets["AI_GATEWAY_URL"], api_key=st.secrets["AI_GATEWAY_KEY"])
        return gpt._call_openai_native(system_prompt, user_text, image_list, model_name, client)

    # 1. Google Gemini (Flash, Pro 등)
    if "gemini" in model_name.lower():
        api_key = st.secrets["GOOGLE_API_KEY_LSS"]
        return gemini._call_gemini_api(system_prompt, user_text, image_list, model_name, api_key)
    
    # 2. Qwen (OpenAI 호환 API 사용 권장) 또는 기타 OpenAI 호환 모델
    elif "qwen" in model_name.lower():
        # Qwen용 클라이언트가 별도로 없으면 기존 client 사용하거나 새로 생성
        api_key = st.secrets["DASHSCOPE_API_KEY"]
        client = OpenAI(base_url=st.secrets["DASHSCOPE_API_URL"], api_key=api_key)
        return qwen._call_openai_compatible(system_prompt, user_text, image_list, model_name, client)
    
    # 3. aws nova lite
    elif "aws" in model_name.lower():        
        token = st.secrets["AWS_BEARER_TOKEN_BEDROCK"]
        schema = json.dumps(ProductSchema.model_json_schema(), ensure_ascii=False)
        return _to_product_schema(nova_lite._call_nova_lite_api(system_prompt, user_text, image_list, model_name, token, schema))
    
    # 3. 기본 OpenAI — 사내 LiteLLM 게이트웨이 경유 (company 팀 키, 허용 모델은 게이트웨이 키 설정을 따름)
    else:
        client = OpenAI(base_url=st.secrets["AI_GATEWAY_URL"], api_key=st.secrets["AI_GATEWAY_KEY"])
        return gpt._call_openai_native(system_prompt, user_text, image_list, model_name, client)






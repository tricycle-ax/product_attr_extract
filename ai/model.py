import streamlit as st
from openai import OpenAI
from ai import gpt, gemini, qwen, nova_lite

# ==========================================
# [1] AI 통신 전담 함수 (핵심 변경 부분)
# ==========================================
def call_ai_service(system_prompt, user_text, image_list, model_name):
    """
    모델 이름에 따라 적절한 AI 서비스를 호출하고, 결과를 ProductSchema 형태로 반환합니다.
    """
    
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
        return nova_lite._call_nova_lite_api(system_prompt, user_text, image_list, model_name, token)
    
    # 3. 기본 OpenAI (GPT-4o 등)
    else:
        # ★ [수정] Client가 없으면 api_key로 생성하는 로직 추가
        api_key = st.secrets["OPENAI_API_KEY"]
        client = OpenAI(api_key=api_key)
        return gpt._call_openai_native(system_prompt, user_text, image_list, model_name, client)






from schema.product import ProductSchema # 사용자가 정의한 스키마

# --- [내부 함수 2] OpenAI Native 호출 로직 (Structured Output 사용) ---
def _call_openai_native(system_prompt, user_text, image_list, model_name, client):
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": []}
        ]
        messages[1]["content"].append({"type": "text", "text": user_text})

        for img_data in image_list:
            if isinstance(img_data, str):
                messages[1]["content"].append({
                    "type": "image_url",
                    "image_url": {"url": img_data, "detail": "low"}
                })

        response = client.beta.chat.completions.parse(
            model=model_name, 
            messages=messages,
            response_format=ProductSchema, # 사용자가 정의한 Pydantic 모델
            # gpt-5/6 계열(추론 모델)은 temperature 고정값만 허용 → gpt-4 계열에만 지정
            **({"temperature": 0.2} if model_name.startswith(("gpt-4", "gpt-3")) else {})
        )
        
        product_data = response.choices[0].message.parsed

        return product_data
            
    except Exception as e:
        # ★ [수정] 화면에 에러 출력
        st.error(f"❌ OpenAI(GPT) 호출 오류 상세: {str(e)}")
        return None
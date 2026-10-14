import boto3
import json
import os
import base64
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError

def _call_nova_lite_api(system_prompt, user_text, image_list, model_name, token, schema_structure=None):
    """
    Gemini 코드를 AWS Nova Lite로 변환한 함수
    """       
    if not token:
        raise ValueError("AWS_BEARER_TOKEN_BEDROCK 환경 변수를 설정하세요")
    
    # Bearer Token 추가 함수
    def add_bearer_auth(request, **kwargs):
        request.headers['Authorization'] = f'Bearer {token}'

    # 서명 없이 클라이언트 생성 (Bearer Token만 사용)
    config = Config(signature_version=UNSIGNED)

    # 1. 메시지 콘텐츠 구성 (멀티모달)
    content_parts = [{"text": user_text}]    
    for img_data in image_list:
        if isinstance(img_data, str) and "base64," in img_data:
            try:
                # Base64 헤더와 데이터 분리
                header, encoded = img_data.split("base64,", 1)
                
                # MIME 타입 추출 (예: image/jpeg -> jpeg)
                # Bedrock은 'jpeg', 'png', 'gif', 'webp' 형식을 지원합니다.
                original_mime = header.split(":")[1].split(";")[0]
                format_type = original_mime.split("/")[-1]
                
                # jpg는 jpeg로 변환 필요
                if format_type == "jpg": format_type = "jpeg"
                
                # 디코딩
                img_bytes = base64.b64decode(encoded)
                
                # Bedrock 이미지 구조로 추가
                image_block = {
                    "image": {
                        "format": format_type,
                        "source": {"bytes": img_bytes}
                    }
                }
                content_parts.append(image_block)
                
            except Exception as e:
                print(f"이미지 처리 중 오류 발생: {e}")
                continue

    client = boto3.client(
        service_name="bedrock-runtime",
        region_name="ap-northeast-2",
        endpoint_url="https://vpce-028d9b92686cc88ea-36e83gj6.bedrock-runtime.ap-northeast-2.vpce.amazonaws.com",
        config=config
    )

    # before-sign 이벤트 등록
    client.meta.events.register('before-sign', add_bearer_auth)
    #########################################위에 내용 변경 금지######################################################

    # 3. 메시지 구조 생성
    messages = [
        {
            "role": "user",
            "content": content_parts
        }
    ]

    # 4. 시스템 프롬프트 및 설정 구성
    # Gemini의 response_schema 대신 시스템 프롬프트에 JSON 구조를 강제하는 지시어를 추가합니다.
    final_system_prompts = [{"text": system_prompt}]
    
    if schema_structure:
         # Pydantic 스키마 대신 원하는 JSON 형태를 텍스트로 설명해주는 것이 Nova에서는 더 효과적일 수 있습니다.
        final_system_prompts.append({"text": f"반드시 다음 JSON 구조로 응답하세요: {schema_structure}"})

    # Generation Config 대응
    inference_config = {
        "temperature": 0.1,  # 기존 코드와 동일
        # "maxTokens": 2048,   # 필요에 따라 조절
        "topP": 0.9,
    }

    # 5. API 호출 (converse)
    try:
        response = client.converse(
            modelId="apac.amazon.nova-lite-v1:0",
            messages=messages,
            system=final_system_prompts,
            inferenceConfig=inference_config
        )

        print(response)
        
        # 응답 파싱
        output_message = response["output"]["message"]["content"][0]["text"]
        
        # JSON 형식으로 파싱 시도 (Gemini의 application/json 대응)
        try:
            return json.loads(output_message)
        except json.JSONDecodeError:
            return output_message

    except ClientError as e:
        print(f"API 호출 에러: {e}")
        return None
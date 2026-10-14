from pydantic import BaseModel, Field
from typing import List, Optional, Literal

# --- 1. 데이터 구조 정의 (Pydantic Schema) ---
# description 필드의 설명을 강화했습니다.
class ProductSchema(BaseModel):
    # 텍스트와 이미지 분석 결과가 결합된 설명
    description: str = Field(description="HTML 텍스트의 정보와 이미지의 시각적 특징(색상, 재질, 분위기, 디자인 디테일)을 종합적으로 반영한 상세한 상품 설명, 고객을 위한 매력적인 상품 마케팅 문구 (500자 이내 요약)")
    
    # 주요 속성
    prdNo: Optional[str] = Field(..., description="상품번호")
    prdNm: Optional[str] = Field(..., description="상품명")
    brandNm: Optional[str] = Field(..., description="브랜드명")
    ai_category_L: str = Field(..., description="상품의 대분류 카테고리 (예: 여성, 남성, 유니섹스, 언더웨어, 골프, 스포츠, 아웃도어 등)")
    ai_category_M: str = Field(..., description="상품의 중분류 카테고리 (예: 원피스, 가디건, 셔츠, 팬츠 등)")
    ai_category_S: str = Field(..., description="상품의 소분류 카테고리 (예: 트렌치, 무스탕, 바람막이, 여성골프화, 러닝화 등)")
    ai_gender: str = Field(..., description="추천 성별 (남성, 여성, 남녀공용, 키즈 중 택1)")
    ai_season: List[str] = Field(..., description="착용하기 좋은 계절 (봄, 여름, 가을, 겨울, 사계절 중 복수 선택 가능)")
    ai_style: List[str] = Field(..., description="스타일 키워드 (미니멀, 클래식, 캐주얼, 스트리트 등 분석된 스타일 모두 나열)")
    ai_pattern: str = Field(..., description="패턴 정보 (무지, 스트라이프, 체크, 로고, 그래픽 등)")
    # 필터용 보조속성: 값 목록 고정 (프롬프트 [상의]/[바지]/[치마] 분류와 동일)
    ai_fit: Literal["슬림", "레귤러", "오버", "루즈"] = Field(..., description="핏. 정보가 없으면 레귤러")
    ai_size: str = Field(..., description="사이즈")
    ai_top_length: Optional[Literal["숏/크롭", "레귤러", "롱"]] = Field(..., description="상의기장. 상의가 아니면 null")
    ai_pants_length: Optional[Literal["반바지", "7부", "긴바지"]] = Field(..., description="바지기장. 바지가 아니면 null")
    ai_skirt_length: Optional[Literal["미니", "미디", "롱"]] = Field(..., description="치마기장. 치마가 아니면 null")
    ai_sleeve_length: Optional[Literal["민소매", "반소매", "7부", "긴소매"]] = Field(..., description="소매기장. 상의/원피스/아우터가 아니면 null")
    ai_thickness: Optional[Literal["얇음", "보통", "두꺼움"]] = Field(..., description="두께감. 상세설명/정보고시에 명시된 경우만, 없으면 null")
    ai_stretch: Optional[Literal["없음", "약간", "좋음"]] = Field(..., description="신축성. 상세설명/정보고시에 명시된 경우만, 없으면 null")


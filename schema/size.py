from pydantic import BaseModel, Field
from typing import List, Literal

# 표준 부위명. 표에 없는 항목은 "기타" (raw_name 에 원문 유지)
StdPart = Literal["총장", "어깨너비", "가슴둘레", "허리둘레", "엉덩이둘레", "허벅지둘레", "밑위", "밑단둘레",
                  "소매길이", "화장", "소매통", "암홀", "인심", "발길이", "발볼", "굽높이", "기타"]


class SizeMeasure(BaseModel):
    raw_name: str = Field(description="표에 적힌 원래 항목명 (예: 가슴단면, 허리, 총기장)")
    std_name: StdPart = Field(description="표준 부위명 (총기장/기장→총장, 힙→엉덩이둘레, 바지부리→밑단둘레, 앞길이/앞밑위→밑위 등)")
    is_half: bool = Field(description="단면(한쪽 폭) 측정이면 true, 둘레/길이면 false. 후처리에서 규칙으로 재판정됨")
    value: float = Field(description="숫자 값 (단위 변환 없이 표의 값 그대로)")


class SizeRow(BaseModel):
    group: str = Field(description="표가 성별/기장/핏 등으로 나뉘어 있으면 그 구분 (예: 남성, 여성, 숏). 없으면 빈 문자열")
    label: str = Field(description="표에 적힌 사이즈 라벨 그대로 (예: 066, S, 95, 250)")
    measures: List[SizeMeasure]


class SizeTable(BaseModel):
    found: bool = Field(description="사이즈표를 찾았으면 true")
    kind: str = Field(description="measurement(제품 실측표) | body(착용자 신체치수 기준) | conversion(호수/국가별 환산표) | none")
    unit: str = Field(description="표의 단위 (cm, mm, inch)")
    half_note: bool = Field(description="표 주변에 '단면' 측정이라는 안내 문구가 있으면 true")
    source_slices: List[int] = Field(description="사이즈표가 보인 조각 번호들")
    rows: List[SizeRow] = Field(description="사이즈별 행. 표의 순서 그대로")

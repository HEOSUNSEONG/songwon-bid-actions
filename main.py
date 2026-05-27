import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Query

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "songwon-bid-actions")
DATA_GO_KR_SERVICE_KEY = os.getenv("DATA_GO_KR_SERVICE_KEY")

# 나라장터 입찰공고정보서비스 - 공사조회
NARA_CONSTRUCTION_BID_URL = (
    "http://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoCnstwk"
)

app = FastAPI(
    title="송원건설 입찰분석 GPTS Actions 서버",
    description="나라장터, 낙찰정보, 한국수자원공사 입찰공고를 분석하기 위한 송원건설 전용 API 서버",
    version="0.2.0",
)


COMPANY_PROFILE = {
    "company": "주식회사 송원건설",
    "main_region": "경상남도 김해시",
    "priority_regions": ["김해", "경남", "경상남도", "부산", "양산", "창원", "밀양", "울산"],
    "construction_keywords": [
        "토공",
        "토목",
        "철근콘크리트",
        "철콘",
        "상하수도",
        "상수도",
        "하수도",
        "배수",
        "배수로",
        "관로",
        "포장",
        "도로",
        "보수",
        "정비",
        "시설물",
        "옹벽",
        "측구",
        "맨홀",
        "우수",
        "오수",
    ],
    "risk_keywords": [
        "현장설명",
        "실적제한",
        "공동도급",
        "긴급",
        "특허",
        "신기술",
        "야간",
        "교통통제",
        "폐기물",
        "관급",
        "하자",
        "안전",
    ],
}


def mask_key(value: Optional[str]) -> str:
    if not value:
        return "not_set"
    if len(value) <= 10:
        return "set"
    return value[:4] + "****" + value[-4:]


def make_date_range(days_back: int = 7, days_forward: int = 14) -> Dict[str, str]:
    now = datetime.now()
    begin = now - timedelta(days=days_back)
    end = now + timedelta(days=days_forward)
    return {
        "begin": begin.strftime("%Y%m%d0000"),
        "end": end.strftime("%Y%m%d2359"),
    }


def normalize_items(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    공공데이터 응답에서 item 목록만 안전하게 꺼냅니다.
    item이 1개일 때 dict로 오는 경우도 있어서 list로 맞춥니다.
    """
    response = raw.get("response", {})
    body = response.get("body", {})
    items = body.get("items", {})

    if isinstance(items, dict):
        item = items.get("item", [])
    else:
        item = []

    if isinstance(item, list):
        return item
    if isinstance(item, dict):
        return [item]
    return []


def parse_amount(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value)
    only_numbers = re.sub(r"[^0-9]", "", text)
    if not only_numbers:
        return None
    try:
        return int(only_numbers)
    except ValueError:
        return None


def parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None

    text = str(value).strip()

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y%m%d%H%M",
        "%Y%m%d%H%M%S",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    return None


def get_text(item: Dict[str, Any], keys: List[str]) -> str:
    parts = []
    for key in keys:
        value = item.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts)


def score_notice(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    송원건설 기준 1차 점수화.
    아직 면허/첨부파일/기초금액 상세조회 전 단계라서
    공고명, 기관명, 지역, 마감일, 금액 중심으로 1차 판단합니다.
    """
    title = str(item.get("bidNtceNm", "") or "")
    agency = str(item.get("ntceInsttNm", "") or "")
    demand = str(item.get("dminsttNm", "") or "")
    region_text = get_text(item, ["ntceInsttNm", "dminsttNm", "bidNtceNm"])
    all_text = f"{title} {agency} {demand} {region_text}"

    score = 50
    reasons = []
    risks = []

    # 지역 점수
    if any(region in all_text for region in ["김해"]):
        score += 20
        reasons.append("김해 관련 공고로 지역 적합도가 높습니다.")
    elif any(region in all_text for region in ["경남", "경상남도", "창원", "양산", "밀양"]):
        score += 15
        reasons.append("경남권 공고로 송원건설 검토 대상입니다.")
    elif any(region in all_text for region in ["부산", "울산"]):
        score += 8
        reasons.append("부산/울산권 공고로 거리와 공사금액 확인이 필요합니다.")
    else:
        score -= 10
        risks.append("공고명/기관명 기준으로는 김해·경남권 여부가 뚜렷하지 않습니다.")

    # 공종 점수
    matched_keywords = [
        kw for kw in COMPANY_PROFILE["construction_keywords"] if kw in all_text
    ]
    if matched_keywords:
        score += min(20, len(matched_keywords) * 4)
        reasons.append(f"송원건설 관심 공종 키워드가 포함되어 있습니다: {', '.join(matched_keywords[:5])}")
    else:
        score -= 8
        risks.append("공고명 기준으로 토목/배수/포장/상하수도 관련성이 약합니다.")

    # 리스크 키워드
    matched_risks = [kw for kw in COMPANY_PROFILE["risk_keywords"] if kw in all_text]
    if matched_risks:
        score -= min(15, len(matched_risks) * 3)
        risks.append(f"주의 키워드가 있습니다: {', '.join(matched_risks[:5])}")

    # 마감일 점수
    close_dt = parse_datetime(item.get("bidClseDt"))
    days_left = None
    if close_dt:
        days_left = (close_dt - datetime.now()).days
        if days_left < 0:
            score -= 30
            risks.append("이미 마감된 공고일 수 있습니다.")
        elif days_left <= 1:
            score -= 15
            risks.append("마감이 임박했습니다. 서류 준비 시간이 부족할 수 있습니다.")
        elif days_left <= 3:
            score += 3
            reasons.append("마감 전 검토는 가능하지만 빠른 확인이 필요합니다.")
        else:
            score += 8
            reasons.append("마감까지 검토 시간이 있습니다.")
    else:
        risks.append("마감일시를 해석하지 못했습니다.")

    # 금액 점수
    amount = parse_amount(
        item.get("asignBdgtAmt")
        or item.get("presmptPrce")
        or item.get("bdgtAmt")
        or item.get("basePrce")
    )

    if amount:
        if 30_000_000 <= amount <= 300_000_000:
            score += 10
            reasons.append("금액대가 중소 건설사 검토 범위에 들어올 가능성이 있습니다.")
        elif amount > 1_000_000_000:
            score -= 10
            risks.append("금액이 커서 실적·보증·자금 부담 확인이 필요합니다.")
        elif amount < 30_000_000:
            score -= 3
            risks.append("금액이 작아 이동비·관리비 대비 수익성 확인이 필요합니다.")

    score = max(0, min(100, score))

    if score >= 80:
        grade = "A"
    elif score >= 65:
        grade = "B"
    elif score >= 50:
        grade = "C"
    elif score >= 35:
        grade = "D"
    else:
        grade = "제외검토"

    return {
        "score": score,
        "grade": grade,
        "matched_keywords": matched_keywords,
        "matched_risk_keywords": matched_risks,
        "days_left": days_left,
        "reasons": reasons,
        "risks": risks,
    }


def simplify_notice(item: Dict[str, Any]) -> Dict[str, Any]:
    analysis = score_notice(item)

    return {
        "grade": analysis["grade"],
        "score": analysis["score"],
        "bid_no": item.get("bidNtceNo"),
        "bid_order": item.get("bidNtceOrd"),
        "title": item.get("bidNtceNm"),
        "notice_agency": item.get("ntceInsttNm"),
        "demand_agency": item.get("dminsttNm"),
        "notice_date": item.get("bidNtceDt"),
        "begin_date": item.get("bidBeginDt"),
        "close_date": item.get("bidClseDt"),
        "open_date": item.get("opengDt"),
        "contract_method": item.get("cntrctCnclsMthdNm"),
        "bid_method": item.get("bidMethdNm"),
        "estimated_price": item.get("presmptPrce"),
        "budget_amount": item.get("asignBdgtAmt"),
        "detail_url": item.get("bidNtceDtlUrl"),
        "analysis": analysis,
    }


def call_nara_construction_api(
    keyword: str = "",
    rows: int = 20,
    days_back: int = 7,
    days_forward: int = 14,
) -> Dict[str, Any]:
    if not DATA_GO_KR_SERVICE_KEY:
        return {
            "ok": False,
            "error": "DATA_GO_KR_SERVICE_KEY 환경변수가 설정되지 않았습니다.",
        }

    date_range = make_date_range(days_back=days_back, days_forward=days_forward)

    params = {
        "serviceKey": DATA_GO_KR_SERVICE_KEY,
        "pageNo": 1,
        "numOfRows": rows,
        "type": "json",
        "inqryDiv": 1,
        "inqryBgnDt": date_range["begin"],
        "inqryEndDt": date_range["end"],
    }

    if keyword:
        params["bidNtceNm"] = keyword

    try:
        res = requests.get(NARA_CONSTRUCTION_BID_URL, params=params, timeout=25)
        res.raise_for_status()

        try:
            data = res.json()
        except ValueError:
            return {
                "ok": False,
                "error": "JSON 응답이 아닙니다. 서비스키 Encoding/Decoding 또는 API 응답 형식을 확인해야 합니다.",
                "status_code": res.status_code,
                "raw_preview": res.text[:1000],
            }

        items = normalize_items(data)

        return {
            "ok": True,
            "request": {
                "keyword": keyword,
                "rows": rows,
                "days_back": days_back,
                "days_forward": days_forward,
                "inqryBgnDt": date_range["begin"],
                "inqryEndDt": date_range["end"],
            },
            "total_count": data.get("response", {}).get("body", {}).get("totalCount"),
            "count": len(items),
            "items": items,
        }

    except requests.RequestException as e:
        return {
            "ok": False,
            "error": str(e),
        }


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": APP_NAME,
        "message": "송원건설 입찰분석 서버가 실행 중입니다.",
    }


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": APP_NAME,
        "time": datetime.now().isoformat(),
    }


@app.get("/config-check")
def config_check():
    return {
        "status": "ok",
        "service": APP_NAME,
        "data_go_kr_service_key_set": bool(DATA_GO_KR_SERVICE_KEY),
        "data_go_kr_service_key_masked": mask_key(DATA_GO_KR_SERVICE_KEY),
        "warning": "서비스키 원문은 보안상 표시하지 않습니다.",
    }


@app.get("/bids/nara")
def nara_bids(
    keyword: str = Query("", description="검색할 공고명 키워드 예: 배수로, 포장, 상하수도"),
    rows: int = Query(20, ge=1, le=100),
    days_back: int = Query(7, ge=0, le=30),
    days_forward: int = Query(14, ge=1, le=60),
):
    """
    나라장터 공사 입찰공고 원본 조회 + 간단 정리.
    """
    result = call_nara_construction_api(
        keyword=keyword,
        rows=rows,
        days_back=days_back,
        days_forward=days_forward,
    )

    if not result.get("ok"):
        return result

    simplified = [simplify_notice(item) for item in result["items"]]

    return {
        "status": "ok",
        "source": "나라장터 입찰공고정보서비스 - 공사조회",
        "request": result["request"],
        "total_count": result.get("total_count"),
        "count": len(simplified),
        "items": simplified,
    }


@app.get("/bids/recommend")
def recommend_bids(
    keyword: str = Query("", description="선택 검색어. 비워두면 전체 공사 공고 기준"),
    rows: int = Query(30, ge=1, le=100),
):
    """
    송원건설 기준 추천 공고.
    현재는 공고명/기관명/마감일/금액 기반 1차 점수화입니다.
    """
    result = call_nara_construction_api(
        keyword=keyword,
        rows=rows,
        days_back=7,
        days_forward=21,
    )

    if not result.get("ok"):
        return result

    simplified = [simplify_notice(item) for item in result["items"]]
    simplified.sort(key=lambda x: x["score"], reverse=True)

    recommended = [item for item in simplified if item["grade"] in ["A", "B", "C"]]
    excluded = [item for item in simplified if item["grade"] not in ["A", "B", "C"]]

    return {
        "status": "ok",
        "company": COMPANY_PROFILE["company"],
        "source": "나라장터 입찰공고정보서비스 - 공사조회",
        "summary": {
            "total_checked": len(simplified),
            "recommended_count": len(recommended),
            "excluded_or_low_count": len(excluded),
        },
        "recommended": recommended[:10],
        "excluded_or_low_priority": excluded[:10],
        "notice": "현재는 1차 자동분류입니다. 다음 단계에서 면허제한, 참가가능지역, 기초금액, 첨부파일 분석을 추가합니다.",
    }


@app.get("/bids/today")
def bids_today():
    """
    오늘 추천 입찰공고.
    내부적으로 /bids/recommend와 같은 1차 추천 로직을 사용합니다.
    """
    return recommend_bids(keyword="", rows=30)


@app.get("/bids/results")
def nara_results():
    return {
        "status": "ready",
        "source": "나라장터 낙찰정보서비스",
        "message": "나라장터 낙찰정보 조회 기능은 다음 단계에서 연결합니다.",
    }


@app.get("/bids/water")
def water_bids():
    return {
        "status": "ready",
        "source": "한국수자원공사 전자조달 입찰공고",
        "message": "한국수자원공사 입찰공고 조회 기능은 나라장터 테스트 후 연결합니다.",
    }

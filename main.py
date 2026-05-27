import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Query

load_dotenv()

APP_NAME = os.getenv("APP_NAME", "songwon-bid-actions")
DATA_GO_KR_SERVICE_KEY = os.getenv("DATA_GO_KR_SERVICE_KEY")

KST = timezone(timedelta(hours=9))

# 나라장터 입찰공고정보서비스 - 공사조회
NARA_CONSTRUCTION_BID_URL = (
    "http://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoCnstwk"
)

app = FastAPI(
    title="송원건설 입찰분석 GPTS Actions 서버",
    description="나라장터, 낙찰정보, 한국수자원공사 입찰공고를 분석하기 위한 송원건설 전용 API 서버",
    version="0.3.0",
)


COMPANY_PROFILE = {
    "company": "주식회사 송원건설",
    "main_region": "경상남도 김해시",
    "priority_regions": [
        "김해",
        "경남",
        "경상남도",
        "부산",
        "양산",
        "창원",
        "밀양",
        "울산",
        "진주",
        "사천",
        "거제",
        "통영",
        "함안",
        "창녕",
        "합천",
    ],
    "strong_regions": ["김해", "경남", "경상남도", "양산", "창원", "밀양"],
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
        "재해복구",
        "개선복구",
        "하천",
        "소하천",
        "구거",
        "농로",
        "확포장",
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
        "철도",
        "전기",
        "통신",
        "소방",
        "승강기",
        "엘리베이터",
    ],
    "exclude_keywords": [
        "전기",
        "통신",
        "소방",
        "승강기",
        "엘리베이터",
        "비상발전기",
        "방수",
        "옥상방수",
        "철도신호",
    ],
}


def now_kst() -> datetime:
    return datetime.now(KST)


def mask_key(value: Optional[str]) -> str:
    if not value:
        return "not_set"

    if len(value) <= 10:
        return "set"

    return value[:4] + "****" + value[-4:]


def make_date_range(days_back: int = 7, days_forward: int = 14) -> Dict[str, str]:
    now = now_kst()
    begin = now - timedelta(days=days_back)
    end = now + timedelta(days=days_forward)

    return {
        "begin": begin.strftime("%Y%m%d0000"),
        "end": end.strftime("%Y%m%d2359"),
    }


def normalize_items(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    공공데이터 응답에서 item 목록만 안전하게 꺼냅니다.
    나라장터 API는 응답 형태가 경우에 따라 다르게 올 수 있어서
    dict, list, item 구조를 모두 처리합니다.
    """
    response = raw.get("response", {})
    body = response.get("body", {})
    items = body.get("items", [])

    if isinstance(items, list):
        return items

    if isinstance(items, dict):
        item = items.get("item", [])

        if isinstance(item, list):
            return item

        if isinstance(item, dict):
            return [item]

    item = body.get("item", [])

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


def format_amount(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    return f"{value:,}원"


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
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=KST)
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


def get_all_text(item: Dict[str, Any]) -> str:
    values = []

    for value in item.values():
        if value is not None:
            values.append(str(value))

    return " ".join(values)


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def keyword_matches(item: Dict[str, Any], keyword: str) -> bool:
    """
    나라장터 API 검색 결과가 넓게 들어오는 경우가 있어
    서버에서 다시 검색어 포함 여부를 검사합니다.
    """
    if not keyword:
        return True

    keyword = keyword.strip().lower()

    title = str(item.get("bidNtceNm", "") or "").lower()
    agency = str(item.get("ntceInsttNm", "") or "").lower()
    demand = str(item.get("dminsttNm", "") or "").lower()
    all_text = get_all_text(item).lower()

    # 1순위: 공고명
    if keyword in title:
        return True

    # 2순위: 기관명/수요기관명
    if keyword in agency or keyword in demand:
        return True

    # 3순위: 원본 전체 필드
    if keyword in all_text:
        return True

    return False


def detect_regions(item: Dict[str, Any]) -> List[str]:
    all_text = get_all_text(item)

    matched = []
    for region in COMPANY_PROFILE["priority_regions"]:
        if region in all_text:
            matched.append(region)

    return matched


def is_priority_region(item: Dict[str, Any]) -> bool:
    return len(detect_regions(item)) > 0


def is_strong_region(item: Dict[str, Any]) -> bool:
    all_text = get_all_text(item)
    return any(region in all_text for region in COMPANY_PROFILE["strong_regions"])


def get_deadline_info(item: Dict[str, Any]) -> Dict[str, Any]:
    close_dt = parse_datetime(item.get("bidClseDt"))
    current = now_kst()

    if not close_dt:
        return {
            "close_datetime": None,
            "days_left": None,
            "hours_left": None,
            "is_closed": False,
            "deadline_status": "마감일 해석불가",
        }

    diff = close_dt - current
    hours_left = int(diff.total_seconds() // 3600)
    days_left = diff.days

    if diff.total_seconds() < 0:
        status = "마감"
        is_closed = True
    elif hours_left <= 24:
        status = "24시간 이내 마감"
        is_closed = False
    elif days_left <= 3:
        status = "마감 임박"
        is_closed = False
    else:
        status = "검토 가능"
        is_closed = False

    return {
        "close_datetime": close_dt.isoformat(),
        "days_left": days_left,
        "hours_left": hours_left,
        "is_closed": is_closed,
        "deadline_status": status,
    }


def dedupe_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    같은 공고가 정정공고/중복공고처럼 여러 번 보일 수 있어
    공고명 + 수요기관 + 마감일 기준으로 중복 제거합니다.
    """
    seen = set()
    result = []

    for item in items:
        title = normalize_space(str(item.get("bidNtceNm", "") or ""))
        demand = normalize_space(str(item.get("dminsttNm", "") or ""))
        close_date = normalize_space(str(item.get("bidClseDt", "") or ""))
        price = normalize_space(str(item.get("presmptPrce", "") or ""))

        key = f"{title}|{demand}|{close_date}|{price}"

        if key in seen:
            continue

        seen.add(key)
        result.append(item)

    return result


def apply_local_filters(
    items: List[Dict[str, Any]],
    keyword: str = "",
    priority_only: bool = False,
    exclude_closed: bool = True,
    remove_duplicates: bool = True,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    before_count = len(items)

    filtered = []

    removed_by_keyword = 0
    removed_by_region = 0
    removed_by_closed = 0

    for item in items:
        if keyword and not keyword_matches(item, keyword):
            removed_by_keyword += 1
            continue

        if priority_only and not is_priority_region(item):
            removed_by_region += 1
            continue

        deadline = get_deadline_info(item)
        if exclude_closed and deadline["is_closed"]:
            removed_by_closed += 1
            continue

        filtered.append(item)

    before_dedupe = len(filtered)

    if remove_duplicates:
        filtered = dedupe_items(filtered)

    removed_by_duplicate = before_dedupe - len(filtered)

    return filtered, {
        "raw_count": before_count,
        "after_filter_count": len(filtered),
        "removed_by_keyword": removed_by_keyword,
        "removed_by_region": removed_by_region,
        "removed_by_closed": removed_by_closed,
        "removed_by_duplicate": removed_by_duplicate,
        "priority_only": priority_only,
        "exclude_closed": exclude_closed,
        "remove_duplicates": remove_duplicates,
    }


def score_notice(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    송원건설 기준 1차 점수화.
    아직 면허/첨부파일/기초금액 상세조회 전 단계라서
    공고명, 기관명, 지역, 마감일, 금액 중심으로 1차 판단합니다.
    """
    title = str(item.get("bidNtceNm", "") or "")
    agency = str(item.get("ntceInsttNm", "") or "")
    demand = str(item.get("dminsttNm", "") or "")
    all_text = f"{title} {agency} {demand} {get_all_text(item)}"

    score = 50
    reasons = []
    risks = []

    matched_regions = detect_regions(item)

    # 지역 점수
    if "김해" in all_text:
        score += 25
        reasons.append("김해 관련 공고로 지역 적합도가 매우 높습니다.")
    elif is_strong_region(item):
        score += 20
        reasons.append("경남권 주요 지역 공고로 송원건설 검토 대상입니다.")
    elif any(region in all_text for region in ["부산", "울산"]):
        score += 10
        reasons.append("부산/울산권 공고로 거리와 공사금액 확인이 필요합니다.")
    elif matched_regions:
        score += 8
        reasons.append(f"우선 검토 지역 키워드가 있습니다: {', '.join(matched_regions[:5])}")
    else:
        score -= 15
        risks.append("김해·경남·부산권 공고가 아니어서 지역 적합도가 낮습니다.")

    # 공종 점수
    matched_keywords = [
        kw for kw in COMPANY_PROFILE["construction_keywords"] if kw in all_text
    ]

    if matched_keywords:
        score += min(25, len(matched_keywords) * 5)
        reasons.append(
            f"송원건설 관심 공종 키워드가 포함되어 있습니다: {', '.join(matched_keywords[:6])}"
        )
    else:
        score -= 10
        risks.append("공고명 기준으로 토목/배수/포장/상하수도 관련성이 약합니다.")

    # 제외성 키워드
    matched_exclude_keywords = [
        kw for kw in COMPANY_PROFILE["exclude_keywords"] if kw in all_text
    ]

    if matched_exclude_keywords:
        score -= min(25, len(matched_exclude_keywords) * 8)
        risks.append(
            f"송원건설 주력 공종과 다를 수 있는 키워드가 있습니다: {', '.join(matched_exclude_keywords[:5])}"
        )

    # 리스크 키워드
    matched_risks = [kw for kw in COMPANY_PROFILE["risk_keywords"] if kw in all_text]

    if matched_risks:
        score -= min(18, len(matched_risks) * 3)
        risks.append(f"주의 키워드가 있습니다: {', '.join(matched_risks[:6])}")

    # 마감일 점수
    deadline = get_deadline_info(item)

    if deadline["is_closed"]:
        score -= 40
        risks.append("이미 마감된 공고입니다.")
    elif deadline["hours_left"] is not None:
        if deadline["hours_left"] <= 24:
            score -= 20
            risks.append("24시간 이내 마감으로 서류 준비 시간이 부족할 수 있습니다.")
        elif deadline["days_left"] is not None and deadline["days_left"] <= 3:
            score += 2
            reasons.append("마감 전 검토는 가능하지만 빠른 확인이 필요합니다.")
        else:
            score += 10
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
            score += 12
            reasons.append("금액대가 중소 건설사 검토 범위에 들어올 가능성이 있습니다.")
        elif 300_000_000 < amount <= 800_000_000:
            score += 3
            risks.append("금액이 다소 커서 면허·실적·보증 여력을 확인해야 합니다.")
        elif amount > 800_000_000:
            score -= 15
            risks.append("금액이 커서 실적·보증·자금 부담 확인이 필요합니다.")
        elif amount < 30_000_000:
            score -= 5
            risks.append("금액이 작아 이동비·관리비 대비 수익성 확인이 필요합니다.")
    else:
        risks.append("금액 정보가 부족합니다.")

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
        "matched_regions": matched_regions,
        "matched_keywords": matched_keywords,
        "matched_risk_keywords": matched_risks,
        "matched_exclude_keywords": matched_exclude_keywords,
        "deadline": deadline,
        "amount": amount,
        "amount_text": format_amount(amount),
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
        "title": normalize_space(str(item.get("bidNtceNm", "") or "")),
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

    # 나라장터 결과가 넓게 들어오는 경우가 있어
    # 실제 요청은 최대 100개까지 가져온 뒤 서버에서 다시 필터링합니다.
    api_rows = min(100, max(rows * 5, rows))

    params = {
        "serviceKey": DATA_GO_KR_SERVICE_KEY,
        "pageNo": 1,
        "numOfRows": api_rows,
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
                "api_rows": api_rows,
                "days_back": days_back,
                "days_forward": days_forward,
                "inqryBgnDt": date_range["begin"],
                "inqryEndDt": date_range["end"],
            },
            "total_count": data.get("response", {}).get("body", {}).get("totalCount"),
            "count": len(items),
            "items": items,
            "debug_body_keys": list(data.get("response", {}).get("body", {}).keys()),
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
        "time": now_kst().isoformat(),
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
    strict_keyword: bool = Query(True, description="검색어가 실제 결과에 포함된 공고만 표시"),
    priority_only: bool = Query(False, description="김해/경남/부산권 우선 지역만 표시"),
    exclude_closed: bool = Query(True, description="마감 지난 공고 제외"),
):
    """
    나라장터 공사 입찰공고 조회 + 서버 자체 필터링.
    """
    result = call_nara_construction_api(
        keyword=keyword,
        rows=rows,
        days_back=days_back,
        days_forward=days_forward,
    )

    if not result.get("ok"):
        return result

    filter_keyword = keyword if strict_keyword else ""

    filtered_items, filter_summary = apply_local_filters(
        result["items"],
        keyword=filter_keyword,
        priority_only=priority_only,
        exclude_closed=exclude_closed,
        remove_duplicates=True,
    )

    simplified = [simplify_notice(item) for item in filtered_items]
    simplified.sort(key=lambda x: x["score"], reverse=True)

    return {
        "status": "ok",
        "source": "나라장터 입찰공고정보서비스 - 공사조회",
        "request": result["request"],
        "total_count_from_api": result.get("total_count"),
        "raw_count_from_api": result.get("count"),
        "count": len(simplified[:rows]),
        "filter_summary": filter_summary,
        "debug_body_keys": result.get("debug_body_keys"),
        "items": simplified[:rows],
        "notice": "strict_keyword=true이면 서버에서 검색어 포함 결과만 다시 필터링합니다.",
    }


@app.get("/bids/recommend")
def recommend_bids(
    keyword: str = Query("", description="선택 검색어. 예: 배수로, 포장, 상하수도"),
    rows: int = Query(30, ge=1, le=100),
    priority_only: bool = Query(True, description="김해/경남/부산권 우선 지역만 추천"),
    exclude_closed: bool = Query(True, description="마감 지난 공고 제외"),
):
    """
    송원건설 기준 추천 공고.
    검색어, 지역, 마감, 중복을 서버에서 다시 필터링한 뒤 점수화합니다.
    """
    result = call_nara_construction_api(
        keyword=keyword,
        rows=rows,
        days_back=7,
        days_forward=21,
    )

    if not result.get("ok"):
        return result

    filtered_items, filter_summary = apply_local_filters(
        result["items"],
        keyword=keyword,
        priority_only=priority_only,
        exclude_closed=exclude_closed,
        remove_duplicates=True,
    )

    simplified = [simplify_notice(item) for item in filtered_items]
    simplified.sort(key=lambda x: x["score"], reverse=True)

    recommended = [item for item in simplified if item["grade"] in ["A", "B", "C"]]
    excluded = [item for item in simplified if item["grade"] not in ["A", "B", "C"]]

    return {
        "status": "ok",
        "company": COMPANY_PROFILE["company"],
        "source": "나라장터 입찰공고정보서비스 - 공사조회",
        "summary": {
            "raw_count_from_api": result.get("count"),
            "after_filter_count": len(simplified),
            "recommended_count": len(recommended),
            "excluded_or_low_count": len(excluded),
        },
        "filter_summary": filter_summary,
        "recommended": recommended[:rows],
        "excluded_or_low_priority": excluded[:10],
        "notice": "현재는 공고명/기관명/지역/마감/금액 기반 1차 자동분류입니다. 다음 단계에서 면허제한, 참가가능지역, 기초금액, 첨부파일 분석을 추가합니다.",
    }


@app.get("/bids/today")
def bids_today():
    """
    오늘 추천 입찰공고.
    내부적으로 /bids/recommend와 같은 1차 추천 로직을 사용합니다.
    """
    return recommend_bids(keyword="", rows=30, priority_only=True, exclude_closed=True)


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

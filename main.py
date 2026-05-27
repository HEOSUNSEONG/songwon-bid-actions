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

# 나라장터 입찰공고정보서비스 - 검색조건 공사조회 우선 사용
NARA_CONSTRUCTION_BID_SEARCH_URL = (
    "http://apis.data.go.kr/1230000/BidPublicInfoService04/getBidPblancListInfoCnstwkPPSSrch01"
)

# 예비용 기존 공사조회 주소
NARA_CONSTRUCTION_BID_FALLBACK_URL = (
    "http://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoCnstwk"
)

app = FastAPI(
    title="송원건설 입찰분석 GPTS Actions 서버",
    description="나라장터, 낙찰정보, 한국수자원공사 입찰공고를 분석하기 위한 송원건설 전용 API 서버",
    version="0.3.1",
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
    "strong_regions": [
        "김해",
        "경남",
        "경상남도",
        "양산",
        "창원",
        "밀양",
    ],
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
        "배수관",
        "관로",
        "관거",
        "포장",
        "확포장",
        "도로",
        "보수",
        "정비",
        "시설물",
        "옹벽",
        "측구",
        "맨홀",
        "우수",
        "오수",
        "우수관",
        "오수관",
        "재해복구",
        "개선복구",
        "하천",
        "소하천",
        "구거",
        "농로",
        "흄관",
        "암거",
        "석축",
        "사면",
        "법면",
    ],
    "core_civil_keywords": [
        "토공",
        "토목",
        "철근콘크리트",
        "철콘",
        "상하수도",
        "상수도",
        "하수도",
        "배수",
        "배수로",
        "배수관",
        "관로",
        "관거",
        "포장",
        "확포장",
        "도로",
        "옹벽",
        "측구",
        "맨홀",
        "우수",
        "오수",
        "우수관",
        "오수관",
        "하천",
        "소하천",
        "구거",
        "농로",
        "재해복구",
        "개선복구",
        "법면",
        "사면",
        "석축",
        "암거",
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
        "전기공사",
        "통신",
        "통신공사",
        "소방",
        "소방공사",
        "승강기",
        "엘리베이터",
        "비상발전기",
        "발전기",
        "방수",
        "옥상방수",
        "철도신호",
        "LED",
        "led",
        "조명",
        "조명교체",
        "실습실",
        "웹툰",
        "박물관",
        "전시실",
        "인테리어",
        "리모델링",
        "도장",
        "창호",
        "기계설비",
        "냉난방",
        "공조",
        "CCTV",
        "cctv",
        "가구",
        "집기",
        "냉장",
        "냉동",
        "에어컨",
        "냉난방기",
        "배관교체",
        "보일러",
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


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def get_all_text(item: Dict[str, Any]) -> str:
    values = []

    for value in item.values():
        if value is not None:
            values.append(str(value))

    return " ".join(values)


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

    if keyword in title:
        return True

    if keyword in agency or keyword in demand:
        return True

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
    공고명 + 수요기관 + 마감일 + 가격 기준으로 중복 제거합니다.
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

    matched_exclude_keywords = [
        kw for kw in COMPANY_PROFILE["exclude_keywords"] if kw in all_text
    ]

    if matched_exclude_keywords:
        score -= min(30, len(matched_exclude_keywords) * 10)
        risks.append(
            f"송원건설 주력 공종과 다를 수 있는 키워드가 있습니다: {', '.join(matched_exclude_keywords[:6])}"
        )

    matched_risks = [kw for kw in COMPANY_PROFILE["risk_keywords"] if kw in all_text]

    if matched_risks:
        score -= min(18, len(matched_risks) * 3)
        risks.append(f"주의 키워드가 있습니다: {', '.join(matched_risks[:6])}")

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

    amount = parse_amount(
        item.get("asignBdgtAmt")
        or item.get("presmptPrce")
        or item.get("bdgtAmt")
        or item.get("basePrce")
    )

    if amount is not None and amount > 0:
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


def request_nara_api(
    url: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    res = requests.get(url, params=params, timeout=25)
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
        "data": data,
        "items": items,
        "total_count": data.get("response", {}).get("body", {}).get("totalCount"),
        "debug_body_keys": list(data.get("response", {}).get("body", {}).keys()),
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

    used_url = NARA_CONSTRUCTION_BID_SEARCH_URL
    used_source = "검색조건 공사조회"

    try:
        result = request_nara_api(used_url, params)

        # 검색조건 URL이 환경에 따라 막히거나 빈 응답이면 예비 URL로 재시도
        if not result.get("ok"):
            raise requests.RequestException(result.get("error", "검색조건 API 실패"))

        # 검색조건 API가 응답은 했지만 item 구조가 비정상일 경우 fallback
        if result.get("items") is None:
            raise requests.RequestException("검색조건 API item 파싱 실패")

    except requests.RequestException:
        used_url = NARA_CONSTRUCTION_BID_FALLBACK_URL
        used_source = "기존 공사조회 fallback"

        try:
            result = request_nara_api(used_url, params)
        except requests.RequestException as e:
            return {
                "ok": False,
                "error": str(e),
                "used_url": used_url,
            }

    return {
        "ok": True,
        "used_source": used_source,
        "used_url": used_url,
        "request": {
            "keyword": keyword,
            "rows": rows,
            "api_rows": api_rows,
            "days_back": days_back,
            "days_forward": days_forward,
            "inqryBgnDt": date_range["begin"],
            "inqryEndDt": date_range["end"],
        },
        "total_count": result.get("total_count"),
        "count": len(result.get("items", [])),
        "items": result.get("items", []),
        "debug_body_keys": result.get("debug_body_keys"),
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
        "source": f"나라장터 입찰공고정보서비스 - 공사조회 ({result.get('used_source')})",
        "used_url": result.get("used_url"),
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
    검색어, 지역, 마감, 중복을 서버에서 다시 필터링한 뒤
    송원건설 주력 공종이 아닌 공고는 추천에서 제외합니다.
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

    recommended = []
    excluded = []

    for item in simplified:
        analysis = item.get("analysis", {})
        matched_keywords = analysis.get("matched_keywords", [])
        matched_exclude_keywords = analysis.get("matched_exclude_keywords", [])

        has_core_civil_keyword = any(
            kw in COMPANY_PROFILE["core_civil_keywords"] for kw in matched_keywords
        )

        has_exclude_keyword = len(matched_exclude_keywords) > 0

        if has_exclude_keyword:
            item["exclude_reason"] = (
                "전기/조명/승강기/방수/실습실/기계설비 등 송원건설 주력 공종과 "
                "맞지 않는 키워드가 포함되어 제외합니다."
            )
            excluded.append(item)
            continue

        if not has_core_civil_keyword:
            item["exclude_reason"] = (
                "토목/상하수도/포장/배수/관로/하천/옹벽/측구 등 "
                "송원건설 주력 공종 키워드가 부족하여 제외합니다."
            )
            excluded.append(item)
            continue

        if item["grade"] in ["A", "B", "C"]:
            recommended.append(item)
        else:
            excluded.append(item)

    return {
        "status": "ok",
        "company": COMPANY_PROFILE["company"],
        "source": f"나라장터 입찰공고정보서비스 - 공사조회 ({result.get('used_source')})",
        "used_url": result.get("used_url"),
        "summary": {
            "raw_count_from_api": result.get("count"),
            "after_filter_count": len(simplified),
            "recommended_count": len(recommended),
            "excluded_or_low_count": len(excluded),
        },
        "filter_summary": filter_summary,
        "recommended": recommended[:rows],
        "excluded_or_low_priority": excluded[:10],
        "notice": "추천 목록은 송원건설 주력 공종인 토목/상하수도/배수/관로/포장/하천/옹벽/측구 중심으로 필터링합니다. 다음 단계에서 면허제한, 참가가능지역, 기초금액, 첨부파일 분석을 추가합니다.",
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
@app.get("/bids/smart-recommend")
def smart_recommend_bids(
    rows: int = Query(30, ge=1, le=100),
    priority_only: bool = Query(True, description="김해/경남/부산권 우선 지역만 추천"),
    exclude_closed: bool = Query(True, description="마감 지난 공고 제외"),
):
    """
    송원건설 스마트 추천.
    주력 공종 키워드를 여러 개 자동 검색해서 합친 뒤
    중복 제거, 지역 필터, 마감 필터, 제외 키워드 필터를 적용합니다.
    """
    smart_keywords = [
        "포장",
        "배수",
        "배수로",
        "상하수도",
        "관로",
        "도로",
        "하천",
        "소하천",
        "옹벽",
        "측구",
        "맨홀",
        "농로",
        "재해복구",
        "정비",
        "보수",
    ]

    all_raw_items = []
    keyword_results = []

    for keyword in smart_keywords:
        result = call_nara_construction_api(
            keyword=keyword,
            rows=20,
            days_back=7,
            days_forward=21,
        )

        if not result.get("ok"):
            keyword_results.append(
                {
                    "keyword": keyword,
                    "ok": False,
                    "error": result.get("error"),
                }
            )
            continue

        raw_items = result.get("items", [])
        all_raw_items.extend(raw_items)

        keyword_results.append(
            {
                "keyword": keyword,
                "ok": True,
                "raw_count": len(raw_items),
                "total_count_from_api": result.get("total_count"),
                "used_source": result.get("used_source"),
            }
        )

    # 키워드 전체 결과 중복 제거
    all_raw_items = dedupe_items(all_raw_items)

    # 지역, 마감, 중복 필터
    filtered_items, filter_summary = apply_local_filters(
        all_raw_items,
        keyword="",
        priority_only=priority_only,
        exclude_closed=exclude_closed,
        remove_duplicates=True,
    )

    simplified = [simplify_notice(item) for item in filtered_items]
    simplified.sort(key=lambda x: x["score"], reverse=True)

    recommended = []
    excluded = []

    for item in simplified:
        analysis = item.get("analysis", {})
        matched_keywords = analysis.get("matched_keywords", [])
        matched_exclude_keywords = analysis.get("matched_exclude_keywords", [])

        has_core_civil_keyword = any(
            kw in COMPANY_PROFILE["core_civil_keywords"] for kw in matched_keywords
        )

        has_exclude_keyword = len(matched_exclude_keywords) > 0

        if has_exclude_keyword:
            item["exclude_reason"] = (
                "전기/조명/승강기/방수/실습실/기계설비 등 송원건설 주력 공종과 "
                "맞지 않는 키워드가 포함되어 제외합니다."
            )
            excluded.append(item)
            continue

        if not has_core_civil_keyword:
            item["exclude_reason"] = (
                "토목/상하수도/포장/배수/관로/하천/옹벽/측구 등 "
                "송원건설 주력 공종 키워드가 부족하여 제외합니다."
            )
            excluded.append(item)
            continue

        if item["grade"] in ["A", "B", "C"]:
            recommended.append(item)
        else:
            excluded.append(item)

    return {
        "status": "ok",
        "company": COMPANY_PROFILE["company"],
        "source": "나라장터 입찰공고정보서비스 - 스마트 추천",
        "searched_keywords": smart_keywords,
        "keyword_results": keyword_results,
        "summary": {
            "raw_merged_count": len(all_raw_items),
            "after_filter_count": len(simplified),
            "recommended_count": len(recommended),
            "excluded_or_low_count": len(excluded),
        },
        "filter_summary": filter_summary,
        "recommended": recommended[:rows],
        "excluded_or_low_priority": excluded[:10],
        "notice": "스마트 추천은 송원건설 주력 공종 키워드를 여러 개 자동 검색해 합산한 뒤 추천합니다.",
    }

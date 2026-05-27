import os
from datetime import datetime
from fastapi import FastAPI
from dotenv import load_dotenv

# 로컬 테스트용 .env 파일 읽기
# Render에서는 환경변수로 읽기 때문에 실제 서비스키를 코드에 넣지 않습니다.
load_dotenv()

APP_NAME = os.getenv("APP_NAME", "songwon-bid-actions")
DATA_GO_KR_SERVICE_KEY = os.getenv("DATA_GO_KR_SERVICE_KEY")

app = FastAPI(
    title="송원건설 입찰분석 GPTS Actions 서버",
    description="나라장터, 낙찰정보, 한국수자원공사 입찰공고를 분석하기 위한 송원건설 전용 API 서버",
    version="0.1.0",
)


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
    """
    환경변수 설정 여부 확인용입니다.
    서비스키 원문은 절대 노출하지 않고, 설정 여부만 확인합니다.
    """
    return {
        "status": "ok",
        "service": APP_NAME,
        "data_go_kr_service_key_set": bool(DATA_GO_KR_SERVICE_KEY),
        "warning": "서비스키 원문은 보안상 표시하지 않습니다.",
    }


@app.get("/bids/today")
def bids_today():
    """
    추후 송원건설 기준 오늘 추천 입찰공고를 보여줄 주소입니다.
    현재는 기본 서버 테스트용 응답입니다.
    """
    return {
        "status": "ready",
        "message": "오늘 추천 입찰공고 기능은 다음 단계에서 나라장터 API와 연결합니다.",
        "planned_sources": [
            "나라장터 입찰공고정보서비스",
            "나라장터 낙찰정보서비스",
            "한국수자원공사 전자조달 입찰공고",
        ],
        "company_filter": {
            "company": "주식회사 송원건설",
            "main_region": "경상남도 김해시",
            "priority_regions": ["김해", "경남", "부산", "양산", "창원", "밀양", "울산"],
        },
    }


@app.get("/bids/nara")
def nara_bids():
    """
    추후 나라장터 입찰공고 조회 기능을 연결할 주소입니다.
    """
    return {
        "status": "ready",
        "source": "나라장터 입찰공고정보서비스",
        "message": "나라장터 공사 입찰공고 조회 기능은 다음 단계에서 연결합니다.",
    }


@app.get("/bids/results")
def nara_results():
    """
    추후 나라장터 낙찰정보 조회 기능을 연결할 주소입니다.
    """
    return {
        "status": "ready",
        "source": "나라장터 낙찰정보서비스",
        "message": "나라장터 낙찰정보 조회 기능은 추후 연결합니다.",
    }


@app.get("/bids/water")
def water_bids():
    """
    추후 한국수자원공사 전자조달 입찰공고 조회 기능을 연결할 주소입니다.
    """
    return {
        "status": "ready",
        "source": "한국수자원공사 전자조달 입찰공고",
        "message": "한국수자원공사 입찰공고 조회 기능은 추후 연결합니다.",
    }

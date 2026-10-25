from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.frontend_assets import FRONTEND_ASSETS
from backend.routers import chat_router, conversations_router, data_router
from backend.services.firebase_service import db_service

app = FastAPI(
    title="🏠 나만의 AI 부동산 시세 & 매매 타이밍 비서 API",
    description=(
        "개포우성8차 & 개포현대3차(통합재건축) 시계열 실거래가, 전세 동향, 정부정책·교통호재, 경매 포착 데이터를 분석하고 "
        "**컨텍스트 주입(Context Injection)** 및 **Function Calling**을 통해 맞춤형 답변을 제공하는 FastAPI 백엔드 서버입니다.\n\n"
        "- **대상 단지 확장성**: 기본 탑재된 2개 단지 외에 어떤 아파트 단지를 추가/삭제해도 자동으로 인식해 분석합니다.\n"
        "- **AI 엔진**: Google Gemini API (`openai` 공식 호환 SDK) 연동"
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS 설정 (Vercel 프론트엔드 및 로컬 개발 환경 허용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(data_router.router)
app.include_router(conversations_router.router)
app.include_router(chat_router.router)


@app.get("/api/health", tags=["0. 서버 상태 & 환경 확인 (Health Check)"])
def health_check() -> Dict[str, Any]:
    """서버 상태, DB 연동 모드(Firestore/로컬), AI 엔진 상태(Gemini/폴백) 및 저장된 데이터 수를 확인합니다."""
    return {
        "status": "ok",
        "service": "AI Real Estate Secretary (Gaepo Woosung 8th & Hyundai 3rd + Extensible)",
        "database_mode": db_service.mode,
        "ai_provider": settings.active_ai_provider,
        "ai_model": settings.GEMINI_MODEL if "gemini" in settings.active_ai_provider else settings.OPENAI_MODEL,
        "total_data_count": len(db_service.list_data()),
        "total_conversations_count": len(db_service.list_conversations()),
    }


# 로컬 및 Vercel 서버리스 환경 모두에서 프론트엔드 화면(HTML/CSS/JS)을 안정적으로 서빙
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


def _serve_frontend_asset(filename: str, media_type: str) -> Response:
    file_path = FRONTEND_DIR / filename
    if file_path.exists():
        content = file_path.read_text(encoding="utf-8")
    else:
        content = FRONTEND_ASSETS.get(filename, "")
    return Response(content=content, media_type=media_type)


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
@app.get("/frontend/index.html", include_in_schema=False)
def serve_frontend_index() -> Response:
    return _serve_frontend_asset("index.html", "text/html; charset=utf-8")


@app.get("/styles.css", include_in_schema=False)
@app.get("/frontend/styles.css", include_in_schema=False)
@app.get("/static/styles.css", include_in_schema=False)
def serve_frontend_css() -> Response:
    return _serve_frontend_asset("styles.css", "text/css; charset=utf-8")


@app.get("/app.js", include_in_schema=False)
@app.get("/frontend/app.js", include_in_schema=False)
@app.get("/static/app.js", include_in_schema=False)
def serve_frontend_js() -> Response:
    return _serve_frontend_asset("app.js", "application/javascript; charset=utf-8")



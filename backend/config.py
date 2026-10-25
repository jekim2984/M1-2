from __future__ import annotations

import os
from pathlib import Path

# 프로젝트 루트의 .env 파일 로드 (python-dotenv 사용 + 미설치 환경 대비 내장 파서 지원)
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

try:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)
except ImportError:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))


class Settings:
    """애플리케이션 환경 변수 및 설정 관리 클래스"""

    # AI API 설정 (Gemini OpenAI 호환 엔드포인트 기본 사용)
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "").strip()
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

    # Firebase 설정
    FIREBASE_SERVICE_ACCOUNT_JSON: str = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    FIREBASE_SERVICE_ACCOUNT_PATH: str = os.getenv(
        "FIREBASE_SERVICE_ACCOUNT_PATH", str(BASE_DIR / "serviceAccountKey.json")
    ).strip()

    # CORS 설정
    ALLOWED_ORIGINS_RAW: str = os.getenv("ALLOWED_ORIGINS", "*")

    @property
    def allowed_origins(self) -> list[str]:
        if not self.ALLOWED_ORIGINS_RAW or self.ALLOWED_ORIGINS_RAW.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.ALLOWED_ORIGINS_RAW.split(",") if origin.strip()]

    @property
    def active_ai_provider(self) -> str:
        """활성화된 AI 공급자 확인 (gemini | openai | fallback)"""
        if self.GEMINI_API_KEY and self.GEMINI_API_KEY != "your_gemini_api_key_here":
            return "gemini"
        if self.OPENAI_API_KEY and not self.OPENAI_API_KEY.startswith("your_"):
            # 만약 OPENAI_API_KEY 자리에 AIza...로 시작하는 Gemini 키를 넣은 경우 자동 감지
            if self.OPENAI_API_KEY.startswith("AIza"):
                return "gemini_via_openai_env"
            return "openai"
        return "fallback"


settings = Settings()

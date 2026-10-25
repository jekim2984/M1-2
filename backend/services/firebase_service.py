from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import settings
from backend.seed_data import INITIAL_SEED_DATA

# 로컬 백업 스토어 파일 경로 (Firebase 미연결 시 및 Vercel 서버리스 /tmp 폴백 지원)
_DEFAULT_LOCAL_PATH = Path(__file__).resolve().parent.parent / "local_db_runtime.json"
LOCAL_STORE_PATH = (
    Path("/tmp/local_db_runtime.json")
    if os.environ.get("VERCEL")
    else _DEFAULT_LOCAL_PATH
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_memo(memo: str) -> tuple[str, Optional[str]]:
    """
    memo의 [말머리] 태그를 자동 분석하여 (category, complex_name)을 반환합니다.
    - category: 'trade' (매매실거래/시세) | 'auction' (경매매물/낙찰) | 'policy' (정부정책) | 'transit' (교통호재)
    - complex_name: '개포우성8차', '개포현대3차', 또는 사용자가 새로 추가한 '[단지명]'
    """
    memo_text = memo.strip()
    tags = re.findall(r"\[([^\]]+)\]", memo_text)
    first_tag = tags[0].strip() if tags else ""

    # 1. 카테고리 판별
    if "경매" in first_tag or "경매" in memo_text[:25]:
        category = "auction"
    elif "정책" in first_tag or "정부정책" in memo_text[:25] or "토지거래허가" in first_tag:
        category = "policy"
    elif "교통" in first_tag or "호재" in first_tag or "GTX" in first_tag:
        category = "transit"
    else:
        category = "trade"

    # 2. 단지명 자동 추출 (새로운 아파트를 추가해도 자동으로 인식!)
    complex_name: Optional[str] = None
    if "우성8차" in memo_text or "8차우성" in memo_text:
        complex_name = "개포우성8차"
    elif "현대3차" in memo_text:
        complex_name = "개포현대3차"
    elif first_tag and category == "trade":
        # 예: [대치은마], [잠실주공5단지/실거래] 등에서 단지명 추출
        cleaned = re.split(r"[/·|]", first_tag)[0].strip()
        if cleaned not in ("정부정책", "교통호재", "경매매물", "경매낙찰", "실거래", "매매"):
            complex_name = cleaned

    return category, complex_name


class DatabaseService:
    """
    Firestore Database 연동 및 CRUD 서비스 클래스.
    - 환경변수(FIREBASE_SERVICE_ACCOUNT_JSON 또는 FIREBASE_SERVICE_ACCOUNT_PATH)가 설정되어 있으면
      실제 Google Cloud Firestore의 `data`, `conversations` 컬렉션을 사용합니다.
    - 아직 Firebase 키를 넣기 전인 로컬 테스트 환경에서는 자동으로 로컬 JSON 스토어로 폴백하여
      서버 에러 없이 즉시 119개 초기 데이터와 모든 CRUD/채팅 기능을 체험할 수 있습니다.
    """

    def __init__(self) -> None:
        self.db = None
        self.mode = "local_fallback"
        self._local_cache: Dict[str, Dict[str, Any]] = {"data": {}, "conversations": {}}
        self._init_firebase()
        self._ensure_initial_seed()

    def _init_firebase(self) -> None:
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore

            cred = None
            if settings.FIREBASE_SERVICE_ACCOUNT_JSON:
                # JSON 문자열 환경변수에서 로드 (Render 배포 시 권장)
                cred_dict = json.loads(settings.FIREBASE_SERVICE_ACCOUNT_JSON)
                cred = credentials.Certificate(cred_dict)
            elif settings.FIREBASE_SERVICE_ACCOUNT_PATH and os.path.exists(settings.FIREBASE_SERVICE_ACCOUNT_PATH):
                # 로컬 JSON 키 파일 경로에서 로드
                cred = credentials.Certificate(settings.FIREBASE_SERVICE_ACCOUNT_PATH)

            if cred is not None:
                if not firebase_admin._apps:
                    firebase_admin.initialize_app(cred)
                self.db = firestore.client()
                self.mode = "firestore"
                print("[DatabaseService] Firebase Firestore 연동 완료!")
            else:
                print(
                    "[DatabaseService] Firebase 서비스 계정 키가 아직 설정되지 않아 "
                    "로컬 스토어 모드로 동작합니다. (.env 설정 시 Firestore로 자동 전환)"
                )
                self._load_local_store()
        except Exception as e:
            print(f"[DatabaseService] Firebase 초기화 예외 발생 ({e}) -> 로컬 스토어 모드로 동작합니다.")
            self.db = None
            self.mode = "local_fallback"
            self._load_local_store()

    def _load_local_store(self) -> None:
        if LOCAL_STORE_PATH.exists():
            try:
                with open(LOCAL_STORE_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict) and "data" in loaded and "conversations" in loaded:
                        self._local_cache = loaded
                        return
            except Exception:
                pass
        self._local_cache = {"data": {}, "conversations": {}}

    def _save_local_store(self) -> None:
        try:
            with open(LOCAL_STORE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._local_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[DatabaseService] 로컬 스토어 저장 실패: {e}")

    def _enrich_item(self, doc_id: str, raw: Dict[str, Any]) -> Dict[str, Any]:
        memo = str(raw.get("memo", ""))
        category, complex_name = classify_memo(memo)
        return {
            "id": doc_id,
            "date": str(raw.get("date", "")),
            "value": float(raw.get("value", 0)),
            "memo": memo,
            "category": raw.get("category") or category,
            "complex_name": raw.get("complex_name") or complex_name,
            "created_at": raw.get("created_at") or _now_iso(),
        }

    def _ensure_initial_seed(self) -> None:
        """데이터가 비어 있으면 개포우성8차 & 개포현대3차 시계열 데이터(127건)를 자동 시딩합니다."""
        if self.mode == "firestore" and self.db is not None:
            try:
                from google.api_core.retry import Retry
                # Firestore 데이터베이스 생성 전(403 SERVICE_DISABLED)일 때 지연 없이 즉시 폴백하도록 짧은 리트라이 적용
                fast_retry = Retry(initial=0.5, maximum=1.0, multiplier=1.0, deadline=2.5)
                snaps = list(self.db.collection("data").limit(1).stream(retry=fast_retry, timeout=2.5))
                if len(snaps) == 0:
                    print(f"[DatabaseService] Firestore 초기 시계열 데이터 {len(INITIAL_SEED_DATA)}건을 시딩합니다...")
                    self.seed_default_data(reset=False)
                return
            except Exception as e:
                print(f"[DatabaseService] Firestore 아직 미생성 또는 비활성 상태 ({e}) -> 로컬 스토어로 즉시 전환합니다.")
                self.mode = "local_fallback"

        self._load_local_store()
        if len(self._local_cache["data"]) == 0:
            self.seed_default_data(reset=False)

    def seed_default_data(self, reset: bool = False) -> int:
        if self.mode == "firestore" and self.db is not None:
            col_ref = self.db.collection("data")
            if reset:
                for doc in col_ref.stream():
                    doc.reference.delete()
            batch = self.db.batch()
            count = 0
            for idx, item in enumerate(INITIAL_SEED_DATA):
                doc_id = f"seed_{idx + 1:03d}"
                doc_ref = col_ref.document(doc_id)
                category, complex_name = classify_memo(item["memo"])
                payload = {
                    "date": item["date"],
                    "value": float(item["value"]),
                    "memo": item["memo"],
                    "category": category,
                    "complex_name": complex_name,
                    "created_at": _now_iso(),
                }
                batch.set(doc_ref, payload)
                count += 1
            batch.commit()
            return count
        else:
            if reset:
                self._local_cache["data"] = {}
            for idx, item in enumerate(INITIAL_SEED_DATA):
                doc_id = f"seed_{idx + 1:03d}"
                category, complex_name = classify_memo(item["memo"])
                self._local_cache["data"][doc_id] = {
                    "date": item["date"],
                    "value": float(item["value"]),
                    "memo": item["memo"],
                    "category": category,
                    "complex_name": complex_name,
                    "created_at": _now_iso(),
                }
            self._save_local_store()
            return len(INITIAL_SEED_DATA)

    # =====================================================================
    # data 컬렉션 CRUD 메서드
    # =====================================================================
    def list_data(
        self,
        complex_filter: Optional[str] = None,
        category_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        if self.mode == "firestore" and self.db is not None:
            docs = self.db.collection("data").stream(timeout=8.0)
            for doc in docs:
                items.append(self._enrich_item(doc.id, doc.to_dict() or {}))
        else:
            for doc_id, raw in self._local_cache["data"].items():
                items.append(self._enrich_item(doc_id, raw))

        # 필터링 지원
        if complex_filter and complex_filter not in ("all", "전체"):
            items = [
                it
                for it in items
                if it.get("complex_name") == complex_filter or complex_filter in it.get("memo", "")
            ]
        if category_filter and category_filter not in ("all", "전체"):
            items = [it for it in items if it.get("category") == category_filter]

        # 날짜순 정렬 (최신순 조회가 기본이되 분석 시 오름차순 활용)
        items.sort(key=lambda x: (x["date"], x["id"]), reverse=True)
        return items

    def create_data(self, date: str, value: float, memo: str) -> Dict[str, Any]:
        doc_id = f"data_{uuid.uuid4().hex[:10]}"
        category, complex_name = classify_memo(memo)
        payload = {
            "date": date,
            "value": float(value),
            "memo": memo,
            "category": category,
            "complex_name": complex_name,
            "created_at": _now_iso(),
        }
        if self.mode == "firestore" and self.db is not None:
            self.db.collection("data").document(doc_id).set(payload)
        else:
            self._local_cache["data"][doc_id] = payload
            self._save_local_store()
        return self._enrich_item(doc_id, payload)

    def update_data(
        self,
        doc_id: str,
        date: Optional[str] = None,
        value: Optional[float] = None,
        memo: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if self.mode == "firestore" and self.db is not None:
            doc_ref = self.db.collection("data").document(doc_id)
            snap = doc_ref.get()
            if not snap.exists:
                return None
            current = snap.to_dict() or {}
            if date is not None:
                current["date"] = date
            if value is not None:
                current["value"] = float(value)
            if memo is not None:
                current["memo"] = memo
                category, complex_name = classify_memo(memo)
                current["category"] = category
                current["complex_name"] = complex_name
            doc_ref.set(current)
            return self._enrich_item(doc_id, current)
        else:
            if doc_id not in self._local_cache["data"]:
                return None
            current = self._local_cache["data"][doc_id]
            if date is not None:
                current["date"] = date
            if value is not None:
                current["value"] = float(value)
            if memo is not None:
                current["memo"] = memo
                category, complex_name = classify_memo(memo)
                current["category"] = category
                current["complex_name"] = complex_name
            self._save_local_store()
            return self._enrich_item(doc_id, current)

    def delete_data(self, doc_id: str) -> bool:
        if self.mode == "firestore" and self.db is not None:
            doc_ref = self.db.collection("data").document(doc_id)
            snap = doc_ref.get()
            if not snap.exists:
                return False
            doc_ref.delete()
            return True
        else:
            if doc_id not in self._local_cache["data"]:
                return False
            del self._local_cache["data"][doc_id]
            self._save_local_store()
            return True

    # =====================================================================
    # conversations 컬렉션 CRUD 메서드
    # =====================================================================
    def list_conversations(self) -> List[Dict[str, Any]]:
        convs: List[Dict[str, Any]] = []
        if self.mode == "firestore" and self.db is not None:
            docs = self.db.collection("conversations").stream(timeout=8.0)
            for doc in docs:
                raw = doc.to_dict() or {}
                msgs = raw.get("messages", [])
                convs.append(
                    {
                        "id": doc.id,
                        "title": raw.get("title", "부동산 비서 상담"),
                        "created_at": raw.get("created_at", _now_iso()),
                        "updated_at": raw.get("updated_at", raw.get("created_at", _now_iso())),
                        "message_count": len(msgs),
                        "messages": msgs,
                    }
                )
        else:
            for doc_id, raw in self._local_cache["conversations"].items():
                msgs = raw.get("messages", [])
                convs.append(
                    {
                        "id": doc_id,
                        "title": raw.get("title", "부동산 비서 상담"),
                        "created_at": raw.get("created_at", _now_iso()),
                        "updated_at": raw.get("updated_at", raw.get("created_at", _now_iso())),
                        "message_count": len(msgs),
                        "messages": msgs,
                    }
                )
        convs.sort(key=lambda x: x["updated_at"], reverse=True)
        return convs

    def get_conversation(self, conv_id: str) -> Optional[Dict[str, Any]]:
        if self.mode == "firestore" and self.db is not None:
            doc = self.db.collection("conversations").document(conv_id).get()
            if not doc.exists:
                return None
            raw = doc.to_dict() or {}
            msgs = raw.get("messages", [])
            return {
                "id": doc.id,
                "title": raw.get("title", "부동산 비서 상담"),
                "created_at": raw.get("created_at", _now_iso()),
                "updated_at": raw.get("updated_at", raw.get("created_at", _now_iso())),
                "message_count": len(msgs),
                "messages": msgs,
            }
        else:
            raw = self._local_cache["conversations"].get(conv_id)
            if not raw:
                return None
            msgs = raw.get("messages", [])
            return {
                "id": conv_id,
                "title": raw.get("title", "부동산 비서 상담"),
                "created_at": raw.get("created_at", _now_iso()),
                "updated_at": raw.get("updated_at", raw.get("created_at", _now_iso())),
                "message_count": len(msgs),
                "messages": msgs,
            }

    def save_conversation(
        self,
        title: str,
        messages: List[Dict[str, Any]],
        conv_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = _now_iso()
        if not conv_id:
            conv_id = f"conv_{uuid.uuid4().hex[:10]}"

        existing = self.get_conversation(conv_id)
        created_at = existing["created_at"] if existing else now

        payload = {
            "title": title,
            "created_at": created_at,
            "updated_at": now,
            "messages": messages,
        }

        if self.mode == "firestore" and self.db is not None:
            self.db.collection("conversations").document(conv_id).set(payload)
        else:
            self._local_cache["conversations"][conv_id] = payload
            self._save_local_store()

        return {
            "id": conv_id,
            "title": title,
            "created_at": created_at,
            "updated_at": now,
            "message_count": len(messages),
            "messages": messages,
        }

    def delete_conversation(self, conv_id: str) -> bool:
        if self.mode == "firestore" and self.db is not None:
            doc_ref = self.db.collection("conversations").document(conv_id)
            if not doc_ref.get().exists:
                return False
            doc_ref.delete()
            return True
        else:
            if conv_id not in self._local_cache["conversations"]:
                return False
            del self._local_cache["conversations"][conv_id]
            self._save_local_store()
            return True


db_service = DatabaseService()


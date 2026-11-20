"""웹 푸시(PWA 모바일·브라우저 알림) 발송.

- 켜짐 조건: 환경변수 VAPID_PUBLIC_KEY · VAPID_PRIVATE_KEY 가 있고 pywebpush 를 불러올 수 있을 때.
  하나라도 없으면 enabled()=False → 발송을 건너뛰고 서버·인앱 알림은 그대로 동작한다.
- pywebpush(+cryptography)는 실제로 보낼 때 처음 import 한다 (1GB 서버에서 쓰지 않으면 메모리를 쓰지 않게).
- 만료된 구독(푸시 서비스가 404/410 응답)은 발송 중 바로 DB 에서 지운다.

app.py 와 순환 import 를 피하려고 필요한 것은 init(deps) 로 받는다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

D = {}
_LIB = {"checked": False, "ok": False, "error": None}
TTL_SECONDS = 6 * 3600          # 기기가 꺼져 있으면 푸시 서비스가 최대 6시간 보관
MAX_DEVICES_PER_USER = 10


def init(deps):
    D.update(deps)


def log(msg):
    try:
        D["app"].logger.info(f"[PUSH] {msg}")
    except Exception:
        print(f"[PUSH] {msg}", flush=True)


def public_key():
    return os.environ.get("VAPID_PUBLIC_KEY", "").strip()


def _private_key():
    return os.environ.get("VAPID_PRIVATE_KEY", "").strip()


def _subject():
    # 푸시 서비스가 문제 시 연락할 주소. 설정이 없으면 형식만 맞춘 기본값 (실제 주소는 .env 에만)
    return os.environ.get("VAPID_SUBJECT", "").strip() or "mailto:admin@example.com"


def _lib_ok():
    if not _LIB["checked"]:
        _LIB["checked"] = True
        try:
            import pywebpush  # noqa: F401
            _LIB["ok"] = True
        except Exception as e:      # 설치 실패 등 → 푸시만 끄고 계속
            _LIB["error"] = str(e)[:200]
    return _LIB["ok"]


def status():
    """→ (enabled, 꺼진 이유 | None)"""
    if not public_key() or not _private_key():
        return False, "VAPID 키가 설정되지 않음"
    if not _lib_ok():
        return False, f"pywebpush 를 불러올 수 없음: {_LIB['error']}"
    return True, None


def enabled():
    return status()[0]


def short_device(ua):
    """User-Agent → '휴대폰 기종 · 브라우저' 정도의 짧은 이름 (개인 식별 정보는 남기지 않음)."""
    ua = ua or ""
    os_ = ("iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else "Android" if "Android" in ua
           else "Windows" if "Windows" in ua else "Mac" if "Mac OS" in ua else "Linux" if "Linux" in ua else "기타")
    br = ("Edge" if "Edg/" in ua else "Samsung" if "SamsungBrowser" in ua else "Whale" if "Whale" in ua
          else "Chrome" if "Chrome" in ua else "Firefox" if "Firefox" in ua else "Safari" if "Safari" in ua else "")
    return f"{os_} · {br}".strip(" ·")[:80]


def save_subscription(user_id, sub, user_agent=""):
    """브라우저의 PushSubscription.toJSON() 을 저장. 같은 endpoint 가 있으면 갱신(다른 계정이면 현재 계정으로 옮김).
    → (row, 오류 | None)"""
    P, db = D["PushSubscription"], D["db"]
    sub = sub or {}
    endpoint = str(sub.get("endpoint") or "")
    keys = sub.get("keys") or {}
    p256dh, auth = str(keys.get("p256dh") or ""), str(keys.get("auth") or "")
    if not endpoint.startswith("https://") or len(endpoint) > 1000 or not (20 <= len(p256dh) <= 200) or not (8 <= len(auth) <= 100):
        return None, "구독 정보가 올바르지 않습니다"
    row = P.query.filter_by(endpoint=endpoint).first()
    if not row:
        row = P(endpoint=endpoint, user_id=user_id, p256dh=p256dh, auth=auth)
        db.session.add(row)
    row.user_id, row.p256dh, row.auth = user_id, p256dh, auth
    row.user_agent = short_device(user_agent)
    row.last_used_at = datetime.utcnow()
    db.session.flush()
    # 기기 수 제한: 오래된 것부터 정리
    rows = P.query.filter_by(user_id=user_id).order_by(P.last_used_at.desc(), P.id.desc()).all()
    for old in rows[MAX_DEVICES_PER_USER:]:
        db.session.delete(old)
    db.session.commit()
    return row, None


def remove_subscription(user_id, endpoint):
    P, db = D["PushSubscription"], D["db"]
    n = P.query.filter_by(user_id=user_id, endpoint=str(endpoint or "")).delete()
    db.session.commit()
    return n


def send_to_user(user_id, payload):
    """사용자의 모든 기기로 1건 발송. → {"sent", "failed", "removed"}"""
    out = {"sent": 0, "failed": 0, "removed": 0}
    ok, _ = status()
    if not ok:
        return out
    P, db = D["PushSubscription"], D["db"]
    rows = P.query.filter_by(user_id=user_id).all()
    if not rows:
        return out
    from pywebpush import webpush, WebPushException
    data = json.dumps(payload, ensure_ascii=False)
    for row in rows:
        try:
            webpush({"endpoint": row.endpoint, "keys": {"p256dh": row.p256dh, "auth": row.auth}},
                    data=data, vapid_private_key=_private_key(), vapid_claims={"sub": _subject()},
                    ttl=TTL_SECONDS, timeout=10)
            row.last_used_at = datetime.utcnow()
            out["sent"] += 1
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):          # 기기에서 구독이 사라짐 → 정리
                db.session.delete(row)
                out["removed"] += 1
            else:
                out["failed"] += 1
                log(f"발송 실패 user={user_id} code={code}: {str(e)[:120]}")
        except Exception as e:
            out["failed"] += 1
            log(f"발송 오류 user={user_id}: {str(e)[:120]}")
    db.session.commit()
    return out

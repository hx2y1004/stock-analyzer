"""AI 결과 캐시 + 동시 호출 중복 제거.

- 2단 캐시: 프로세스 메모리(LRU) → DB(`ai_cache` 테이블). 메모리에 없으면 DB를 보고, DB에 있으면 메모리에 올린다.
  DB가 실패해도(연결 오류 등) 메모리 캐시로 계속 동작한다.
- in-flight 중복 제거: 같은 키의 AI 호출이 이미 진행 중이면 새로 호출하지 않고 그 결과를 기다린다
  (새로고침 연타, 같은 종목 동시 접속).
- 백그라운드 스레드에서도 쓰이므로 DB 작업은 항상 app_context 안에서 한다.
"""
import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from datetime import datetime, timedelta

_app = None
_db = None
_Model = None
_log = None

_MEM = OrderedDict()            # key → (expires_ts | None, payload, model)
_MEM_MAX = 3000
_MEM_LOCK = threading.Lock()

_INFLIGHT = {}                  # key → Future
_INFLIGHT_LOCK = threading.Lock()

_puts_since_cleanup = 0


def init(app, db, model, logger):
    global _app, _db, _Model, _log
    _app, _db, _Model, _log = app, db, model, logger


def _mem_get(key):
    with _MEM_LOCK:
        hit = _MEM.get(key)
        if not hit:
            return None
        exp, payload, model = hit
        if exp is not None and exp < time.time():
            _MEM.pop(key, None)
            return None
        _MEM.move_to_end(key)
        return payload, model


def _mem_put(key, payload, model, exp_ts):
    with _MEM_LOCK:
        _MEM[key] = (exp_ts, payload, model)
        _MEM.move_to_end(key)
        while len(_MEM) > _MEM_MAX:
            _MEM.popitem(last=False)


def get(key):
    """(payload, model) 또는 None."""
    hit = _mem_get(key)
    if hit:
        return hit
    if _app is None:
        return None
    try:
        with _app.app_context():
            row = _Model.query.filter_by(cache_key=key).first()
            if not row:
                return None
            if row.expires_at is not None and row.expires_at < datetime.utcnow():
                return None
            payload = json.loads(row.payload_json)
            # expires_at 은 UTC naive → epoch 초
            exp_ts = (row.expires_at - datetime(1970, 1, 1)).total_seconds() if row.expires_at else None
            _mem_put(key, payload, row.model_used, exp_ts)
            return payload, row.model_used
    except Exception as e:
        if _log:
            _log.warning(f"[ai_cache] DB get 실패({key}): {e}")
        return None


def put(key, ticker, cache_type, payload, model=None, ttl_seconds=None):
    """ttl_seconds=None 이면 만료 없음."""
    global _puts_since_cleanup
    exp_ts = time.time() + ttl_seconds if ttl_seconds else None
    _mem_put(key, payload, model, exp_ts)
    if _app is None:
        return
    try:
        with _app.app_context():
            now = datetime.utcnow()
            exp = now + timedelta(seconds=ttl_seconds) if ttl_seconds else None
            body = json.dumps(payload, ensure_ascii=False)
            row = _Model.query.filter_by(cache_key=key).first()
            if row:
                row.payload_json, row.model_used, row.created_at, row.expires_at = body, model, now, exp
                row.ticker, row.cache_type = ticker, cache_type
            else:
                _db.session.add(_Model(cache_key=key, ticker=ticker, cache_type=cache_type,
                                       payload_json=body, model_used=model,
                                       created_at=now, expires_at=exp))
            try:
                _db.session.commit()
            except Exception:
                # 동시에 같은 키를 넣은 경우(유니크 충돌) → 다시 읽어서 갱신
                _db.session.rollback()
                row = _Model.query.filter_by(cache_key=key).first()
                if row:
                    row.payload_json, row.model_used, row.created_at, row.expires_at = body, model, now, exp
                    _db.session.commit()
            _puts_since_cleanup += 1
            if _puts_since_cleanup >= 200:          # 가끔 만료된 행 정리
                _puts_since_cleanup = 0
                _Model.query.filter(_Model.expires_at.isnot(None),
                                    _Model.expires_at < now - timedelta(days=1)).delete()
                _db.session.commit()
    except Exception as e:
        if _log:
            _log.warning(f"[ai_cache] DB put 실패({key}): {e}")
        try:
            with _app.app_context():
                _db.session.rollback()
        except Exception:
            pass


def single_flight(key, fn, wait_timeout=None):
    """같은 key 의 작업이 진행 중이면 그 결과를 기다리고, 아니면 fn() 을 직접 실행한다.

    wait_timeout 초 안에 남의 작업이 안 끝나면 None (작업은 계속 돈다).
    fn 의 예외는 호출자에게 그대로 전달된다.
    """
    with _INFLIGHT_LOCK:
        fut = _INFLIGHT.get(key)
        owner = fut is None
        if owner:
            fut = Future()
            _INFLIGHT[key] = fut
    if not owner:
        try:
            return fut.result(timeout=wait_timeout)
        except Exception:
            return None
    try:
        result = fn()
        fut.set_result(result)
        return result
    except Exception as e:
        fut.set_exception(e)
        raise
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(key, None)


def inflight(key):
    with _INFLIGHT_LOCK:
        return key in _INFLIGHT

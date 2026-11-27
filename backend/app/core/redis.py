"""
Redis Refresh Token Cache & Session Management.
Focuses strictly on long-lived Refresh Tokens (7 days) with explicit TTLs.
Access tokens (5 min) are purely stateless JWTs verified via HS256 signature in memory.
Implements Cache-Aside:
- If refresh token is in Redis and valid -> fast O(1) response.
- If refresh token is removed/evicted from Redis -> falls back to PostgreSQL as source of truth.
"""
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None

from app.core.config import settings

logger = logging.getLogger("vaultcity.redis")

# Global Redis connection pool
_redis_client: Optional[Any] = None
_redis_retry_after: float = 0.0
_REDIS_RETRY_COOLDOWN_SECONDS: float = 15.0

# In-memory fallback map (jti -> dict, and user_id -> dict) when Redis server is offline
_memory_token_map: Dict[str, Dict[str, Any]] = {}
_memory_user_tokens: Dict[str, Dict[str, str]] = {}
_memory_otp_map: Dict[str, Dict[str, Any]] = {}
_memory_otp_resends: Dict[str, Dict[str, int]] = {}


def reset_redis_client(cooldown_seconds: float = _REDIS_RETRY_COOLDOWN_SECONDS) -> None:
    """Reset cached Redis client after a connection error and set a reconnect cooldown."""
    global _redis_client, _redis_retry_after
    _redis_client = None
    _redis_retry_after = time.monotonic() + cooldown_seconds


async def get_redis_client() -> Optional[Any]:
    """Retrieve or initialize singleton Redis async connection pool with tight timeouts and auto-reconnect."""
    global _redis_client, _redis_retry_after
    if aioredis is None:
        return None

    if _redis_client is not None:
        return _redis_client

    now = time.monotonic()
    if now < _redis_retry_after:
        return None

    try:
        client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_timeout=1.5,
            socket_connect_timeout=1.5,
        )
        # Fast ping verification
        await client.ping()
        _redis_client = client
        _redis_retry_after = 0.0
        logger.info("Connected to Redis cache at %s", settings.REDIS_URL)
        return _redis_client
    except Exception as e:
        logger.warning(
            "Redis connection failed (%s); operating with in-memory fallback (retrying in %.0fs).",
            e,
            _REDIS_RETRY_COOLDOWN_SECONDS,
        )
        _redis_client = None
        _redis_retry_after = time.monotonic() + _REDIS_RETRY_COOLDOWN_SECONDS
        return None


async def store_refresh_token(
    jti: str,
    user_id: str,
    exp_timestamp: int,
    raw_token: Optional[str] = None,
) -> bool:
    """
    Store refresh token in Redis with exact TTL (7 days).
    Enforces strict TTL on every key:
    - vault:refresh:{jti} with TTL = exp_timestamp - now
    - vault:user_refresh_tokens:{user_id} set with TTL
    """
    now = int(time.time())
    ttl = max(1, exp_timestamp - now)
    user_id_str = str(user_id)

    # In-memory fallback store
    _memory_token_map[jti] = {
        "jti": jti,
        "sub": user_id_str,
        "type": "refresh",
        "exp": exp_timestamp,
        "created_at": now,
        "revoked": 0,
    }
    if user_id_str not in _memory_user_tokens:
        _memory_user_tokens[user_id_str] = {}
    _memory_user_tokens[user_id_str][jti] = str(exp_timestamp)

    client = await get_redis_client()
    if not client:
        return True

    try:
        refresh_key = f"vault:refresh:{jti}"
        user_key = f"vault:user_refresh_tokens:{user_id_str}"

        async with client.pipeline(transaction=True) as pipe:
            mapping_data = {
                "jti": jti,
                "sub": user_id_str,
                "exp": str(exp_timestamp),
                "created_at": str(now),
                "revoked": "0",
            }
            if raw_token:
                mapping_data["token_prefix"] = raw_token[:16]

            pipe.hset(refresh_key, mapping=mapping_data)
            pipe.expire(refresh_key, ttl)

            pipe.sadd(user_key, jti)
            # Keep user set alive for token duration + 1 hour
            pipe.expire(user_key, max(ttl + 3600, 60 * 60 * 24 * 8))
            await pipe.execute()
        return True
    except Exception as e:
        logger.error("Redis store_refresh_token failure for jti=%s: %s", jti, e)
        return True


async def check_refresh_token_in_redis(
    jti: str,
    user_id: Optional[str] = None,
) -> Optional[bool]:
    """
    Check refresh token status in Redis cache in O(1) time.
    Returns:
      True:  Found in Redis and valid (not revoked and not expired).
      False: Found in Redis but revoked or expired.
      None:  Cache miss (removed, evicted from Redis, or Redis offline) -> check Database!
    """
    now = int(time.time())

    # Check in-memory fallback first
    if jti in _memory_token_map:
        mem = _memory_token_map[jti]
        if mem.get("revoked") == 1:
            return False
        if mem.get("exp", 0) < now:
            return False
        if user_id and mem.get("sub") != str(user_id):
            return False
        return True

    client = await get_redis_client()
    if not client:
        # Cache miss (Redis offline) -> delegate to Database as source of truth
        return None

    try:
        refresh_key = f"vault:refresh:{jti}"
        data = await client.hgetall(refresh_key)
        if not data:
            # Token not in Redis (evicted / expired / removed) -> Cache miss!
            return None

        # Check revoked flag
        if data.get("revoked") == "1":
            return False

        # Check expiration
        exp = int(data.get("exp", 0))
        if exp < now:
            return False

        # Optional user match guard
        if user_id and data.get("sub") != str(user_id):
            return False

        return True
    except Exception as e:
        logger.warning("Redis check_refresh_token_in_redis error for jti=%s: %s", jti, e)
        return None


async def revoke_refresh_token(jti: str, user_id: Optional[str] = None) -> bool:
    """
    Revoke a single refresh token in Redis map with 3600s tombstone TTL.
    """
    if jti in _memory_token_map:
        _memory_token_map[jti]["revoked"] = 1

    if user_id and str(user_id) in _memory_user_tokens:
        _memory_user_tokens[str(user_id)].pop(jti, None)

    client = await get_redis_client()
    if not client:
        return True

    try:
        refresh_key = f"vault:refresh:{jti}"
        async with client.pipeline(transaction=True) as pipe:
            pipe.hset(refresh_key, "revoked", "1")
            pipe.expire(refresh_key, 3600)  # 1 hour tombstone TTL
            if user_id:
                pipe.srem(f"vault:user_refresh_tokens:{user_id}", jti)
            await pipe.execute()
        return True
    except Exception as e:
        logger.error("Redis revoke_refresh_token failure for jti=%s: %s", jti, e)
        return False


async def revoke_all_user_refresh_tokens(user_id: str) -> bool:
    """
    Revoke all active refresh tokens for a specific user (password reset / account lock).
    Ensures tombstones have strict 3600-second TTLs.
    """
    user_id_str = str(user_id)
    if user_id_str in _memory_user_tokens:
        for jti in list(_memory_user_tokens[user_id_str].keys()):
            if jti in _memory_token_map:
                _memory_token_map[jti]["revoked"] = 1
        _memory_user_tokens.pop(user_id_str, None)

    client = await get_redis_client()
    if not client:
        return True

    try:
        user_key = f"vault:user_refresh_tokens:{user_id_str}"
        jtis = await client.smembers(user_key)
        if jtis:
            async with client.pipeline(transaction=True) as pipe:
                for jti in jtis:
                    pipe.hset(f"vault:refresh:{jti}", "revoked", "1")
                    pipe.expire(f"vault:refresh:{jti}", 3600)
                pipe.delete(user_key)
                await pipe.execute()
        return True
    except Exception as e:
        logger.error("Redis revoke_all_user_refresh_tokens failure for user=%s: %s", user_id_str, e)
        return False


async def get_refresh_token_metadata(jti: str) -> Optional[Dict[str, Any]]:
    """Retrieve refresh token details from Redis map or memory fallback."""
    if jti in _memory_token_map:
        return _memory_token_map[jti]

    client = await get_redis_client()
    if not client:
        return None

    try:
        return await client.hgetall(f"vault:refresh:{jti}")
    except Exception as e:
        logger.warning("Redis get_refresh_token_metadata failure for jti=%s: %s", jti, e)
        return None


# Backward compatibility aliases
store_token_mapping = store_refresh_token
store_refresh_token_in_redis = store_refresh_token
is_token_valid = check_refresh_token_in_redis
revoke_token = revoke_refresh_token
revoke_all_user_tokens = revoke_all_user_refresh_tokens
get_token_metadata = get_refresh_token_metadata


# --- Email Onboarding OTP Storage (Redis-backed with in-memory fallback) ---

def generate_numeric_otp(length: int = 6) -> str:
    """Generate a cryptographically secure N-digit numeric OTP (zero-padded)."""
    return f"{secrets.randbelow(10 ** length):0{length}d}"


def _hash_otp(email: str, otp_code: str) -> str:
    """Compute a salted SHA-256 digest of an OTP so plaintext codes are never stored."""
    normalized = f"{email.lower().strip()}:{otp_code.strip()}:{settings.SECRET_KEY}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def store_email_otp(
    email: str,
    otp_code: str,
    ttl_seconds: Optional[int] = None,
) -> bool:
    """Store a hashed 6-digit email verification OTP in Redis with strict TTL."""
    email_key = email.lower().strip()
    ttl = max(60, ttl_seconds if ttl_seconds is not None else settings.OTP_EXPIRE_MINUTES * 60)
    now = int(time.time())
    exp = now + ttl
    otp_hash = _hash_otp(email_key, otp_code)

    _memory_otp_map[email_key] = {
        "otp_hash": otp_hash,
        "attempts": 0,
        "created_at": now,
        "exp": exp,
    }

    client = await get_redis_client()
    if not client:
        return True

    try:
        redis_key = f"vault:email_otp:{email_key}"
        async with client.pipeline(transaction=True) as pipe:
            pipe.hset(
                redis_key,
                mapping={
                    "otp_hash": otp_hash,
                    "attempts": "0",
                    "created_at": str(now),
                    "exp": str(exp),
                },
            )
            pipe.expire(redis_key, ttl)
            await pipe.execute()
        return True
    except Exception as e:
        logger.error("Redis store_email_otp failure for email=%s: %s", email_key, e)
        return True


async def get_email_otp_resend_cooldown(
    email: str,
    cooldown_seconds: Optional[int] = None,
) -> int:
    """Return remaining seconds before another OTP may be dispatched (0 if allowed now)."""
    email_key = email.lower().strip()
    cooldown = cooldown_seconds if cooldown_seconds is not None else settings.OTP_RESEND_COOLDOWN_SECONDS
    now = int(time.time())

    client = await get_redis_client()
    created_at: Optional[int] = None

    if client:
        try:
            raw = await client.hget(f"vault:email_otp:{email_key}", "created_at")
            if raw is not None:
                created_at = int(raw)
        except Exception as e:
            logger.warning("Redis cooldown check error for email=%s: %s", email_key, e)

    if created_at is None and email_key in _memory_otp_map:
        mem = _memory_otp_map[email_key]
        if mem.get("exp", 0) > now:
            created_at = int(mem.get("created_at", 0))

    if not created_at:
        return 0

    elapsed = now - created_at
    return max(0, cooldown - elapsed)


async def verify_email_otp(
    email: str,
    otp_code: str,
    max_attempts: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    Verify a single-use 6-digit email OTP.
    Returns (True, "verified") on success, or (False, reason) where reason is one of:
      - "expired": no active OTP found or TTL elapsed
      - "max_attempts": too many incorrect guesses (OTP invalidated)
      - "invalid": incorrect code (attempt counter incremented)
    """
    email_key = email.lower().strip()
    limit = max_attempts if max_attempts is not None else settings.OTP_MAX_ATTEMPTS
    expected_hash = _hash_otp(email_key, otp_code)
    now = int(time.time())

    client = await get_redis_client()
    if client:
        redis_key = f"vault:email_otp:{email_key}"
        try:
            data = await client.hgetall(redis_key)
            if not data:
                _memory_otp_map.pop(email_key, None)
                return False, "expired"

            exp = int(data.get("exp", 0))
            if exp < now:
                await client.delete(redis_key)
                _memory_otp_map.pop(email_key, None)
                return False, "expired"

            attempts = int(data.get("attempts", 0))
            if attempts >= limit:
                await client.delete(redis_key)
                _memory_otp_map.pop(email_key, None)
                return False, "max_attempts"

            stored_hash = data.get("otp_hash", "")
            if hmac.compare_digest(stored_hash, expected_hash):
                await client.delete(redis_key)
                _memory_otp_map.pop(email_key, None)
                return True, "verified"

            new_attempts = await client.hincrby(redis_key, "attempts", 1)
            if new_attempts >= limit:
                await client.delete(redis_key)
                _memory_otp_map.pop(email_key, None)
                return False, "max_attempts"
            return False, "invalid"
        except Exception as e:
            logger.warning("Redis verify_email_otp failure for email=%s: %s", email_key, e)

    # In-memory fallback when Redis is unavailable
    mem = _memory_otp_map.get(email_key)
    if not mem or mem.get("exp", 0) < now:
        _memory_otp_map.pop(email_key, None)
        return False, "expired"

    if mem.get("attempts", 0) >= limit:
        _memory_otp_map.pop(email_key, None)
        return False, "max_attempts"

    if hmac.compare_digest(mem.get("otp_hash", ""), expected_hash):
        _memory_otp_map.pop(email_key, None)
        return True, "verified"

    mem["attempts"] = mem.get("attempts", 0) + 1
    if mem["attempts"] >= limit:
        _memory_otp_map.pop(email_key, None)
        return False, "max_attempts"
    return False, "invalid"


async def get_email_otp_resend_count(email: str) -> int:
    """Return how many times an OTP resend has been requested for this email in the current window."""
    email_key = email.lower().strip()
    now = int(time.time())

    client = await get_redis_client()
    if client:
        try:
            raw = await client.get(f"vault:email_otp_resends:{email_key}")
            if raw is not None:
                return int(raw)
        except Exception as e:
            logger.warning("Redis get_email_otp_resend_count error for email=%s: %s", email_key, e)

    mem = _memory_otp_resends.get(email_key)
    if mem and mem.get("exp", 0) > now:
        return int(mem.get("count", 0))
    _memory_otp_resends.pop(email_key, None)
    return 0


async def increment_email_otp_resend_count(
    email: str,
    window_seconds: Optional[int] = None,
) -> int:
    """Increment and return the resend counter for this email (with strict TTL)."""
    email_key = email.lower().strip()
    ttl = window_seconds if window_seconds is not None else settings.OTP_RESEND_WINDOW_SECONDS
    now = int(time.time())

    # Update local fallback
    mem = _memory_otp_resends.get(email_key)
    if mem and mem.get("exp", 0) > now:
        mem["count"] = int(mem.get("count", 0)) + 1
    else:
        _memory_otp_resends[email_key] = {"count": 1, "exp": now + ttl}

    client = await get_redis_client()
    if not client:
        return _memory_otp_resends[email_key]["count"]

    try:
        redis_key = f"vault:email_otp_resends:{email_key}"
        new_count = await client.incr(redis_key)
        if new_count == 1:
            await client.expire(redis_key, ttl)
        return int(new_count)
    except Exception as e:
        logger.warning("Redis increment_email_otp_resend_count error for email=%s: %s", email_key, e)
        return _memory_otp_resends[email_key]["count"]


async def clear_email_otp_resend_count(email: str) -> None:
    """Reset the resend counter once the user completes email verification."""
    email_key = email.lower().strip()
    _memory_otp_resends.pop(email_key, None)

    client = await get_redis_client()
    if not client:
        return
    try:
        await client.delete(f"vault:email_otp_resends:{email_key}")
    except Exception as e:
        logger.warning("Redis clear_email_otp_resend_count error for email=%s: %s", email_key, e)


# ---------------------------------------------------------------------------
# Fast Redis Caching Layer for AI Tokens, User Settings, and Vault File Info
# ---------------------------------------------------------------------------

def make_user_tokens_cache_key(user_id: str) -> str:
    return f"vault:cache:user_tokens:{user_id}"


def make_user_settings_cache_key(user_id: str) -> str:
    return f"vault:cache:user_settings:{user_id}"


def make_user_files_cache_key(user_id: str) -> str:
    return f"vault:cache:user_files:{user_id}"


async def get_json_cache(key: str) -> Optional[Any]:
    """Retrieve JSON-deserialized value from Redis cache (<1ms response)."""
    client = await get_redis_client()
    if not client:
        return None
    try:
        val = await client.get(key)
        if val is not None:
            return json.loads(val)
    except Exception as exc:
        logger.warning("Redis get_json_cache failed for %s: %s", key, exc)
    return None


async def set_json_cache(key: str, data: Any, ttl_seconds: int = 300) -> bool:
    """Store JSON-serialized value in Redis cache with strict TTL."""
    client = await get_redis_client()
    if not client:
        return False
    try:
        serialized = json.dumps(data)
        await client.set(key, serialized, ex=ttl_seconds)
        return True
    except Exception as exc:
        logger.warning("Redis set_json_cache failed for %s: %s", key, exc)
        return False


async def get_text_cache(key: str) -> Optional[str]:
    """Retrieve raw text (e.g. markdown memory) from Redis cache (<1ms response)."""
    client = await get_redis_client()
    if not client:
        return None
    try:
        val = await client.get(key)
        return str(val) if val is not None else None
    except Exception as exc:
        logger.warning("Redis get_text_cache failed for %s: %s", key, exc)
    return None


async def set_text_cache(key: str, data: str, ttl_seconds: int = 3600) -> bool:
    """Store raw text string in Redis cache with TTL (defaults to 1 hour)."""
    client = await get_redis_client()
    if not client:
        return False
    try:
        await client.set(key, str(data), ex=ttl_seconds)
        return True
    except Exception as exc:
        logger.warning("Redis set_text_cache failed for %s: %s", key, exc)
        return False


async def delete_cache(key: str) -> bool:
    """Evict a specific key from Redis cache."""
    client = await get_redis_client()
    if not client:
        return False
    try:
        await client.delete(key)
        return True
    except Exception as exc:
        logger.warning("Redis delete_cache failed for %s: %s", key, exc)
        return False


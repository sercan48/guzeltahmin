import pytest
import time
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from shared.security import generate_signature, verify_signature
from shared.http_client import ResilientHttpClient, CircuitBreakerOpen, CircuitState
from shared.redis_lock import DistributedLock, LockAcquisitionError


# --- TASK 1: HMAC Request Signing Tests ---

def test_hmac_signature_generation_and_verification():
    secret = "test-secret"
    method = "POST"
    path = "/api/v1/payments/mock"
    body = '{"telegram_id":123,"package_id":1,"idempotency_key":"test_key"}'
    timestamp = str(int(time.time()))
    client_id = "bot-service"

    # Generate signature
    sig = generate_signature(secret, method, path, body, timestamp, client_id)
    assert len(sig) == 64  # Hex HMAC-SHA256 length is 64 characters

    # Verify signature - valid case
    is_valid, err_msg = verify_signature(secret, method, path, body, timestamp, client_id, sig)
    assert is_valid is True
    assert err_msg == ""

    # Verify signature - expired timestamp
    expired_timestamp = str(int(time.time()) - 100)
    sig_expired = generate_signature(secret, method, path, body, expired_timestamp, client_id)
    is_valid, err_msg = verify_signature(secret, method, path, body, expired_timestamp, client_id, sig_expired)
    assert is_valid is False
    assert "expired" in err_msg.lower()

    # Verify signature - wrong secret
    is_valid, err_msg = verify_signature("wrong-secret", method, path, body, timestamp, client_id, sig)
    assert is_valid is False
    assert "invalid hmac" in err_msg.lower()

    # Verify signature - tampered body
    is_valid, err_msg = verify_signature(secret, method, path, body + "tampered", timestamp, client_id, sig)
    assert is_valid is False
    assert "invalid hmac" in err_msg.lower()


# --- TASK 2: Resilient HTTP Client Tests ---

@pytest.mark.asyncio
async def test_resilient_http_client_success():
    client = ResilientHttpClient(base_url="http://mock-api")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"status": "success"}

    with patch.object(client, "_do_request", new_callable=AsyncMock) as mock_do_request:
        mock_do_request.return_value = mock_response
        result = await client.request("GET", "/api/v1/packages")
        assert result == {"status": "success"}
        mock_do_request.assert_called_once()


@pytest.mark.asyncio
async def test_resilient_http_client_retries_on_5xx():
    client = ResilientHttpClient(base_url="http://mock-api", max_retries=3, timeout=1.0)
    
    # 5xx responses (retryable)
    from httpx import HTTPStatusError, Request, Response
    request = Request("POST", "http://mock-api/api/v1/payments/mock")
    response_500 = Response(500, request=request)
    exc = HTTPStatusError("Internal Server Error", request=request, response=response_500)

    # Mock asyncio.sleep to run instantly
    with patch.object(client, "_do_request", new_callable=AsyncMock) as mock_do_request, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        
        mock_do_request.side_effect = exc
        result = await client.request("POST", "/api/v1/payments/mock", json_data={"foo": "bar"})
        
        assert result is None
        assert mock_do_request.call_count == 3
        assert mock_sleep.call_count == 2


@pytest.mark.asyncio
async def test_resilient_http_client_circuit_breaker():
    client = ResilientHttpClient(
        base_url="http://mock-api", 
        max_retries=1, 
        circuit_threshold=3, 
        circuit_timeout=10.0
    )
    
    from httpx import ConnectError, Request
    request = Request("GET", "http://mock-api/api/v1/packages")
    exc = ConnectError("Connection refused", request=request)

    with patch.object(client, "_do_request", new_callable=AsyncMock) as mock_do_request:
        mock_do_request.side_effect = exc

        # First 3 attempts trigger failure count increments (max_retries=1 means 1 attempt per request call)
        # Call 1
        await client.request("GET", "/api/v1/packages")
        assert client.circuit_state == CircuitState.CLOSED

        # Call 2
        await client.request("GET", "/api/v1/packages")
        assert client.circuit_state == CircuitState.CLOSED

        # Call 3 -> reaches threshold (3 failures) -> opens circuit
        await client.request("GET", "/api/v1/packages")
        assert client.circuit_state == CircuitState.OPEN

        # Call 4 -> should raise CircuitBreakerOpen immediately without hitting mock_do_request
        mock_do_request.reset_mock()
        with pytest.raises(CircuitBreakerOpen):
            await client.request("GET", "/api/v1/packages")
        
        mock_do_request.assert_not_called()


# --- TASK 3: Redis Distributed Lock Tests ---

@pytest.mark.asyncio
async def test_redis_distributed_lock_acquire_and_release():
    redis_url = "redis://mock-redis:6379/0"
    lock_name = "test_expire_check"
    
    lock = DistributedLock(redis_url, lock_name, ttl=60)
    
    mock_redis = AsyncMock()
    mock_redis.set.return_value = True  # SET NX EX success
    mock_redis.eval.return_value = 1    # Release Lua script success

    with patch.object(lock, "_get_client", new_callable=AsyncMock) as mock_get_client:
        mock_get_client.return_value = mock_redis
        
        # Acquire
        success = await lock.acquire()
        assert success is True
        mock_redis.set.assert_called_once_with(
            "lock:test_expire_check", 
            lock._token, 
            nx=True, 
            ex=60
        )
        
        # Release
        await lock.release()
        mock_redis.eval.assert_called_once()
        assert lock._acquired is False


@pytest.mark.asyncio
async def test_redis_distributed_lock_acquisition_failure():
    redis_url = "redis://mock-redis:6379/0"
    lock_name = "test_expire_check"
    
    lock = DistributedLock(redis_url, lock_name, ttl=60)
    
    mock_redis = AsyncMock()
    mock_redis.set.return_value = None  # SET NX EX returns None when key exists (lock held)

    with patch.object(lock, "_get_client", new_callable=AsyncMock) as mock_get_client:
        mock_get_client.return_value = mock_redis
        
        with pytest.raises(LockAcquisitionError):
            await lock.acquire()

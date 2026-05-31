import asyncio
import json
import logging
import time
import traceback
from typing import Callable, Any
import redis.asyncio as aioredis
from workers.config import settings
from shared.redis_lock import DistributedLock, LockAcquisitionError

logger = logging.getLogger("worker_runner")


async def push_to_dlq(redis_url: str, job_name: str, error: str, traceback_str: str):
    """Pushes failed job information into the worker DLQ list."""
    try:
        redis_client = aioredis.from_url(redis_url, decode_responses=True)
        dlq_payload = {
            "job_name": job_name,
            "error": error,
            "traceback": traceback_str,
            "timestamp": int(time.time())
        }
        await redis_client.lpush("dlq:worker_jobs", json.dumps(dlq_payload))
        await redis_client.incr(f"metrics:counter:worker_dlq_total:{job_name}")
        await redis_client.aclose()
        logger.warning(f"DLQ: Job {job_name} failure payload pushed to dlq:worker_jobs")
    except Exception as ex:
        logger.error(f"Failed to push failure payload to DLQ: {ex}")


async def run_job_with_retry(
    job_fn: Callable[..., Any],
    job_name: str,
    lock_name: str,
    *args,
    max_retries: int = 3,
    **kwargs
):
    """Executes a worker job with distributed locks, metrics recording, and backoff retries."""
    logger.info(f"Triggering job {job_name}...")
    
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    
    try:
        async with DistributedLock(settings.REDIS_URL, lock_name) as lock:
            attempt = 0
            backoff = 5.0
            
            while attempt <= max_retries:
                try:
                    start_time = time.time()
                    
                    if asyncio.iscoroutinefunction(job_fn):
                        await job_fn(*args, **kwargs)
                    else:
                        job_fn(*args, **kwargs)
                        
                    duration = time.time() - start_time
                    logger.info(f"Job {job_name} completed successfully in {duration:.2f}s.")
                    
                    # Log success metrics in Redis
                    await redis_client.incr(f"metrics:counter:worker_job_success_rate:{job_name}:success")
                    await redis_client.set(f"metrics:latency:worker_{job_name}", str(duration))
                    return
                except Exception as e:
                    attempt += 1
                    err_msg = str(e)
                    tb_str = traceback.format_exc()
                    
                    logger.error(f"Job {job_name} failed on attempt {attempt}/{max_retries + 1}: {err_msg}")
                    await redis_client.incr(f"metrics:counter:worker_retry_count:{job_name}")
                    
                    if attempt > max_retries:
                        await push_to_dlq(settings.REDIS_URL, job_name, err_msg, tb_str)
                        await redis_client.incr(f"metrics:counter:worker_job_success_rate:{job_name}:failure")
                        break
                        
                    logger.info(f"Sleeping {backoff} seconds before retry...")
                    await asyncio.sleep(backoff)
                    backoff *= 3.0
    except LockAcquisitionError:
        logger.warning(f"Could not acquire lock '{lock_name}' for job {job_name}. Skipping run.")
    except Exception as unexpected:
        logger.error(f"Unexpected error in job {job_name} runner: {unexpected}")
    finally:
        await redis_client.aclose()

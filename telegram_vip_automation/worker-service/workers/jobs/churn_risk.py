import logging
from workers.client.api_client import worker_api_client
from shared.redis_lock import DistributedLock, LockAcquisitionError
from workers.config import settings

logger = logging.getLogger(__name__)


async def run_churn_risk_calculator():
    """
    Worker Job:
    1. Acquires a Redis distributed lock to prevent concurrent runs across instances.
    2. Calls the API Service to trigger churn risk calculation for all users.
    """
    logger.info("Starting background churn risk calculator...")
    
    try:
        async with DistributedLock(settings.REDIS_URL, "churn_risk_check") as lock:
            logger.info("Lock 'churn_risk_check' acquired. Triggering calculation...")
            
            success = await worker_api_client.trigger_churn_risk_calculation()
            if success:
                logger.info("Churn risk calculation triggered successfully.")
            else:
                logger.error("Failed to complete churn risk calculation via API.")
                
            logger.info("Churn risk calculation job run completed.")
            
    except LockAcquisitionError:
        logger.warning("Could not acquire distributed lock 'churn_risk_check'. Another worker instance is running. Skipping this run.")
    except Exception as e:
        logger.error(f"Unexpected error during churn risk calculation worker job: {e}")

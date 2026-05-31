import logging
import asyncio
from aiogram import Bot
from workers.client.api_client import worker_api_client

logger = logging.getLogger(__name__)


async def send_automated_renewal_campaigns(bot: Bot):
    """
    Worker Job:
    1. Fetches pending renewal notifications from API Service.
    2. Sends the appropriate campaign message to the user via Telegram DM.
    3. Logs the sent notification in the API database to prevent duplicates.
    """
    logger.info("Starting background renewal campaigns processing...")

    try:
        pending_campaigns = await worker_api_client.get_pending_campaigns()
        
        if not pending_campaigns:
            logger.info("No pending renewal notifications to send.")
            return

        logger.info(f"Found {len(pending_campaigns)} pending notifications to send.")

        for index, camp in enumerate(pending_campaigns):
            sub_id = camp.get("subscription_id")
            user_id = camp.get("user_id")
            telegram_id = camp.get("telegram_id")
            notif_type = camp.get("notification_type")
            message = camp.get("message")

            if not sub_id or not user_id or not telegram_id or not notif_type or not message:
                continue

            logger.info(f"Sending campaign {notif_type} to user {telegram_id} (Sub ID: {sub_id})")

            # Send Telegram DM
            sent_success = False
            try:
                await bot.send_message(
                    chat_id=telegram_id,
                    text=message,
                    parse_mode="Markdown"
                )
                logger.info(f"Sent campaign {notif_type} DM to user {telegram_id}")
                sent_success = True
            except Exception as bot_err:
                logger.error(f"Could not send campaign DM to user {telegram_id}: {bot_err}")

            # If sent successfully, log it to database to mark as sent
            if sent_success:
                try:
                    logged = await worker_api_client.log_campaign(
                        subscription_id=sub_id,
                        user_id=user_id,
                        notification_type=notif_type
                    )
                    if not logged:
                        logger.error(f"Failed to log sent campaign notification in database for user {telegram_id}")
                except Exception as db_err:
                    logger.error(f"Error logging sent campaign for user {telegram_id}: {db_err}")

            # Sleep 1 second between DMs to comply with Telegram rate limits
            if index < len(pending_campaigns) - 1:
                await asyncio.sleep(1)

        # 2. Process new event-driven campaign executions
        try:
            pending_execs = await worker_api_client.get_pending_campaign_executions()
        except Exception as exc:
            logger.error(f"Failed to fetch pending campaign executions: {exc}")
            pending_execs = []

        if pending_execs:
            logger.info(f"Found {len(pending_execs)} pending campaign executions to process.")
            for index, exec_item in enumerate(pending_execs):
                exec_id = exec_item.get("execution_id")
                telegram_id = exec_item.get("telegram_id")
                message = exec_item.get("message")

                if not exec_id or not telegram_id or not message:
                    continue

                logger.info(f"Sending campaign execution {exec_id} to user {telegram_id}")

                sent_success = False
                try:
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=message,
                        parse_mode="Markdown"
                    )
                    logger.info(f"Sent campaign execution {exec_id} DM to user {telegram_id}")
                    sent_success = True
                except Exception as bot_err:
                    logger.error(f"Could not send campaign execution DM to user {telegram_id}: {bot_err}")

                # Complete the execution record in API service
                status = "sent" if sent_success else "failed"
                try:
                    await worker_api_client.complete_campaign_execution(exec_id, status)
                except Exception as db_err:
                    logger.error(f"Error completing campaign execution {exec_id}: {db_err}")

                # Sleep 1 second to comply with Telegram rate limits
                if index < len(pending_execs) - 1:
                    await asyncio.sleep(1)

        logger.info("Renewal campaigns processing completed.")

    except Exception as e:
        logger.error(f"Unexpected error during renewal campaigns worker job: {e}")

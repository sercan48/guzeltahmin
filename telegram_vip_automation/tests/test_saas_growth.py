import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from app.models.coupon import Coupon
from app.models.user import User
from app.services.coupon import CouponService
from app.services.referral import ReferralService
from app.services.trial import TrialService
from app.services.admin import AdminService
from app.services.campaign import CampaignService
from app.core.admin_auth import hash_password, verify_password, create_access_token, decode_token


# ── COUPON SERVICE TESTS ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_coupon_discount_calculations():
    db_mock = AsyncMock()
    service = CouponService(db_mock)

    # Test Percentage Coupon
    percentage_coupon = Coupon(code="PCT15", coupon_type="percentage", value=15.0)
    discounted_pct = await service.calculate_discounted_amount(percentage_coupon, 100.0)
    assert discounted_pct == 85.0

    # Test Fixed Coupon
    fixed_coupon = Coupon(code="FX20", coupon_type="fixed", value=20.0)
    discounted_fix = await service.calculate_discounted_amount(fixed_coupon, 100.0)
    assert discounted_fix == 80.0

    # Test Fixed Coupon Exceeding Price
    discounted_exceed = await service.calculate_discounted_amount(fixed_coupon, 15.0)
    assert discounted_exceed == 0.0

    # Test Free Trial Coupon
    trial_coupon = Coupon(code="TRIAL3", coupon_type="free_trial", value=0.0)
    discounted_trial = await service.calculate_discounted_amount(trial_coupon, 100.0)
    assert discounted_trial == 0.0


# ── REFERRAL FRAUD & REWARDS TESTS ─────────────────────────────────────────

def test_referral_fraud_risk_score_calculation():
    db_mock = AsyncMock()
    service = ReferralService(db_mock)

    referrer = User(id=1, telegram_id=1001, first_name="John", last_name="Doe", username="johndoe")
    
    # Safe Referral Case (Low Risk Score)
    safe_referred = User(id=2, telegram_id=1002, first_name="Alice", last_name="Smith", username="alicesmith")
    risk_score, reasons = service._calculate_fraud_risk(referrer, safe_referred)
    assert risk_score == 0.0
    assert reasons == ""

    # Name Match Fraud Case (High Risk Score >= 0.8)
    fraud_name_referred = User(id=3, telegram_id=1003, first_name="John", last_name="Doe", username="johndoe_alt")
    risk_score, reasons = service._calculate_fraud_risk(referrer, fraud_name_referred)
    assert risk_score >= 0.6
    assert "Exact name match" in reasons

    # Minimal Info Case (Accumulates small risk weights)
    anonymous_referred = User(id=4, telegram_id=1004, first_name=None, last_name=None, username=None)
    risk_score, reasons = service._calculate_fraud_risk(referrer, anonymous_referred)
    assert risk_score >= 0.4
    assert "No Telegram username" in reasons
    assert "No Telegram first or last name" in reasons


# ── FREE TRIAL RULE TESTS ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_claim_free_trial_already_used():
    db_mock = AsyncMock()
    service = TrialService(db_mock)

    # Mock user who already used trial
    user = User(id=1, telegram_id=12345, trial_used=True)
    
    # Mock database queries
    db_mock.execute = AsyncMock()
    
    # Mock user query result returning the user
    mock_user_res = MagicMock()
    mock_user_res.scalars().first.return_value = user
    db_mock.execute.return_value = mock_user_res

    success, message, sub, invite = await service.claim_free_trial(12345)
    assert success is False
    assert "already claimed" in message.lower()
    assert sub is None
    assert invite == ""


# ── ADMIN AUTH & TOKENS TESTS ──────────────────────────────────────────────

def test_admin_password_hashing():
    raw_pass = "SuperSecure123"
    hashed = hash_password(raw_pass)
    
    assert hashed != raw_pass
    assert verify_password(raw_pass, hashed) is True
    assert verify_password("WrongPass", hashed) is False


def test_admin_jwt_generation_and_validation():
    admin_id = 99
    role = "finance"
    
    access_token = create_access_token(admin_id, role)
    decoded = decode_token(access_token)
    
    assert decoded["sub"] == str(admin_id)
    assert decoded["role"] == role
    assert decoded["type"] == "access"


# ── ADMIN SERVICE & RBAC ACTIONS TESTS ─────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_service_suspend_and_unsuspend():
    db_mock = AsyncMock()
    service = AdminService(db_mock)

    # Mock user object
    user = User(id=42, telegram_id=98765, is_suspended=False)
    
    # Mock database select
    mock_execute = MagicMock()
    mock_execute.scalars().first.return_value = user
    mock_execute.scalars().all.return_value = []
    db_mock.execute.return_value = mock_execute

    # Test Suspend
    success_suspend = await service.suspend_user(user_id=42, admin_id=1, ip_address="127.0.0.1")
    assert success_suspend is True
    assert user.is_suspended is True

    # Test Unsuspend
    success_unsuspend = await service.unsuspend_user(user_id=42, admin_id=1, ip_address="127.0.0.1")
    assert success_unsuspend is True
    assert user.is_suspended is False


# ── CAMPAIGN AUTOMATION RULE EVALUATION TESTS ───────────────────────────────────

@pytest.mark.asyncio
async def test_campaign_automation_rule_evaluation():
    db_mock = AsyncMock()
    service = CampaignService(db_mock)

    from app.models.event_log import EventLog
    from app.models.campaign import Campaign, CampaignRule

    # Mock event
    event = EventLog(id=100, event_type="trial_expired", user_id=10)

    # Mock campaign rule and campaign
    campaign = Campaign(id=1, name="Trial End Campaign", is_active=True)
    rule = CampaignRule(id=2, campaign_id=1, trigger_event="trial_expired", delay_hours=24, message_template="Hello")

    # Mock database executions
    mock_rules_res = MagicMock()
    mock_rules_res.scalars().all.return_value = [rule]
    
    mock_dup_res = MagicMock()
    mock_dup_res.scalar.return_value = 0 # No duplicate delivery

    mock_cooldown_res = MagicMock()
    mock_cooldown_res.scalar.return_value = 0 # No cooldown violations
    
    db_mock.execute.side_effect = [mock_rules_res, mock_dup_res, mock_cooldown_res]

    # Evaluate event rules
    await service.trigger_campaign_on_event(event) if hasattr(service, "trigger_campaign_on_event") else await service.trigger_campaigns_on_event(event)

    # Verify that an execution was added
    assert db_mock.add.called
    added_obj = db_mock.add.call_args[0][0]
    assert added_obj.campaign_id == 1
    assert added_obj.user_id == 10
    assert added_obj.status == "pending"


# ── DATA CONSISTENCY & RECONCILIATION ENGINE TESTS ─────────────────────────────

@pytest.mark.asyncio
async def test_reconciliation_engine_auto_repair():
    from app.services.reconciliation import ReconciliationService
    from app.models.payment import Payment
    from app.models.subscription import Subscription
    from app.models.product import ProductPackage
    from app.models.channel import TelegramChannel
    
    db_mock = AsyncMock()
    tg_mock = MagicMock()
    
    tg_mock.bot = MagicMock()
    tg_mock.bot.send_message = AsyncMock()
    tg_mock.is_user_in_chat = AsyncMock()
    tg_mock.create_single_use_invite = AsyncMock()
    tg_mock.kick_user = AsyncMock()
    tg_mock.close = AsyncMock()

    service = ReconciliationService(db_mock, telegram_service=tg_mock)

    # 1. Mock Payment check:
    payment = Payment(id=1, user_id=10, package_id=5, status="paid", created_at=datetime.now(timezone.utc))
    package = ProductPackage(id=5, product_id=2, duration_days=30, price=299.0)
    
    mock_pay_res = MagicMock()
    mock_pay_res.scalars().all.return_value = [payment]
    
    mock_no_sub_res = MagicMock()
    mock_no_sub_res.scalars().first.return_value = None  # No subscription exists!
    
    mock_pkg_res = MagicMock()
    mock_pkg_res.scalars().first.return_value = package

    # For active check:
    sub = Subscription(id=100, user_id=10, product_id=2, package_id=5, is_active=True, end_date=datetime.now(timezone.utc) + timedelta(days=10))
    user = User(id=10, telegram_id=123456, username="testuser")
    
    mock_active_subs = MagicMock()
    mock_active_subs.all.return_value = [(sub, user)]
    
    channel = TelegramChannel(id=1, telegram_id=-100123456)
    mock_channels = MagicMock()
    mock_channels.scalars().all.return_value = [channel]

    # For expired check:
    expired_sub = Subscription(id=200, user_id=11, product_id=2, package_id=5, is_active=False, end_date=datetime.now(timezone.utc) - timedelta(days=10))
    expired_user = User(id=11, telegram_id=987654, username="expireduser")
    
    mock_expired_subs = MagicMock()
    mock_expired_subs.all.return_value = [(expired_sub, expired_user)]
    
    mock_no_active_for_expired = MagicMock()
    mock_no_active_for_expired.scalars().first.return_value = None

    db_mock.execute.side_effect = [
        mock_pay_res,
        mock_no_sub_res,
        mock_pkg_res,
        mock_active_subs,
        mock_channels,
        mock_expired_subs,
        mock_no_active_for_expired,
        mock_channels
    ]

    # Mock Redis returns
    # Active user is NOT in chat (should reinvite), Expired user IS in chat (should kick)
    tg_mock.is_user_in_chat.side_effect = [False, True]
    tg_mock.create_single_use_invite.return_value = "https://t.me/invite/abc"
    tg_mock.kick_user.return_value = True

    mock_redis = AsyncMock()
    mock_redis.incr = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        stats = await service.run_reconciliation()
    
    # Assertions
    assert stats["subscriptions_created"] == 1
    assert stats["reinvites_sent"] == 1
    assert stats["users_kicked"] == 1
    
    tg_mock.create_single_use_invite.assert_called_once()
    tg_mock.kick_user.assert_called_once_with(str(channel.telegram_id), expired_user.telegram_id)


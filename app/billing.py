import json
import os
import time

import requests
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import administrators_collection, users_collection, plans_collection
from .models import mirror_collection, sites_collection

DEFAULT_BIGBURRY_GATEWAY_URL = "https://ppgmyyxzrlemjgegnbou.supabase.co/functions/v1/payment-service"
DEFAULT_BIGBURRY_PAYMENTS_WEB_URL = "https://payments.bigburry.com"
SOURCE_APP_NAME = os.environ.get("BIGBURRY_SOURCE_APP", "mirrorlink").strip() or "mirrorlink"

PLAN = {
    "plan_id": "mirrorlink-business-monthly",
    "name": "MirrorLink Business",
    "amount": 12999.00,  # in INR
    "currency": "INR",
    "period": "monthly",
    "interval": 1,
    "total_count": 120,
    "sites": 10,
    "mirrors_per_site": 50,
}
plans_collection.update_one(
    {"name": PLAN["name"]},
    {"$set": {**PLAN, "updated_at": int(time.time())}, "$setOnInsert": {"created_at": int(time.time())}},
    upsert=True
)


def get_bigburry_config():
    session_url = os.environ.get(
        "BIGBURRY_PAYMENT_SERVICE_URL",
        f"{DEFAULT_BIGBURRY_GATEWAY_URL}/create-session"
    ).strip()
    verify_url = os.environ.get(
        "BIGBURRY_VERIFY_SERVICE_URL",
        f"{DEFAULT_BIGBURRY_GATEWAY_URL}/verify-transaction"
    ).strip()
    web_url = os.environ.get("PAYMENTS_WEB_URL", DEFAULT_BIGBURRY_PAYMENTS_WEB_URL).strip().rstrip("/")
    source_app = SOURCE_APP_NAME
    return session_url, verify_url, web_url, source_app


def admin_from_key(login_key):
    if not login_key:
        return None
    return administrators_collection.find_one({"login_key": login_key})


def administrator_ids(administrator_id):
    return [administrator_id, str(administrator_id)]


def count_administrator_sites(administrator_id):
    return sites_collection.count_documents({"administrator_id": {"$in": administrator_ids(administrator_id)}})


def count_administrator_mirrors(administrator_id):
    return mirror_collection.count_documents({"administrator_id": {"$in": administrator_ids(administrator_id)}})


def error_response(error, status):
    return Response({"status_text": error, "status_code": status}, status=status)


def activate_user_subscription(admin_id, order_id=None, payment_id=None, metadata=None):
    """Mark an administrator account active for the current billing cycle."""
    now = int(time.time())
    one_month_later = now + (30 * 86400)
    metadata = metadata or {}
    
    sub_data = {
        "plan_name": PLAN["name"],
        "plan_id": PLAN["plan_id"],
        "status": "active",
        "current_start": now,
        "current_end": one_month_later,
        "charge_at": one_month_later,
        "source_app": SOURCE_APP_NAME,
        "order_id": order_id or metadata.get("order_id"),
        "payment_id": payment_id or metadata.get("payment_id"),
        "last_payment_at": now,
        "updated_at": now,
    }
    
    administrators_collection.update_one(
        {"_id": admin_id},
        {"$set": {"subscription": sub_data}}
    )
    users_collection.update_one(
        {"_id": admin_id},
        {"$set": {"subscription": sub_data}},
        upsert=True
    )
    return sub_data


class Billing(APIView):
    """Handle subscription sessions and verification through BigBurry Payments."""

    def post(self, request):
        service = request.data.get("service")

        if service == "get_subscription":
            admin = admin_from_key(request.data.get("login_key"))
            if not admin:
                return error_response("Please sign in to view subscription details.", 401)
            saved = dict(admin.get("subscription", {}))
            is_active = saved.get("status") == "active" and bool(saved.get("plan_id"))

            # Check if active subscription has passed current_end
            current_end = saved.get("current_end")
            if is_active and current_end and current_end < int(time.time()):
                # Auto expire if past renewal window
                saved["status"] = "expired"
                is_active = False
                administrators_collection.update_one(
                    {"_id": admin["_id"]},
                    {"$set": {"subscription.status": "expired"}}
                )
                users_collection.update_one(
                    {"_id": admin["_id"]},
                    {"$set": {"subscription.status": "expired"}}
                )

            return Response({
                "status_code": 200,
                "plan_active": is_active,
                "plan": PLAN,
                "subscription": saved,
            })

        if service == "create_subscription":
            admin = admin_from_key(request.data.get("login_key"))
            if not admin:
                return error_response("Sign in to your MirrorLink account before subscribing.", 401)
            saved = admin.get("subscription", {})
            if saved.get("status") == "active" and (saved.get("current_end", 0) > int(time.time())):
                return Response({
                    "status_text": "Your MirrorLink subscription is already active.",
                    "status_code": 200,
                    "subscription_status": "active"
                })

            session_url, _, web_url, source_app = get_bigburry_config()
            customer_name = admin.get("username") or admin.get("email_id") or "MirrorLink User"
            customer_email = admin.get("email_id") or admin.get("email") or ""

            payload = {
                "sourceApp": source_app,
                "amount": float(PLAN["amount"]),
                "currency": PLAN["currency"],
                "productName": PLAN["name"],
                "customerName": customer_name,
                "customerEmail": customer_email,
            }

            try:
                resp = requests.post(session_url, json=payload, timeout=12)
                resp.raise_for_status()
                data = resp.json()
                session_id = data.get("sessionId")
                if not session_id:
                    return error_response("BigBurry Payments did not return a valid session ID.", 502)

                checkout_url = f"{web_url}/?session_id={session_id}"
                return Response({
                    "status_code": 200,
                    "session_id": session_id,
                    "checkout_url": checkout_url,
                    "plan": PLAN,
                })
            except requests.RequestException as e:
                return error_response(f"Could not reach BigBurry payment server: {str(e)}", 502)

        if service == "verify_subscription":
            admin = admin_from_key(request.data.get("login_key"))
            if not admin:
                return error_response("Sign in to your MirrorLink account before verifying payment.", 401)

            order_id = request.data.get("order_id") or request.data.get("orderId")
            payment_id = request.data.get("payment_id") or request.data.get("paymentId")
            signature = request.data.get("signature") or request.data.get("razorpay_signature")

            if not order_id or not payment_id:
                return error_response("order_id and payment_id are required for verification.", 400)

            # Replay defense check: verify order_id not consumed already by another user
            existing = administrators_collection.find_one({"subscription.order_id": order_id})
            if existing and existing["_id"] != admin["_id"]:
                return error_response("This payment order has already been consumed by another account.", 409)

            _, verify_url, _, source_app = get_bigburry_config()

            # Local development mock support as specified in BigBurry payments guide
            is_dev_mock = os.environ.get("ENV") != "production" and str(order_id).startswith("mock_order_")
            verified = is_dev_mock

            if not is_dev_mock:
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        verify_res = requests.get(
                            f"{verify_url}?orderId={requests.utils.quote(str(order_id))}",
                            timeout=10,
                        )
                        if verify_res.ok:
                            verify_data = verify_res.json()
                            if verify_data.get("status") == "success":
                                resp_source = verify_data.get("sourceApp")
                                if resp_source and resp_source != source_app:
                                    return error_response(f"Cross-application payment mismatch: {resp_source}", 403)
                                verified = True
                                break
                    except requests.RequestException:
                        pass
                    if attempt < max_retries - 1:
                        time.sleep(1.5)

            if not verified:
                return error_response("Payment was not confirmed by the BigBurry payment gateway.", 402)

            sub_data = activate_user_subscription(
                admin["_id"],
                order_id=order_id,
                payment_id=payment_id,
                metadata={"signature": signature}
            )

            return Response({
                "status_code": 200,
                "status_text": "Subscription payment confirmed and activated.",
                "subscription_status": "active",
                "subscription": sub_data,
            })

        return error_response("Unknown billing service.", 400)


class BigBurryWebhook(APIView):
    """Receive payment status notifications from BigBurry Payments Server."""
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        try:
            payload = request.data if isinstance(request.data, dict) else json.loads(request.body.decode("utf-8"))
            event_type = payload.get("type") or payload.get("event")
            status = payload.get("status")
            order_id = payload.get("orderId") or payload.get("order_id")
            payment_id = payload.get("paymentId") or payload.get("payment_id")
            source_app = payload.get("sourceApp") or payload.get("source_app")
            customer_email = payload.get("customerEmail") or payload.get("customer_email") or payload.get("email")

            # Validate source application matches MirrorLink
            if source_app and source_app != SOURCE_APP_NAME:
                return error_response("Source app mismatch.", 403)

            is_success = (
                event_type in ("PAYMENT_SUCCESS", "payment.captured", "payment.success")
                or status in ("success", "paid", "captured")
            )

            if is_success and (order_id or customer_email):
                query = {}
                if order_id:
                    query = {"subscription.order_id": order_id}
                elif customer_email:
                    query = {"email_id": customer_email.lower().strip()}

                admin = administrators_collection.find_one(query)
                if not admin and customer_email:
                    admin = administrators_collection.find_one({"email_id": customer_email.lower().strip()})

                if admin:
                    activate_user_subscription(
                        admin["_id"],
                        order_id=order_id,
                        payment_id=payment_id,
                        metadata=payload
                    )
                    return Response({"status": "ok", "message": "Subscription updated successfully"})

            return Response({"status": "ignored", "message": "Webhook received"})
        except Exception as exc:
            return error_response(f"Webhook processing error: {str(exc)}", 400)


# Backward-compatible alias
RazorpayWebhook = BigBurryWebhook


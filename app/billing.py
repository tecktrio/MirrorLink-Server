import hashlib
import hmac
import json
import os
import time

import requests
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import administrators_collection
from .models import mirror_collection, sites_collection


PLAN = {
    "name": "MirrorLink Business",
    "amount": 2500000,
    "currency": "INR",
    "period": "monthly",
    "interval": 1,
    "total_count": 120,
    "sites": 10,
    "mirrors_per_site": 50,
}
RAZORPAY_API = "https://api.razorpay.com/v1"


def razorpay_config():
    key_id = os.environ.get("RAZORPAY_KEY_ID", "").strip()
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
    plan_id = os.environ.get("RAZORPAY_PLAN_ID", "").strip()
    if not key_id or not key_secret or not plan_id:
        return None
    return key_id, key_secret, plan_id


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


class Billing(APIView):
    """Create and verify the single MirrorLink recurring subscription."""

    def post(self, request):
        service = request.data.get("service")
        config = razorpay_config()
        if not config:
            return error_response("Razorpay is not configured. Set RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET and RAZORPAY_PLAN_ID on the server.", 503)
        key_id, key_secret, plan_id = config

        if service == "create_subscription":
            admin = admin_from_key(request.data.get("login_key"))
            if not admin:
                return error_response("Sign in to your MirrorLink account before subscribing.", 401)
            saved = admin.get("subscription", {})
            if saved.get("status") in ("active", "authenticated"):
                return Response({"status_text": "Your MirrorLink subscription is already set up.", "status_code": 200, "subscription_status": saved.get("status")})

            try:
                plan_response = requests.get(
                    f"{RAZORPAY_API}/plans/{plan_id}",
                    auth=(key_id, key_secret), timeout=15,
                )
                if not plan_response.ok:
                    return error_response("The configured Razorpay plan could not be loaded. Check RAZORPAY_PLAN_ID.", 502)
                plan = plan_response.json()
                item = plan.get("item", {})
                if (plan.get("period") != "monthly" or plan.get("interval") != 1
                        or item.get("amount") != PLAN["amount"] or item.get("currency") != "INR"):
                    return error_response("The Razorpay plan must be a monthly INR 25,000 plan.", 503)

                pending_id = saved.get("razorpay_subscription_id")
                if saved.get("status") == "created" and pending_id:
                    return Response({"status_code": 200, "key_id": key_id, "subscription_id": pending_id, "plan": PLAN})

                payload = {
                    "plan_id": plan_id,
                    "total_count": PLAN["total_count"],
                    "quantity": 1,
                    "customer_notify": False,
                    "expire_by": int(time.time()) + 1800,
                    "notes": {
                        "administrator_id": str(admin["_id"]),
                        "plan_name": PLAN["name"],
                    },
                }
                response = requests.post(
                    f"{RAZORPAY_API}/subscriptions", json=payload,
                    auth=(key_id, key_secret), timeout=15,
                )
                if not response.ok:
                    return error_response("Razorpay could not create the subscription. Please try again.", 502)
                subscription = response.json()
                administrators_collection.update_one(
                    {"_id": admin["_id"]},
                    {"$set": {"subscription": {
                        "plan_name": PLAN["name"],
                        "plan_id": plan_id,
                        "razorpay_subscription_id": subscription["id"],
                        "status": subscription.get("status", "created"),
                        "created_at": int(time.time()),
                    }}},
                )
                return Response({"status_code": 200, "key_id": key_id, "subscription_id": subscription["id"], "plan": PLAN})
            except requests.RequestException:
                return error_response("Could not reach Razorpay. Please try again shortly.", 502)

        if service == "verify_subscription":
            admin = admin_from_key(request.data.get("login_key"))
            if not admin:
                return error_response("Sign in to your MirrorLink account before verifying payment.", 401)
            saved = admin.get("subscription", {})
            subscription_id = saved.get("razorpay_subscription_id")
            payment_id = request.data.get("razorpay_payment_id", "")
            signature = request.data.get("razorpay_signature", "")
            if not subscription_id or not payment_id or not signature:
                return error_response("The payment verification details are incomplete.", 400)

            expected = hmac.new(
                key_secret.encode(), f"{payment_id}|{subscription_id}".encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, signature):
                return error_response("Razorpay payment verification failed.", 400)

            try:
                remote = requests.get(
                    f"{RAZORPAY_API}/subscriptions/{subscription_id}",
                    auth=(key_id, key_secret), timeout=15,
                )
                if not remote.ok:
                    return error_response("Payment signature was verified, but subscription status is not available yet.", 502)
                remote_status = remote.json().get("status", "authenticated")
                administrators_collection.update_one(
                    {"_id": admin["_id"], "subscription.razorpay_subscription_id": subscription_id},
                    {"$set": {
                        "subscription.status": remote_status,
                        "subscription.razorpay_payment_id": payment_id,
                        "subscription.verified_at": int(time.time()),
                    }},
                )
                return Response({"status_code": 200, "status_text": "Payment authorization verified.", "subscription_status": remote_status})
            except requests.RequestException:
                return error_response("Payment signature was verified, but Razorpay status could not be checked. Please retry.", 502)

        return error_response("Unknown billing service.", 400)


class RazorpayWebhook(APIView):
    """Keep local subscription status in step with Razorpay subscription events."""
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "").strip()
        signature = request.headers.get("X-Razorpay-Signature", "")
        if not secret or not signature:
            return error_response("Webhook signature is missing or not configured.", 400)
        expected = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return error_response("Invalid webhook signature.", 400)
        try:
            event = json.loads(request.body.decode("utf-8"))
            event_name = event.get("event", "")
            entity = event.get("payload", {}).get("subscription", {}).get("entity", {})
            subscription_id = entity.get("id")
            status = entity.get("status")
            if subscription_id and status:
                administrators_collection.update_one(
                    {"subscription.razorpay_subscription_id": subscription_id},
                    {"$set": {
                        "subscription.status": status,
                        "subscription.last_event": event_name,
                        "subscription.updated_at": int(time.time()),
                    }},
                )
            return Response({"status": "ok"})
        except (ValueError, TypeError):
            return error_response("Invalid webhook payload.", 400)

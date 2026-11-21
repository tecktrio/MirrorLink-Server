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

FREE_PLAN = {
    "plan_id": "mirrorlink-free-monthly",
    "name": "ScreenPilot Free",
    "amount": 0.00,  # in INR
    "currency": "INR",
    "period": "monthly",
    "interval": 1,
    "total_count": None,
    "sites": 1,
    "mirrors_per_site": 1,
    "max_mirrors": 1,
    "contents_per_mirror": None,
    "total_contents": None,
    "description": "Essential features to get started with 1 screen and unlimited content.",
    "benefits": [
        "1 site",
        "1 mirror",
        "Unlimited content on mirror",
        "Unlimited logins for mirror",
        "Menus, streaming, queue displays and ad management",
        "All core features included",
    ],
}

BUSINESS_PLAN = {
    "plan_id": "mirrorlink-business-monthly",
    "name": "ScreenPilot Business",
    "amount": 18900.00,  # in INR
    "currency": "INR",
    "period": "monthly",
    "interval": 1,
    "total_count": 120,
    "sites": 10,
    "mirrors_per_site": 50,
    "max_mirrors": 500,
    "contents_per_mirror": None,
    "total_contents": None,
    "description": "Full ScreenPilot toolkit ready to scale with your business.",
    "benefits": [
        "Up to 10 sites",
        "Up to 50 mirrors at each site",
        "Unlimited content on every mirror",
        "Unlimited logins for every mirror",
        "Menus, streaming, queue displays and ad management",
        "All core features included",
    ],
}

PLANS = [FREE_PLAN, BUSINESS_PLAN]

# Backward compatibility alias
PLAN = BUSINESS_PLAN

for p in PLANS:
    try:
        plans_collection.update_one(
            {"plan_id": p["plan_id"]},
            {"$set": {**p, "updated_at": int(time.time())}, "$setOnInsert": {"created_at": int(time.time())}},
            upsert=True
        )
    except Exception as exc:
        print(f"[Billing] Error upserting plan {p['plan_id']}: {exc}", flush=True)

# Supported Country Database with standard currency ISO and display symbol
COUNTRY_CATALOG = [
    {"name": "India", "code": "IN", "currency": "INR", "symbol": "₹"},
    {"name": "United States", "code": "US", "currency": "USD", "symbol": "$"},
    {"name": "United Kingdom", "code": "GB", "currency": "GBP", "symbol": "£"},
    {"name": "United Arab Emirates", "code": "AE", "currency": "AED", "symbol": "AED "},
    {"name": "Saudi Arabia", "code": "SA", "currency": "SAR", "symbol": "SAR "},
    {"name": "Qatar", "code": "QA", "currency": "QAR", "symbol": "QAR "},
    {"name": "Kuwait", "code": "KW", "currency": "KWD", "symbol": "KWD "},
    {"name": "Oman", "code": "OM", "currency": "OMR", "symbol": "OMR "},
    {"name": "Bahrain", "code": "BH", "currency": "BHD", "symbol": "BHD "},
    {"name": "Singapore", "code": "SG", "currency": "SGD", "symbol": "S$"},
    {"name": "Canada", "code": "CA", "currency": "CAD", "symbol": "CA$"},
    {"name": "Australia", "code": "AU", "currency": "AUD", "symbol": "A$"},
    {"name": "Germany", "code": "DE", "currency": "EUR", "symbol": "€"},
    {"name": "France", "code": "FR", "currency": "EUR", "symbol": "€"},
    {"name": "Italy", "code": "IT", "currency": "EUR", "symbol": "€"},
    {"name": "Spain", "code": "ES", "currency": "EUR", "symbol": "€"},
    {"name": "Netherlands", "code": "NL", "currency": "EUR", "symbol": "€"},
    {"name": "Switzerland", "code": "CH", "currency": "CHF", "symbol": "CHF "},
    {"name": "Sweden", "code": "SE", "currency": "SEK", "symbol": "kr "},
    {"name": "Norway", "code": "NO", "currency": "NOK", "symbol": "kr "},
    {"name": "Denmark", "code": "DK", "currency": "DKK", "symbol": "kr "},
    {"name": "Poland", "code": "PL", "currency": "PLN", "symbol": "zł "},
    {"name": "Ireland", "code": "IE", "currency": "EUR", "symbol": "€"},
    {"name": "New Zealand", "code": "NZ", "currency": "NZD", "symbol": "NZ$"},
    {"name": "Japan", "code": "JP", "currency": "JPY", "symbol": "¥"},
    {"name": "South Korea", "code": "KR", "currency": "KRW", "symbol": "₩"},
    {"name": "China", "code": "CN", "currency": "CNY", "symbol": "¥"},
    {"name": "Hong Kong", "code": "HK", "currency": "HKD", "symbol": "HK$"},
    {"name": "Taiwan", "code": "TW", "currency": "TWD", "symbol": "NT$"},
    {"name": "Malaysia", "code": "MY", "currency": "MYR", "symbol": "RM "},
    {"name": "Indonesia", "code": "ID", "currency": "IDR", "symbol": "Rp "},
    {"name": "Philippines", "code": "PH", "currency": "PHP", "symbol": "₱"},
    {"name": "Thailand", "code": "TH", "currency": "THB", "symbol": "฿"},
    {"name": "Vietnam", "code": "VN", "currency": "VND", "symbol": "₫"},
    {"name": "South Africa", "code": "ZA", "currency": "ZAR", "symbol": "R "},
    {"name": "Brazil", "code": "BR", "currency": "BRL", "symbol": "R$ "},
    {"name": "Mexico", "code": "MX", "currency": "MXN", "symbol": "Mex$"},
    {"name": "Turkey", "code": "TR", "currency": "TRY", "symbol": "₺"},
    {"name": "Egypt", "code": "EG", "currency": "EGP", "symbol": "E£ "},
    {"name": "Nigeria", "code": "NG", "currency": "NGN", "symbol": "₦"},
    {"name": "Kenya", "code": "KE", "currency": "KES", "symbol": "KSh "},
    {"name": "Bangladesh", "code": "BD", "currency": "BDT", "symbol": "৳"},
    {"name": "Pakistan", "code": "PK", "currency": "PKR", "symbol": "₨ "},
    {"name": "Sri Lanka", "code": "LK", "currency": "LKR", "symbol": "Rs "},
    {"name": "Nepal", "code": "NP", "currency": "NPR", "symbol": "Rs "},
    {"name": "Israel", "code": "IL", "currency": "ILS", "symbol": "₪"},
    {"name": "Russia", "code": "RU", "currency": "RUB", "symbol": "₽"},
    {"name": "Austria", "code": "AT", "currency": "EUR", "symbol": "€"},
    {"name": "Belgium", "code": "BE", "currency": "EUR", "symbol": "€"},
    {"name": "Portugal", "code": "PT", "currency": "EUR", "symbol": "€"},
    {"name": "Finland", "code": "FI", "currency": "EUR", "symbol": "€"},
    {"name": "Greece", "code": "GR", "currency": "EUR", "symbol": "€"},
    {"name": "Czech Republic", "code": "CZ", "currency": "CZK", "symbol": "Kč "},
    {"name": "Hungary", "code": "HU", "currency": "HUF", "symbol": "Ft "},
    {"name": "Romania", "code": "RO", "currency": "RON", "symbol": "lei "},
    {"name": "Bulgaria", "code": "BG", "currency": "BGN", "symbol": "лв "},
    {"name": "Croatia", "code": "HR", "currency": "EUR", "symbol": "€"},
    {"name": "Morocco", "code": "MA", "currency": "MAD", "symbol": "DH "},
    {"name": "Ghana", "code": "GH", "currency": "GHS", "symbol": "GH₵ "},
    {"name": "Jordan", "code": "JO", "currency": "JOD", "symbol": "JD "},
    {"name": "Peru", "code": "PE", "currency": "PEN", "symbol": "S/ "},
    {"name": "Ukraine", "code": "UA", "currency": "UAH", "symbol": "₴"},
    {"name": "Kazakhstan", "code": "KZ", "currency": "KZT", "symbol": "₸"},
]

# Currency conversion rates cache in-memory (valid for 1 hour)
RATES_CACHE = {
    "timestamp": 0,
    "rates": {},
}


def get_client_ip(request):
    """Extract client IP handling forward headers."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        ip = request.META.get("REMOTE_ADDR", "").strip()
    return ip


def detect_client_country(request):
    """Detect client country and currency from IP address using free open geo-lookup APIs."""
    ip = get_client_ip(request)
    # Check if local/private IP
    is_private = not ip or ip.startswith("127.") or ip.startswith("10.") or ip.startswith("192.168.") or ip == "::1"

    # Try ipapi.co
    try:
        url = "https://ipapi.co/json/" if is_private else f"https://ipapi.co/{ip}/json/"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
        if r.ok:
            data = r.json()
            code = data.get("country_code")
            name = data.get("country_name")
            curr = data.get("currency")
            if code and name:
                return {"code": code.upper(), "name": name, "currency": curr}
    except Exception:
        pass

    # Fallback to ip-api.com
    try:
        url = "http://ip-api.com/json/" if is_private else f"http://ip-api.com/json/{ip}"
        r = requests.get(url, timeout=3)
        if r.ok:
            data = r.json()
            if data.get("status") == "success":
                return {
                    "code": (data.get("countryCode") or "").upper(),
                    "name": data.get("country"),
                    "currency": None,
                }
    except Exception:
        pass

    # Default to India if detection fails
    return {"code": "IN", "name": "India", "currency": "INR"}


def fetch_exchange_rates():
    """Fetch live currency exchange rates from open exchange APIs with INR as base."""
    now = time.time()
    if RATES_CACHE["rates"] and (now - RATES_CACHE["timestamp"]) < 3600:
        return RATES_CACHE["rates"]

    # 1. Primary: open.er-api.com (Free open exchange API, unlimited, updated live)
    try:
        r = requests.get("https://open.er-api.com/v6/latest/INR", timeout=6)
        if r.ok:
            data = r.json()
            rates = data.get("rates")
            if rates and isinstance(rates, dict):
                RATES_CACHE["rates"] = rates
                RATES_CACHE["timestamp"] = now
                return rates
    except Exception as exc:
        print(f"[Currency] open.er-api fetch failed: {exc}", flush=True)

    # 2. Fallback: Frankfurter Open API
    try:
        r = requests.get("https://api.frankfurter.dev/v1/latest?base=INR", timeout=6)
        if r.ok:
            data = r.json()
            rates = data.get("rates", {})
            rates["INR"] = 1.0
            if rates:
                RATES_CACHE["rates"] = rates
                RATES_CACHE["timestamp"] = now
                return rates
    except Exception as exc:
        print(f"[Currency] frankfurter fetch failed: {exc}", flush=True)

    return RATES_CACHE["rates"] or {"INR": 1.0, "USD": 0.0106, "EUR": 0.0096, "GBP": 0.0080, "AED": 0.0389}


def convert_inr_to_country(amount_inr, country_code=None, country_name=None):
    """Convert an INR amount into the target country's currency."""
    target = None
    if country_code:
        code_upper = country_code.strip().upper()
        target = next((c for c in COUNTRY_CATALOG if c["code"] == code_upper), None)
    if not target and country_name:
        name_lower = country_name.strip().lower()
        target = next((c for c in COUNTRY_CATALOG if c["name"].lower() == name_lower), None)

    if not target:
        target = {"name": country_name or "India", "code": country_code or "IN", "currency": "INR", "symbol": "₹"}

    currency = target.get("currency", "INR")
    symbol = target.get("symbol", f"{currency} ")
    rates = fetch_exchange_rates()
    rate = rates.get(currency)

    if currency == "INR" or not rate:
        converted_amount = round(amount_inr, 2)
        rate = 1.0
    else:
        converted_amount = round(amount_inr * rate, 2)

    return {
        "country": target["name"],
        "country_code": target["code"],
        "currency": currency,
        "symbol": symbol,
        "rate": rate,
        "converted_amount": converted_amount,
        "base_amount": amount_inr,
        "base_currency": "INR",
    }


def get_current_plan():
    """Fetch the active business plan from the MongoDB plans collection, or return default."""
    try:
        doc = plans_collection.find_one({"plan_id": BUSINESS_PLAN["plan_id"]}) or plans_collection.find_one({"name": BUSINESS_PLAN["name"]})
        if doc:
            clean = dict(doc)
            clean.pop("_id", None)
            return clean
    except Exception as exc:
        print(f"[Billing] Error reading plan from database: {exc}", flush=True)
    return BUSINESS_PLAN


def get_plan_by_id(plan_id):
    """Retrieve plan by id or name from memory or DB."""
    if not plan_id:
        return None
    for p in PLANS:
        if p.get("plan_id") == plan_id or p.get("name") == plan_id:
            return p
    try:
        doc = plans_collection.find_one({"$or": [{"plan_id": plan_id}, {"name": plan_id}]})
        if doc:
            clean = dict(doc)
            clean.pop("_id", None)
            return clean
    except Exception:
        pass
    return None


def get_all_plans():
    """Return all available plans."""
    db_plans = []
    try:
        cursor = plans_collection.find({})
        for doc in cursor:
            clean = dict(doc)
            clean.pop("_id", None)
            db_plans.append(clean)
    except Exception:
        pass

    if db_plans:
        plan_map = {p.get("plan_id"): p for p in db_plans if p.get("plan_id")}
        result = []
        for default_p in PLANS:
            p_id = default_p["plan_id"]
            if p_id in plan_map:
                result.append({**default_p, **plan_map[p_id]})
            else:
                result.append(default_p)
        return result
    return list(PLANS)


def get_user_plan(user):
    """Resolve active subscription plan for a given administrator/user."""
    sub = (user or {}).get("subscription", {})
    plan_id = sub.get("plan_id")
    plan_name = sub.get("plan_name")
    if plan_id:
        found = get_plan_by_id(plan_id)
        if found:
            return found
    if plan_name:
        for p in PLANS:
            if p.get("name") == plan_name:
                return p
    if sub.get("status") == "active":
        return BUSINESS_PLAN
    return FREE_PLAN


def get_user_plan_limits(user):
    """Return enforce limits for a user based on their subscription."""
    plan = get_user_plan(user) or FREE_PLAN
    return {
        "sites": plan.get("sites", 1),
        "mirrors_per_site": plan.get("mirrors_per_site", 1),
        "max_mirrors": plan.get("max_mirrors") or (plan.get("sites", 1) * plan.get("mirrors_per_site", 1)),
        "contents_per_mirror": plan.get("contents_per_mirror"),
        "total_contents": plan.get("total_contents"),
    }


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


def activate_user_subscription(admin_id, plan=None, order_id=None, payment_id=None, metadata=None):
    """Mark an administrator account active for the current billing cycle."""
    now = int(time.time())
    one_month_later = now + (30 * 86400)
    metadata = metadata or {}
    chosen_plan = plan or BUSINESS_PLAN

    sub_data = {
        "plan_name": chosen_plan["name"],
        "plan_id": chosen_plan["plan_id"],
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
    """Handle subscription sessions, currency conversion, and verification through BigBurry Payments."""

    def post(self, request):
        service = request.data.get("service")

        if service == "get_countries":
            return Response({
                "status_code": 200,
                "countries": COUNTRY_CATALOG,
            })

        if service == "convert_currency":
            plan_id = request.data.get("plan_id")
            current_plan = get_plan_by_id(plan_id) if plan_id else get_current_plan()
            inr_amount = float(current_plan.get("amount", BUSINESS_PLAN["amount"]))
            country_code = request.data.get("country_code")
            country_name = request.data.get("country_name")

            if not country_code and not country_name:
                detected = detect_client_country(request)
                country_code = detected.get("code")
                country_name = detected.get("name")

            conversion = convert_inr_to_country(inr_amount, country_code, country_name)
            all_plans = get_all_plans()
            for p in all_plans:
                p_amt = float(p.get("amount", 0))
                p["conversion"] = convert_inr_to_country(p_amt, country_code, country_name)

            return Response({
                "status_code": 200,
                "plan": current_plan,
                "plans": all_plans,
                "conversion": conversion,
                "countries": COUNTRY_CATALOG,
            })

        if service == "get_plan":
            plan_id = request.data.get("plan_id")
            current_plan = get_plan_by_id(plan_id) if plan_id else get_current_plan()
            inr_amount = float(current_plan.get("amount", BUSINESS_PLAN["amount"]))
            country_code = request.data.get("country_code")
            country_name = request.data.get("country_name")

            if not country_code and not country_name:
                detected = detect_client_country(request)
                country_code = detected.get("code")
                country_name = detected.get("name")

            conversion = convert_inr_to_country(inr_amount, country_code, country_name)
            all_plans = get_all_plans()
            for p in all_plans:
                p_amt = float(p.get("amount", 0))
                p["conversion"] = convert_inr_to_country(p_amt, country_code, country_name)

            return Response({
                "status_code": 200,
                "plan": current_plan,
                "plans": all_plans,
                "conversion": conversion,
                "countries": COUNTRY_CATALOG,
            })

        if service == "get_subscription":
            admin = admin_from_key(request.data.get("login_key"))
            if not admin:
                return error_response("Please sign in to view subscription details.", 401)
            saved = dict(admin.get("subscription", {}))
            is_active = (saved.get("status") == "active" and bool(saved.get("plan_id"))) or (not saved.get("plan_id") or saved.get("plan_id") == FREE_PLAN["plan_id"])

            # Check if active subscription has passed current_end
            current_end = saved.get("current_end")
            if is_active and current_end and current_end < int(time.time()):
                # Free plan auto-renews
                plan_id = saved.get("plan_id")
                plan_obj = get_plan_by_id(plan_id)
                if plan_obj and float(plan_obj.get("amount", 0)) == 0:
                    saved = activate_user_subscription(
                        admin["_id"],
                        plan=plan_obj,
                        order_id="free_renew",
                        payment_id="free_tier"
                    )
                    is_active = True
                else:
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

            current_plan = get_user_plan(admin) or get_current_plan()
            inr_amount = float(current_plan.get("amount", 0))
            conversion = convert_inr_to_country(inr_amount, "IN", "India")
            all_plans = get_all_plans()
            for p in all_plans:
                p_amt = float(p.get("amount", 0))
                p["conversion"] = convert_inr_to_country(p_amt, "IN", "India")

            return Response({
                "status_code": 200,
                "plan_active": is_active,
                "plan": current_plan,
                "plans": all_plans,
                "conversion": conversion,
                "subscription": saved,
            })

        if service == "create_subscription":
            plan_id = request.data.get("plan_id")
            chosen_plan = get_plan_by_id(plan_id) if plan_id else get_current_plan()
            admin = admin_from_key(request.data.get("login_key"))
            if not admin:
                return error_response("Sign in to your MirrorLink account before subscribing.", 401)
            
            saved = admin.get("subscription", {})
            if saved.get("status") == "active" and (saved.get("current_end", 0) > int(time.time())):
                if saved.get("plan_id") == chosen_plan.get("plan_id"):
                    return Response({
                        "status_text": f"Your {chosen_plan.get('name', 'subscription')} is already active.",
                        "status_code": 200,
                        "subscription_status": "active",
                        "plan": chosen_plan,
                    })

            # Check if plan is FREE (amount == 0)
            if float(chosen_plan.get("amount", 0)) == 0:
                sub_data = activate_user_subscription(
                    admin["_id"],
                    plan=chosen_plan,
                    order_id=f"free_{int(time.time())}",
                    payment_id="free_tier"
                )
                return Response({
                    "status_code": 200,
                    "status_text": "Free subscription plan activated successfully!",
                    "subscription_status": "active",
                    "subscription": sub_data,
                    "plan": chosen_plan,
                })

            session_url, _, web_url, source_app = get_bigburry_config()
            customer_name = admin.get("username") or admin.get("email_id") or "MirrorLink User"
            customer_email = admin.get("email_id") or admin.get("email") or ""

            # Check if user selected another country/currency or default INR
            inr_amount = float(chosen_plan.get("amount", BUSINESS_PLAN["amount"]))

            # Always record the charge amount
            payload = {
                "sourceApp": source_app,
                "amount": inr_amount,
                "currency": "INR",
                "productName": chosen_plan.get("name", BUSINESS_PLAN["name"]),
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
                    "plan": chosen_plan,
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
            plan_id = request.data.get("plan_id")
            chosen_plan = get_plan_by_id(plan_id) or BUSINESS_PLAN

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
                plan=chosen_plan,
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



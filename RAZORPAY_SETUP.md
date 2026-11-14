# Razorpay subscription setup

MirrorLink's single subscription is **MirrorLink Business**: INR 25,000 billed monthly, covering up to 10 sites, up to 50 mirrors per site, unlimited content per mirror, and unlimited logins per mirror.

## Configure Razorpay

1. In the Razorpay Dashboard, create one subscription plan named `MirrorLink Business`, with a monthly billing period, interval `1`, amount `2500000` paise, and currency `INR`.
2. Add the plan ID and API credentials to the backend environment (never the React app):

   ```env
   RAZORPAY_KEY_ID=rzp_test_...
   RAZORPAY_KEY_SECRET=...
   RAZORPAY_PLAN_ID=plan_...
   RAZORPAY_WEBHOOK_SECRET=...
   ```

3. Configure a Razorpay webhook pointing to `https://mirrorlink.bigburry.com/api/RazorpayWebhook`. Subscribe to `subscription.authenticated`, `subscription.activated`, `subscription.pending`, `subscription.halted`, `subscription.paused`, `subscription.resumed`, `subscription.cancelled`, and `subscription.completed`. Use the same webhook secret in `RAZORPAY_WEBHOOK_SECRET`.
4. Test with Razorpay test mode keys and a test plan before switching to live keys and a live plan.

The backend checks that the configured plan is monthly and matches INR 25,000 before creating subscriptions. It verifies checkout signatures on the server and validates webhook signatures. Razorpay subscription billing supports at most 120 monthly charges, so the checkout is configured for 120 cycles (10 years); customers must subscribe again to continue after that period. The Razorpay account must have Subscriptions enabled for recurring mandates.

## Email OTP delivery

Registration and password reset send six-digit codes that expire after 10 minutes through Gmail SMTP. The server accepts `SMTP_PASS` (or `SMTP_PASSWORD`) as the App Password, plus `SMTP_FROM_NAME` and `SMTP_FROM_EMAIL` for the sender display. With `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, and `SMTP_SECURE=false`, Django uses STARTTLS. Set `SMTP_USER` to the Google account that sends OTPs. The Google account must have 2-Step Verification enabled to create an App Password. Keep the App Password in the server environment or its ignored `.env` file; never commit it. OTP challenges are kept in the MongoDB `users` collection; plan catalog details are kept in `plans`. Account records continue to be mirrored in `administrators` for compatibility with existing MirrorLink data ownership and billing.

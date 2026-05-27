# SentinelWeb PWA

This folder is a no-build Progressive Web App served by the FastAPI backend at `/app/`.

## Local test

1. Start the backend from `C:\Users\pgg12\Desktop\SentinelWeb`:

   ```powershell
   python main.py
   ```

2. Open `http://localhost:8766/app/`.
3. Use Single mode for `/query`, or Compare mode for `/query/compare`.
4. Use Inventory mode for the private beta `/inventory/search` flow.

The app shell is cached by `sw.js`. API calls are network-only so automation results are always fresh.

Inventory endpoints require an invited user. In production Cloudflare Access should provide `Cf-Access-Authenticated-User-Email`; for local API testing, seed invited users with `SENTINEL_BETA_USERS` and call the endpoints with a matching `X-Sentinel-User-Email` header or configured bearer token.

## Install on iOS

1. Open `https://app.sentinelprime.org/app/` in Safari.
2. Tap Share.
3. Tap Add to Home Screen.
4. Launch SentinelWeb from the home screen.

## Install on Android

1. Open `https://app.sentinelprime.org/app/` in Chrome.
2. Tap the install prompt, or open the three-dot menu and choose Add to Home screen.
3. Launch SentinelWeb from the home screen.

Cloudflare Access handles authentication before the app loads. The PWA does not expose any credential-management UI.

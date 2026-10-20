# Pocket Bot Pro — iPhone free-hosting paper test

This is a staging prototype using public Kraken market data and virtual trading. **Live orders are hard-disabled. Do not enter or upload Kraken API keys.** This is a simple moving-average strategy, not an AI model. No profitability is implied.

## Deploy using iPhone Safari

1. Download and unzip the project. In Safari, open https://github.com/new and create a **private** repository named `pocket-bot-paper`.
2. Open the new repository, select Add file > Upload files, and upload the *contents* of the unzipped folder, retaining `templates/index.html` and `static/manifest.json` directories. GitHub's mobile interface may make multi-folder uploads difficult; use Safari's Request Desktop Website if necessary. Alternatively connect GitHub in ChatGPT for assistance.
3. Open https://dashboard.render.com, sign up with GitHub, select New > Web Service, and connect the private repository.
4. Set Runtime to Python, instance type **Free**, build command `pip install -r requirements.txt`, start command `python start.py`.
5. In Environment, add `APP_PASSWORD` (a long unique password), `SESSION_SECRET` (a different long random string), and `HTTPS_ONLY=true`. Never add Kraken credentials. Deploy.
6. Open the HTTPS URL Render provides, sign in using APP_PASSWORD, and choose Share > Add to Home Screen in Safari.

## Limitations

Render free services sleep after 15 minutes idle. The bot stops while asleep. Render free storage is **ephemeral**, so your virtual balance and trade history will reset after restarts/redeployments. The service is for temporary manual testing only, not long-running reliable backtesting or live trading. Paper stop-loss is polled, not exchange-native, and cannot guarantee an exit. This build has not been independently audited or end-to-end tested on Render. Fees are estimated, slippage is not modeled, and the default strategy can lose money.

The `/api/live-order` endpoint always returns HTTP 403. Kraken account integration is intentionally not included.

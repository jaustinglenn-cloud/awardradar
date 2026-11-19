# AwardRadar — Zero-Cost Live Award Flight Finder

AwardRadar is a self-hosted, **$0/month** award travel radar powered by **GitHub Pages** and **GitHub Actions**. It aggregates live award flight deals across multiple free sources, benchmarks them against live cash fares to compute true Cents-Per-Point (CPP), matches them against your personal watchlist, and sends instant alerts.

## 5-Layer Zero-Cost Backend Waterfall (`scripts/search_flights.py`)
1. **PointsYeah Insider Deals & Flying Blue Promo Rewards** — Scraped cleanly with `BeautifulSoup`.
2. **Curated Award RSS Feeds** — Parses Frequent Miler, One Mile at a Time, View from the Wing, Thrifty Traveler, and Doctor of Credit via `feedparser`.
3. **r/awardtravel Live JSON** — Monitors real-time community award drops and partner space releases.
4. **Optional Seats.aero Partner API** — Automatically activates if you add `SEATS_AERO_API_KEY` in GitHub Secrets.
5. **Live Cash Verification (`fast-flights`)** — Uses Google Flights Protobuf to verify live cash prices and calculate real CPP value.

## Quick Setup
1. Open `index.html`, go to the **GitHub Deployer** tab, enter your repo (`owner/repo`) and Personal Access Token (stored locally in your browser only), and click **Push All 8 Files to GitHub**.
2. Enable GitHub Pages in **Repository Settings → Pages → Deploy from a branch (`main` / `/ (root)`)**.
3. (Optional) Add notification secrets in **Repository Settings → Secrets and variables → Actions**:
   - `NTFY_TOPIC`: Free instant mobile push via the `ntfy.sh` app.
   - `DISCORD_WEBHOOK_URL`: Free Discord channel alerts.
   - `SMTP_USER`, `SMTP_PASS`, `ALERT_EMAIL`: Optional Gmail App Password email alerts.
   - `SEATS_AERO_API_KEY`: Optional Seats.aero Partner API key.

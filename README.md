# AwardRadar — Zero-Cost Live Award Flight Finder

AwardRadar is a self-hosted, **$0/month** award travel radar powered by **GitHub Pages** and **GitHub Actions**. It actively scans your configured itineraries (Business/First prioritized + Economy) alongside 18+ free award sources, grades every flight 1–5, and pushes alerts to your iPhone via **ntfy.sh**.

## Quick Setup
1. Open `index.html`, go to **GitHub Deployer**, enter your repo (`owner/repo`) and PAT, and click **Push All 8 Files to GitHub**.
2. Enable GitHub Pages in **Repository Settings → Pages → Deploy from a branch (`main` / `/ (root)`)**.
3. Add `NTFY_TOPIC` (or `NFTY_TOPIC`) in **Repository Settings → Secrets and variables → Actions** for instant iPhone push alerts (and optionally `SERPAPI_API_KEY` for 100% reliable Google Flights live schedules/times).

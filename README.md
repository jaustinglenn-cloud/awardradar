# AwardRadar — Zero-Cost Live Award Flight Finder

AwardRadar is a self-hosted, **$0/month** award travel radar powered by **GitHub Pages** and **GitHub Actions**. It aggregates live award flight deals across multiple free sources, grades every flight 1–5 (with >4 CPP for Grade 4 and <1x/year DFW/DAL Unicorns for Grade 5), visualizes configured itineraries on an interactive map, and sends instant alerts.

## Quick Setup
1. Open `index.html`, go to **GitHub Deployer**, enter your repo (`owner/repo`) and PAT, and click **Push All 8 Files to GitHub**.
2. Enable GitHub Pages in **Repository Settings → Pages → Deploy from a branch (`main` / `/ (root)`)**.
3. Configure your itineraries on the **Itinerary Config** map, tune your 1–5 grading rules in **Alert Config**, and click **Run Live Search**.

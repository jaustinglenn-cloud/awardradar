# AwardRadar — Complete Setup & User's Guide

This manual walks you step by step through deploying your **$0 GitHub Pages + GitHub Actions backend**, installing **AwardRadar** as a full-screen iPhone Safari Progressive Web App (PWA), and operating every tab, filter, map control, transfer bonus calculator, and live push alert in day-to-day use.

---

## Part 1: Step-by-Step Setup Guide (Follow to the Letter)

### Step 1 — Create Your GitHub Repository & Upload the Bundle

1. Open **[github.com/new](https://github.com/new)** in your browser and sign in to your GitHub account.
2. Under **Repository name**, type **`awardradar`**.
3. Select **Public** visibility (Public repositories receive unlimited free GitHub Pages hosting and 2,000+ free GitHub Actions minutes per month).
4. Leave "Add a README file" unchecked (the zip bundle already includes `README.md`) and click **Create repository**.
5. Locate the **`awardradar-repo.zip`** file generated in this chat and unzip it on your computer. Confirm you see these 7 files:
   - `index.html` (the self-contained iPhone PWA frontend)
   - `searches.json` (your 5 active saved search slots and transfer bonuses)
   - `data/deals.json` (initial evaluated award and cash flight deals)
   - `scripts/search_flights.py` (Python runner using [`fast-flights`](https://pypi.org/project/fast-flights/) and modular partner connectors)
   - `.github/workflows/flight_search.yml` (6-hour automated cron workflow)
   - `requirements.txt` (Python package dependencies)
   - `README.md` (repository documentation)
6. Upload the files using **either** method below:
   - **Option A — Browser Drag & Drop (No Terminal Needed)**: On your newly created GitHub repository page, click **uploading an existing file**, drag all extracted files and folders (`index.html`, `searches.json`, `requirements.txt`, `README.md`, `data/`, `scripts/`, and `.github/`) into the browser window, and click **Commit changes**. *(Note: If your OS hides the `.github` folder in Finder/Explorer, press `Cmd + Shift + .` on macOS or enable "Hidden items" on Windows so `.github/workflows/flight_search.yml` is included).*
   - **Option B — Terminal Git Push**:
     ```bash
     unzip awardradar-repo.zip -d awardradar
     cd awardradar
     git init
     git branch -M main
     git add .
     git commit -m "feat: initial AwardRadar iPhone PWA and 6-hour GitHub Actions backend"
     git remote add origin https://github.com/<your-github-username>/awardradar.git
     git push -u origin main
     ```

### Step 2 — Grant GitHub Actions Read & Write Permissions

Because the scheduled workflow (`.github/workflows/flight_search.yml`) commits updated `data/deals.json` back to your `main` branch every 6 hours, you must enable write access for Actions:

1. In your `awardradar` GitHub repository, click **Settings** in the top navigation bar.
2. In the left sidebar, expand **Actions** and click **General** ([GitHub Actions Permissions Guide](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository#configuring-the-default-github_token-permissions)).
3. Scroll to the **Workflow permissions** section near the bottom.
4. Select **Read and write permissions**.
5. Click **Save**.

### Step 3 — Set Up Free iPhone Push Alerts (`ntfy.sh`)

AwardRadar uses **[ntfy.sh](https://ntfy.sh/)**, a free, zero-account pub/sub notification service, to push 4★ and 5★ deal alerts straight to your iPhone lock screen.

1. On your iPhone, install **[ntfy from the Apple App Store](https://apps.apple.com/us/app/ntfy/id1625396347)** and allow notifications when prompted.
2. Open the **ntfy** app, tap the **`+`** button in the bottom-right corner, and enter a unique, hard-to-guess topic name—for example:
   ```text
   awardradar-jglenn-dfw-94f4
   ```
   *(Leave "Use another server" unchecked so it defaults to `https://ntfy.sh`, then tap **Subscribe**).*
3. Back in your GitHub repository in your browser, go to **Settings → Secrets and variables → Actions** ([GitHub Actions Secrets Docs](https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions)).
4. Click **New repository secret**:
   - **Name**: `NTFY_TOPIC`
   - **Secret**: `awardradar-jglenn-dfw-94f4` *(paste the exact topic name you subscribed to on your iPhone)*
5. Click **Add secret**.

### Step 4 — Enable GitHub Pages Hosting

1. In your GitHub repository, go to **Settings → Pages** in the left sidebar ([GitHub Pages Docs](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)).
2. Under **Build and deployment → Source**, keep **Deploy from a branch** selected.
3. Under **Branch**, change `None` to **`main`**, keep the folder set to **`/ (root)`**, and click **Save**.
4. Wait about 60 seconds and refresh the **Settings → Pages** screen. A banner at the top will display your live URL:
   ```text
   https://<your-github-username>.github.io/awardradar/
   ```

### Step 5 — Install AwardRadar to Your iPhone Home Screen

1. Open **Safari** on your iPhone (iOS requires Safari for full PWA installation) and navigate to:
   `https://<your-github-username>.github.io/awardradar/`
2. Tap the **Share** button (`↑` square with an upward arrow at the bottom center of Safari).
3. Scroll down the share sheet and tap **Add to Home Screen**.
4. Verify the title reads **AwardRadar** alongside the custom blue radar icon, then tap **Add** in the top-right corner.
5. Tap the **AwardRadar** icon on your iPhone Home Screen. It launches in full-screen native mode (`apple-mobile-web-app-capable`) with no Safari address bar.

### Step 6 — Optional: Enable One-Tap iPhone-to-GitHub Sync (Fine-Grained PAT)

By default, you can edit your 5 search slots in the app and tap **📋 Copy searches.json** to paste into GitHub. If you want to push slot updates and trigger immediate scraper runs **directly from your iPhone in one tap**:

1. Open **[GitHub Fine-Grained Personal Access Tokens](https://github.com/settings/personal-access-tokens/new)**.
2. Set **Token name** to `AwardRadar-iPhone-PWA` and choose an expiration (e.g., 90 days or 1 year).
3. Under **Repository access**, select **Only select repositories** and pick **`awardradar`**.
4. Expand **Permissions → Repository permissions** and set:
   - **Contents**: `Read and write` *(allows your iPhone to commit `searches.json`)*
   - **Actions**: `Read and write` *(allows your iPhone to trigger `workflow_dispatch` runs on demand)*
5. Click **Generate token** and copy the `github_pat_...` string.
6. Open **AwardRadar** on your iPhone, tap the **Alerts & Setup** tab at the bottom right, fill in:
   - **Custom `deals.json` Feed URL**: `./data/deals.json`
   - **GitHub Repository (`owner/repo`)**: `<your-github-username>/awardradar`
   - **Target Branch**: `main`
   - **Fine-Grained PAT**: Paste your `github_pat_...` token
7. Tap **Save Sync Settings**. Your token is stored exclusively in your iPhone's local browser storage (`localStorage`) and is never committed to the repository.

---

## Part 2: Complete AwardRadar User's Guide

AwardRadar is organized into **4 bottom navigation tabs** plus a top status bar. Below is how to use every feature.

### 1. Top Header Bar & Live Sync

- **Home Badge (`Home: DFW` / `Home: DAL`)**: Shows your currently active home origin airport.
- **`⚡ UR Bonus: Active` Button**: Indicates whether any Chase Ultimate Rewards transfer bonus is currently toggled on. Tapping it jumps straight to the transfer bonus controls.
- **`Sync Feed` Button**: Fetches the latest `data/deals.json` committed by GitHub Actions, recalculates every deal's effective Cents Per Point (CPP) and 1–5★ grade, and updates the timestamp.

### 2. Tab 1 — Deals (`Deals`)

The **Deals** tab is your daily command center for evaluating whether to book an award flight with points, pay cash, or wait.

#### A. Reading the Top KPI Strip
- **Active Deals**: Shows total evaluated award/cash comparisons across your active slots (e.g., `12` deals across `5/5 Slots`).
- **Best Value (CPP)**: Highlights the single highest Cents-Per-Point redemption currently available and its route/program (e.g., `9.07¢ CPP` on `DFW → MXP • Flying Blue`).
- **4★–5★ Alerts**: Counts how many deals meet or exceed the 4★ sweet-spot threshold that triggers an [`ntfy.sh`](https://ntfy.sh/) push notification.

#### B. Using the Live Chase UR Transfer Bonus Bar
Whenever Chase offers a promotional transfer bonus (such as `+25%` to Air France/KLM Flying Blue or `+30%` to British Airways / Iberia Avios):
1. Tap any program chip in the **⚡ Live UR Transfer Bonuses** bar (`Flying Blue`, `BA Avios`, `Iberia Plus`, `Virgin Atlantic`, `Aeroplan`).
2. Each tap cycles the bonus through **`0% → +20% → +25% → +30% → +40%`** (or tap **`Reset 0%`** to clear all bonuses back to the 1:1 baseline).
3. Watch every eligible Chase UR deal card update immediately:
   - **Effective Points Formula**: `Effective Points = ceil(Base Partner Points / (1 + Bonus% / 100))`
   - For example, a `50,000`-point Flying Blue Business Class saver award to Milan (`MXP`) drops to **`40,000` Chase UR points** with a `+25%` bonus, boosting your effective redemption value from `6.80¢` to **`8.51¢ CPP`**.

#### C. Filtering Your Deals
Use the 4 dropdown menus and checkboxes to narrow down your feed:
- **Search Slot**: View all 5 slots at once, or isolate a single route (e.g., `Slot #1: DFW → MXP`).
- **Program Ecosystem**: Filter by `All Ecosystems`, `Chase Ultimate Rewards (1:1)`, `American AAdvantage / oneworld`, or `Delta SkyMiles / SkyTeam`.
- **Cabin Class**: Filter by `All Cabins (J Prioritized)`, `Business / First Only`, or `Economy Only`.
- **Min Star Grade**: Filter by `Any Grade (1★–5★)`, `4★+ Sweet Spots Only`, or `5★ Unicorn Deals Only`.
- **`±3d Flex Hits Only` Checkbox**: Check this box to display only deals discovered on adjacent flexible dates (`-3d` to `+3d`) around your target departure date.

#### D. Understanding Deal Cards, Star Grades & Buy/Wait Recommendations
Every deal card displays side-by-side metrics so you never have to calculate value manually:

| Card Element | What It Tells You |
| :--- | :--- |
| **Route & Flex Badge** | Origin `→` Destination (`DFW → MXP`) plus whether the deal is on your `Exact Date` or an adjacent flexible day (`-1d Flex`, `+1d Flex`). |
| **Award Cost (Per Person)** | Effective points required per person + taxes/fees in USD, plus the **total points required for all passengers** (e.g., `2 pax`). |
| **Fast-Flights Cash Baseline** | Live Google Flights one-way cash price per person fetched by [`fast-flights`](https://github.com/AWeirdDev/flights) plus Google's built-in price indicator (`LOW`, `TYPICAL`, or `HIGH`). |
| **Effective CPP & 1–5★ Grade** | `CPP = ((Cash Price - Taxes/Fees) / Effective Points) * 100`. Graded **5★** (Unicorn: Business `≥4.5¢` or Saver `≤55k`; Economy `≥2.3¢` or Saver `≤20k`), **4★** (Strong Sweet Spot: Business `≥3.2¢`; Economy `≥1.7¢`), **3★** (Fair Value), or **1★–2★** (Subpar). |
| **Action Recommendation Pill** | **`🔥 BOOK AWARD NOW (UNICORN)`** (5★), **`✅ GREAT SAVER AWARD`** (4★), **`💵 PAY CASH (LOW FARE ALERT)`** (when Google Flights flags cash as `LOW` and award CPP is `≤3★`), or **`⚠️ SUBPAR CPP — SAVE POINTS`**. |

#### E. Using the Phase 2 Round-Trip / Open-Jaw Pairing Synthesizer
Although Phase 1 searches one-way legs individually (which is how award sweet spots are priced and booked), you can combine any two one-way deals into a complete round-trip or open-jaw itinerary:
1. Tap **`+ Outbound`** on your preferred outbound deal card (for example, `DFW → MXP` on `2027-09-10` via Air France Flying Blue).
2. Tap **`+ Return`** on your preferred return deal card (for example, `VCE → DFW` on `2027-09-18` via Iberia Plus).
3. The **Round-Trip / Open-Jaw Itinerary Synthesizer** drawer opens automatically at the top of the Deals tab, displaying:
   - Combined points + taxes/fees **per person**
   - Total points required across **all passengers (e.g., 2 seats)**
   - A live **130k Chase UR Pool Check** badge (`✓ Fits 130k Chase UR Pool` or `Split UR + AA/DL Miles`)
   - Your **Blended Round-Trip CPP** versus combined cash fares.

#### F. Inspecting a Deal (`📊 Valuation & Transfer Guide`)
Tap **`📊 Valuation & Transfer Guide`** on any card to open the step-by-step booking drawer. It shows the exact math breakdown, total cash savings across your passenger count, step-by-step transfer instructions, and a **`Verify Live on Google Flights / Partner ↗`** deep link.

---

### 3. Tab 2 — Map & Date (`Map & Date`)

Use the **Map & Date** tab to explore routes visually and configure new search slots.

1. **Choose Pin Mode (`Set Home (Origin)` vs. `Set Destination`)**:
   - Tap **`Set Home (Origin)`** or use the one-tap **`DFW (Default)`** and **`DAL (Love Field)`** quick buttons to set your departure airport.
   - Tap **`Set Destination`** or tap any quick target pill (`MXP`, `VCE`, `BLQ`, `VRN`, `LGB`, `LAX`, `CDG`, `LHR`, `HND`) to set your arrival airport.
   - Tap **`⇄ Swap`** at any time to flip Origin and Destination (ideal for quickly building your return leg).
2. **Search 28,000+ Global Airports**:
   - Type any 3-letter IATA code, city, or airport name into the search bar (backed by the 26 core verified hubs offline and expanded live to 28,000+ airports via [`mwgg/Airports`](https://github.com/mwgg/Airports)). Tap a result from the dropdown to set your pin and draw the animated great-circle geodesic flight arc with exact mileage.
3. **Pick Departure Date & `±3 Days Flex Scan`**:
   - Use **`◀`** / **`▶`** or the **Quick Jump** buttons (`Dec 24, 2026`, `Dec 31, 2026`, `Sep 10, 2027`, `Sep 18, 2027`) to navigate the monthly calendar.
   - Tap any day cell to select your target departure date (highlighted in solid sky blue).
   - Toggle **`±3 Days Flex Scan`** on or off. When checked, the calendar shades the 3 days before (`-3d` to `-1d`) and 3 days after (`+1d` to `+3d`) your target date, and the **Rate-Limit Guardrail Check** box updates the request budget (`7 reqs / 6h run` with 3.0–5.5s randomized jitter).
4. **Select Cabin, Seats & Save (`Max 5`)**:
   - Choose **`Both (J + Y)`**, **`Business (J)`**, or **`Economy (Y)`**.
   - Choose **`1 Seat`**, **`2 Seats`** (default), **`3 Seats`**, or **`4 Seats`**.
   - Add a descriptive note under **Trip Label / Notes** and tap **`Save to Active Searches (Max 5)`**.

---

### 4. Tab 3 — Saved Searches (`Saved (5)`)

The **Saved** tab enforces the **5-slot rate-limit guardrail** so your GitHub Actions runner never triggers Google Flights or airline anti-bot rate limits.

- **Slot Actions**:
  - **`View Deals`**: Filters the **Deals** tab immediately to show only deals belonging to that specific search slot.
  - **`Edit on Map`**: Loads that slot's origin, destination, date, `±3d` flex toggle, cabin, and seat count into the **Map & Date** tab so you can modify and save it in place.
  - **`Delete`**: Removes the slot to free up capacity (the progress bar updates from `5 / 5 Active` to `4 / 5 Active`).
- **Syncing Your Slots to GitHub**:
  - **One-Tap API Push (`🚀 Push to GitHub Repo (API)`)**: If you saved a Fine-Grained PAT in **Alerts & Setup**, tapping this button commits your updated `searches.json` directly to your GitHub repository via `PUT https://api.github.com/repos/{owner}/{repo}/contents/searches.json`.
  - **Manual Copy (`📋 Copy searches.json`)**: Copies the formatted `searches.json` payload to your iPhone clipboard so you can paste it into GitHub if you prefer not to use a PAT.
  - **`Restore Default 5 Slots`**: Resets your 5 slots back to the pre-configured Northern Italy (`DFW → MXP`, `VCE → DFW`, `DFW → BLQ`) and LA Holiday (`DAL → LGB`, `LAX → DFW`) searches at any time.

---

### 5. Tab 4 — Alerts & Setup (`Alerts & Setup`)

- **Test iPhone Push Alerts (`🔔 Send Test Push`)**:
  - Enter your secret [`ntfy.sh`](https://ntfy.sh/) topic name, select whether you want push notifications for **`4★ & 5★ Deals (Default)`** or **`5★ Unicorn Deals Only`**, and tap **`🔔 Send Test Push`**.
  - Your iPhone will immediately fire a live `POST https://ntfy.sh/<topic>` request so you can verify lock-screen delivery in the [ntfy iOS app](https://apps.apple.com/us/app/ntfy/id1625396347).
- **Trigger an On-Demand Backend Scan (`⚡ Trigger GitHub Action Run Now`)**:
  - Instead of waiting for the next 6-hour cron schedule (`0 */6 * * *`), tap **`⚡ Trigger GitHub Action Run Now`** after updating your search slots to dispatch `.github/workflows/flight_search.yml` immediately from your iPhone.

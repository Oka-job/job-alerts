# Daily job alerts

Every day around midday this finds new jobs and sends up to 20 of them to Telegram and email, with apply links, a match percentage, and warnings for suspicious listings. A free website tracks which jobs have been applied to. It runs on GitHub's free servers, so no computer needs to stay on.

**What it keeps:** remote jobs open to people in India, and hybrid jobs only in Bengaluru. Posted in the last 15 days, up to 5 years' experience asked, permanent or contract, employee roles only. Everything else (onsite, hybrid elsewhere, freelance, part-time, internships, too senior) is dropped.

**What it shows:** jobs inside 12-20 LPA, jobs with no salary listed (in their own section), and "near matches" to fill the list when fewer than 20 qualify. Salaries in dollars or euros are converted at approximate rates set in `config.yaml`.

## One-time setup (about 30 minutes)

### 1. Put the project on GitHub
1. Create a free account at github.com (use any email).
2. Click **New repository**. Name it `job-alerts`. Choose **Public** (needed for the free tracker website; see the privacy note below). Create it.
3. Click **uploading an existing file**, drag in **everything inside the unzipped folder** (including the hidden `.github` folder), and commit.

> If `.github` doesn't upload by drag and drop, use **Add file → Create new file**, type `.github/workflows/daily.yml` as the name, and paste the contents of that file.

### 2. Make a Telegram bot (free, instant phone notifications)
1. In Telegram, search for **@BotFather**, send `/newbot`, follow the prompts. It gives you a **token** like `123456:ABC...`.
2. Open a chat with your new bot and press **Start** (send it any message).
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser (replace `<TOKEN>`). Find `"chat":{"id":123456789` and note the number. That is your **chat id**.

### 3. Email (optional but recommended as a backup)
Use a Gmail account to send. Turn on 2-step verification, then create an **App password** at myaccount.google.com/apppasswords (it looks like 16 letters). Do not use your normal password.

### 4. Add the secrets
In the repo: **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the bot token |
| `TELEGRAM_CHAT_IDS` | the chat id (several allowed, comma separated) |
| `EMAIL_USER` | the Gmail address that sends |
| `EMAIL_PASS` | the 16-letter app password |
| `EMAIL_TO` | where to deliver (several allowed, comma separated) |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | optional: free keys from developer.adzuna.com, adds Indian job listings |

**Testing with your own account first:** use *your* chat id and email now. Later, edit `TELEGRAM_CHAT_IDS` and `EMAIL_TO` to his details (or add both, comma separated). Nothing else changes. He needs to open the bot and press Start once so it is allowed to message him.

### 5. Turn on the tracker website
**Settings → Pages → Build and deployment → Deploy from a branch → `main` / `docs` → Save.** After a minute the page appears at `https://<your-username>.github.io/job-alerts/`. Under **Settings → Secrets and variables → Actions → Variables** add a variable `TRACKER_URL` with that address, and each message will link to it.

### 6. Run it
Open the **Actions** tab, choose **Daily job alerts**, press **Run workflow**. Check Telegram/email within a couple of minutes. If it works, it runs by itself every day.

## Reading a run's log
Open the run in the Actions tab. It lists each source (`remotive: 34 jobs`, or `FAILED: ...`) and how many jobs were dropped and why. If one source fails, the others still work. The message footer also says which sources were not available that day.

## Changing the rules
Everything is in `config.yaml`: salary range, cities, role words, tools, companies to watch, number of jobs per day. To watch a particular company, add its name under `greenhouse`, `lever` or `ashby` if its jobs page is `boards.greenhouse.io/<name>`, `jobs.lever.co/<name>` or `jobs.ashbyhq.com/<name>`.

### Keeping the salary range private
A public repo shows `config.yaml` to anyone. To hide the salary, delete the `min_lpa` / `max_lpa` numbers from the file and instead create a secret `JOB_CONFIG` containing:

```yaml
salary:
  min_lpa: 12
  max_lpa: 20
```
Secrets are merged over the file at run time.

## Good to know
- **Not covered:** LinkedIn, Naukri, Indeed and Glassdoor forbid automated collection, so they are not used. The most reliable complement is to set up each site's own free email alerts for the same roles. Large employers that use their own career portals (Workday, SAP SuccessFactors, etc.) are also not covered unless they appear through Adzuna or the boards above.
- **Verify first:** the scam checks are heuristics, not proof. Check any recruiter on LinkedIn and never pay money or share ID documents to get a job.
- **Tracker data** (applied, notes) is stored in the browser on that device. Use the backup button to move it between devices.
- **If alerts stop:** GitHub can pause schedules on repos with no activity for 60 days. The daily history commit normally counts as activity; if it is paused, press **Enable** on the Actions page.
- **Try it offline:** `pip install -r requirements.txt` then `python -m jobalerts.main --sample` builds a preview from fake listings in `out/`.

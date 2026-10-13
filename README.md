# Research Grant Portal

A web system for institutional research and research grants. A researcher submits a study, answers the review panel's critiques, gets the study archived with an accession number, and can receive a conference or publication subsidy. The subsidy is released in tranches and liquidated with audited receipts. Once the record is archived and the grant is 100% liquidated, the admin issues the Institutional Research and Financial Clearance Certificate.

It uses the same tools and deployment path as Laboratory Experiment 8: Flask with Jinja templates, HTML and CSS, Python, bcrypt, Pydantic, SQLite on localhost, PostgreSQL on Supabase through psycopg, Gunicorn, GitHub and Render, plus an optional Tkinter desktop viewer.

## The seven steps

| Step | Who | What happens |
|---|---|---|
| 1. Stakeholder profiling | Researcher | Name, affiliation (student, faculty, non-teaching personnel or independent researcher), ID number and college or office (not asked of independent researchers), subject category and research type. |
| 2. Submission and notification | Researcher | Title, abstract (50 to 350 words), 3 to 6 keywords, research agenda, co-authors, manuscript, notification email and visibility (public, institutional or restricted). Submitting assigns the accession number, for example IR-2026-0001. |
| 3. Review and revision matrix | Admin reviews, researcher answers | The admin records the similarity index, which must be below 20%, and attaches the similarity report and the IERB certification. The admin then adds critiques, which the researcher answers with the revision made, the page and the lines, or accepts the study without revisions. The admin can remove a critique that has not been answered. |
| 4. Archival, accessioning and track selection | Researcher attaches, admin verifies | Choose the Conference Presentation Subsidy, the Publication Subsidy or the Archival-Only Pathway. The matching checklist appears. Verifying archives the record. |
| 5. Grant and tranche allocation | Researcher budgets, admin approves | Budget per line item within the ceiling, released in two tranches (60% and 40%) or three (40%, 30% and 30%). Approval releases Tranche 1. |
| 6. Voucher, receipt and audit reconciliation | Researcher files, admin audits | Remaining Tranche Balance = Disbursed Tranche − Σ Audited Receipts. Unspent money can be returned to the cashier. The next tranche is released only when the current one reaches zero. |
| 7. Clearance and closure | Admin | Checks archival and 100% liquidation, then issues the certificate, for example IRFC-2026-0001, with a verification code. |

The Archival-Only Pathway has no grant, so it skips steps 5 and 6.

Grant ceilings in pesos:

| Track | Student | Faculty | Non-teaching personnel | Independent researcher |
|---|---|---|---|---|
| Conference Presentation Subsidy | 25,000 | 60,000 | 40,000 | 40,000 |
| Publication Subsidy | 30,000 | 80,000 | 50,000 | 50,000 |

The amounts are set in `TRACKS` in `researchgrantportal.py`.

**Notification emails.** The notification email given in step 2 receives a short email when the submission is received, the review is completed, the record is archived, the grant is approved, a receipt is not accepted, a tranche is released and the certificate is issued. A failed email never blocks the action.

**Deleting a submission.** Researchers can delete their own submissions from the dashboard, after a confirmation page, until grant funds are released or a certificate is issued. Deleting removes the submission's files and activity history. Accession and certificate numbers are never reused.

## Signing in

After signing in, researchers and the admin first see the Home page, which offers three choices: the Dashboard, the Research archive and the Account page. Each card shows a live count, such as how many submissions are waiting for your action, how many archived studies you can see, or a warning while the admin still uses the default password.

The menu on the left is the same on every page. Under it, **Needs your action** lists up to three studies waiting on you, each with its step and status, and links to the rest on the dashboard. Below that are the totals of your submissions (or, for the admin, of all studies): total, in progress and cleared. On short windows the list shows two items and the totals are left out, so Sign out stays in view. On phones the menu becomes four tabs at the top and these panels are left to the Home page and the dashboard.

## Accounts

**Researchers** create their own accounts on the sign-up page, with any email address they can open (Gmail, Yahoo, Outlook, a school address and so on). A 6-digit code sent to that email confirms it. Forgot password works the same way. A code expires after 10 minutes and allows 5 tries, and a new one can be requested once a minute. Only a bcrypt hash of each code is stored, never the code itself.

**The admin** is a single account. Nobody can sign up as admin; it is created automatically on the first start:

- On localhost, if you set nothing: email `admin@example.com`, password `ChangeMe#2026`. The Account page shows a warning until the password is changed.
- Anywhere else, including Render: set `ADMIN_NAME`, `ADMIN_EMAIL` and `ADMIN_PASSWORD` before the first start. On Render the local default above is never created.
- To hand the portal over, give the new admin the email and password and have them change the password on the Account page. Use an email the admin can open, because Forgot password sends its code there.

Five wrong passwords in a row lock an account; Forgot password unlocks it. Guests can search the public archive without any account.

## Project structure

```
ResearchGrantPortal/
├── researchgrantportal.py            core logic, database, validation, optional Tkinter viewer
├── app.py                            Flask web bridge: routes and pages
├── migrate_sqlite_to_postgresql.py   copies local SQLite data into Supabase
├── requirements.txt
├── .gitignore
├── .env.example                      every setting, with comments
├── templates/                        Jinja pages (sign-in, sign-up, home, dashboard, archive, study, step1 to step7, certificate, delete)
└── static/
    ├── style.css
    ├── favicon.svg
    └── LICENSE-lucide.txt            license for the Lucide icons
```

`researchgrantportal.py` plays the role of `Laboratorysystem.py` in the lab: it holds the rules and is shared by the website and the desktop viewer. `app.py` only connects browser requests to those functions.

## Run on localhost (Windows CMD)

1. Open Command Prompt in the `ResearchGrantPortal` folder.
2. Optional: activate your environment, for example `conda activate your_env`, or `python -m venv .venv` then `.venv\Scripts\activate`.
3. Install the packages:
   ```
   python -m pip install -r requirements.txt
   ```
4. Optional: copy the settings file and edit it (admin account, email):
   ```
   copy .env.example .env
   ```
5. Start the app:
   ```
   python app.py
   ```
6. Open http://127.0.0.1:5000

The first start creates `research_grant_portal.db` and the admin account and prints the admin email in the terminal. Without email settings, every verification code is printed in the terminal too, so sign-up can be tested without a mail account. Gunicorn does not run on Windows, so use `python app.py` locally; Render runs Gunicorn.

## Sending real emails

Set one of these options in `.env` (localhost) or in Render's Environment. The app picks it up automatically.

**Option 1: Gmail (localhost only)**

1. Turn on 2-Step Verification for the Gmail account.
2. Create an app password in the Google Account security settings.
3. Set `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER` (your Gmail), `SMTP_PASSWORD` (the 16-character app password) and `MAIL_FROM` (your Gmail).

Render's free web services block outgoing email ports (25, 465 and 587), so this option does not work there.

**Option 2: Brevo (Render and localhost)**

1. Create a free Brevo account (300 emails a day).
2. Add a sender email under Senders and verify it. A Gmail address works.
3. Create an API key.
4. Set `BREVO_API_KEY` and `MAIL_FROM` (the verified sender email).

Brevo is reached over HTTPS, which Render allows.

## Deploy: GitHub, Supabase, Render

This is the same path as Laboratory Experiment 8.

**A. GitHub.** Push the project the same way as in the lab (`git init`, `git remote add origin ...`, `git add .`, `git commit -m "Initial web application"`, `git push -u origin master:main`). The `.gitignore` already keeps out the database file, `.env`, virtual environments and `__pycache__`. Never commit `.env` or a connection string.

**B. Supabase.** Create a project and save the database password. For the connection string, open Connect and copy the **Session pooler** string. It works on IPv4 networks, which includes Render and most home internet, and looks like `postgresql://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres`.

**C. Optional: move your local data.** To bring your local records online, run the migration before the app ever starts with this `DATABASE_URL`:

```
set "DATABASE_URL=YOUR_SUPABASE_CONNECTION_STRING"
python migrate_sqlite_to_postgresql.py
```

It creates the tables, copies every table including uploaded files, and refuses to run if the tables already hold rows, so nothing is duplicated. Skip it for a fresh start: the app creates its own tables.

**D. Test locally with Supabase.**

```
set "DATABASE_URL=YOUR_SUPABASE_CONNECTION_STRING"
python app.py
```

Sign in, save something, and check that it appears in the Supabase Table Editor.

**E. Render.** New, then Web Service. Connect the GitHub repository, choose the `main` branch and the Python runtime, and leave Root Directory blank.

- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app`
- Plan: Free

Environment variables:

| Name | Value |
|---|---|
| `DATABASE_URL` | the Supabase Session pooler string |
| `SECRET_KEY` | a long random value, for example from `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_NAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` | the admin account to create on the first start |
| `BREVO_API_KEY`, `MAIL_FROM` | email for verification codes |

Render sets `RENDER=true` by itself. The app then refuses to start without `SECRET_KEY`, sends its cookies over https only, and never creates the local default admin. Deploy, wait for Live, and open the onrender.com address.

## Upgrading from the previous version

Replace the project files and run `python app.py` again. An existing `research_grant_portal.db` keeps working: tables and columns added in later versions are created on start, and columns the current version no longer uses are left alone. The migration script copies only the columns the current version uses, so an older database can still be moved to Supabase.

## Optional desktop viewer

```
python researchgrantportal.py
```

A read-only Tkinter window for the admin: sign in, then search every study with its current status. It uses the same database as the website (the SQLite file, or Supabase when `DATABASE_URL` is set). Without Tkinter it prints a short message instead.

## Uploads

Manuscripts can be PDF or Word; the similarity report and IERB certification are PDF; checklist documents and receipts can be PDF, JPG or PNG. Each file can be up to 5 MB. Files are stored inside the database, because a Render free service loses its own disk on every restart and redeploy. Keep files small, because Supabase's free database holds 500 MB.

## All settings

| Name | Purpose |
|---|---|
| `SECRET_KEY` | Signs the login cookie. Required on Render. |
| `DATABASE_URL` | PostgreSQL (Supabase) connection string. Leave unset to use SQLite. |
| `ADMIN_NAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` | The admin created on the first start. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | Gmail or another mail server (localhost). |
| `BREVO_API_KEY` | Brevo email API (works on Render). |
| `MAIL_FROM` | The sender address for codes. |
| `APP_UTC_OFFSET` | Hours from UTC for dates and times. Default 8 (Philippine time). |
| `SQLITE_PATH` | Optional other location for the SQLite file. |
| `BCRYPT_ROUNDS` | Password hashing cost. Default 12. |

## Security notes

Passwords and one-time codes are stored as bcrypt hashes. Every form carries a hidden token, so other websites cannot submit forms on a user's behalf. Researchers can open only their own studies and files; the admin can open all of them. Uploaded files are served only to signed-in users with access. Keep `.env`, the Supabase password and `SECRET_KEY` private; if one leaks, change it.

## Troubleshooting

| Problem | What to do |
|---|---|
| No code arrives | Without email settings, the code is in the terminal. With Brevo, check that the sender is verified and `MAIL_FROM` matches it. Check the spam folder. |
| "The code could not be sent right now" | The email service refused or could not be reached; the terminal or the Render log shows why. On Render, Gmail is blocked, so use Brevo. |
| `RuntimeError: SECRET_KEY is not set` | Add `SECRET_KEY` under Environment in Render. |
| No admin account on Render | Set `ADMIN_EMAIL` and `ADMIN_PASSWORD`, then restart the service. |
| Migration says a table is not empty | Run it on a fresh database, before the app has started with that `DATABASE_URL`. |
| Database connection error on Render | Use the Session pooler string and check the password inside it. |
| "This form has expired" | The page was open across a sign-in or sign-out. Reload it and try again. |
| The first page load is slow | A Render free service sleeps after inactivity. Wait for it to wake. |

## Credits

Icons are from Lucide (lucide.dev), used under the ISC license included as `static/LICENSE-lucide.txt`.

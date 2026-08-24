# MyClinic — Implementation Plan

Companion to `requirements.md`. Work top to bottom; each phase should be functionally complete, covered by tests where noted, and manually testable before moving on.

## Phase -1 — Confirm Open Decisions
- [x] Review `requirements.md` §1 (Open Decisions) with the product owner; confirm or override each default before starting the phase it affects
- [x] Record final decisions back into `requirements.md` §1 so it stays the source of truth

## Phase 0 — Project Setup
- [x] Django project scaffold, PostgreSQL configured via `DATABASE_URL` env var
- [x] `TIME_ZONE=Asia/Tehran` set in settings, `USE_TZ=True`
- [x] `.env.example` created listing all required env vars (no real secrets committed)
- [x] Redis + Celery + Celery Beat wired up and running locally (`celery -A ... worker`, `celery -A ... beat`)
- [x] Tailwind (or chosen CSS approach) configured with RTL base styles
- [x] Base template with `dir="rtl"`, Persian font, nav skeleton
- [x] Test runner configured (`TestCase` or pytest-django) and running in CI/locally on a no-op test

## Phase 1 — Auth & User Model
- [x] Custom `User(AbstractBaseUser)` with `email` as `USERNAME_FIELD`, `user_type` field
- [x] Integrate django-allauth with the custom user model
- [x] `Patient` and `Doctor` profile models (OneToOne to User)
- [x] Patient self-registration flow + email verification (Gmail SMTP via env vars)
- [x] Doctor creation via Django admin custom form + verification email
- [x] Password reset flow
- [x] Login/logout, role-based redirect after login (patient → patient dashboard, doctor → doctor dashboard)
- [x] Test: unverified user cannot log in and gets resend-verification prompt

## Phase 2 — Public Pages
- [x] Home/landing page (Alef.ba-style: hero, features, CTAs)
- [x] Doctor listing page with specialty filter, card grid (drdr.ir-style, no ratings)
- [x] Doctor profile page (public) showing bio + available time slots

## Phase 3 — Appointments
- [x] `TimeSlot` model + doctor "define working hours" flow (Jalali date input → 30-min slot generation)
- [x] Slot generation dedupes against existing slots for that date; trailing-minute remainder handled per requirements.md §8
- [x] Doctor: delete a time slot (blocked if booked) — **test per requirements.md §8**
- [x] Patient: book a slot from doctor profile page, using `select_for_update()` transaction — **test the race condition per requirements.md §8**
- [x] Instant confirmation on booking (on-screen + email)
- [x] Patient: view/cancel appointments, 12-hour window enforced — **test per requirements.md §8**
- [x] Doctor: view today's / all appointments, add visit summary notes (visible to patient once completed)

## Phase 4 — Medications & Reminders
- [x] `Medication` model incl. `current_inventory`, `refill_reminder_threshold`, `refill_reminder_sent`; `MedicationSchedule` model (per requirements.md §3)
- [x] Set up `flatpickr` + Jalali locale plugin (or confirmed alternative, requirements.md §1 row 10); wire calendar picker, time picker, and time-range picker as the standard widgets — **retrofit the existing working-hours start/end time inputs (Phase 3) to the new time-range picker**
- [x] Add-medication step wizard: name+unit → frequency (radio) → dynamic schedule fields (per frequency type, using the new widgets) → dosage → inventory + refill threshold → save
- [x] Edit-medication flow: same wizard pre-filled; regenerates future `MedicationIntake` rows from the edit point forward
- [x] Delete-medication flow: confirm-and-remove, future pending intakes removed, past intake history retained
- [x] `frequency_type` as radio buttons; `specific_days` as checkboxes (multi-select, not radio — see requirements.md §9)
- [x] `MedicationIntake` generation task (7-day rolling window) on schedule create/update
- [x] Celery Beat: daily window-extension task
- [x] Celery Beat: per-minute reminder-send task, grouping same-time intakes into one email per patient
- [x] Celery Beat: missed-intake auto-transition (pending → missed at scheduled_time + 30min) — **test per requirements.md §8**
- [x] Signed-token one-click Taken/Skipped email links + corresponding views
- [x] Inventory decrement on `taken` + one-time refill-reminder email at threshold — **test per requirements.md §8**
- [x] Email-send failure handling: log, no infinite retry — **test per requirements.md §8**
- [x] Patient dashboard: today's medications widget with per-item Taken/Skipped actions **and** a "mark all as taken" bulk action per time-group (`/medications/intake/mark-batch/`) — **test per requirements.md §8**
- [x] Patient medication calendar view: Jalali calendar picker → selected date's medications grouped by time
- [x] Doctor: view/add/edit a patient's medications from that patient's appointment detail page, scoped to shared appointments only — **test the access-boundary per requirements.md §8**
- [x] Test: schedule generation correctness for each of the 4 frequency types

> **Note:** Rows 7–10 of requirements.md §1 (Open Decisions) remain marked ⏳ Default proposed. Phase 4 was implemented using those proposed defaults. Confirm or override before the next phase that touches the affected behavior:
> - Row 7: 30-minute grace window for missed-intake auto-transition
> - Row 8: Doctor access scope (any appointment status vs. only active/completed)
> - Row 9: Inventory decrement logic (decrement-by-1 vs. decrement-by-dose-quantity)
> - Row 10: flatpickr + Jalali locale plugin choice

## Phase 5 — Medical Test Results
- [x] `MedicalTestResult` model, upload form (PDF only via content-type/magic-byte check, not just extension; 5MB max, local filesystem storage)
- [x] List, download, delete views (patient-owned only, enforce ownership check)
- [x] Test: oversized file rejected, non-PDF disguised as `.pdf` rejected
- [x] doctor can see and download pateint's medical test result in booked slot detail

## Phase 6 — Dashboards
- [ ] Patient dashboard: upcoming appointments + today's medications (per-item + bulk "mark all as taken") + quick actions
- [ ] Doctor dashboard: today's appointments + quick stats + quick actions

## Phase 7 — Jalali Date Handling Pass
- [ ] Confirm all date inputs across the app use a Jalali picker/widget
- [ ] Confirm all stored dates are Gregorian/UTC in the DB
- [ ] Confirm all displayed dates/times are converted to Jalali + Asia/Tehran
- [ ] Strict validation + Persian error messages on malformed date input, no silent fallback — **test per requirements.md §8**

## Phase 8 — RTL & Visual Polish
- [ ] Full RTL audit (forms, tables, icons, spacing) across every page
- [ ] Apply MyTherapy-style visuals to medication views
- [ ] Apply drdr.ir-style visuals to doctor cards
- [ ] Apply Alef.ba-style visuals to home page

## Phase 9 — Edge Cases & Hardening
- [ ] Re-run all acceptance-criteria tests from requirements.md §8 as a final regression pass
- [ ] Permission checks: patients cannot access doctor routes and vice versa, and cannot access other patients' data (medications, test results, appointments) — add explicit tests
- [ ] Explicit test: a doctor can reach a patient's medications only via an appointment they share with that patient, never for an unrelated patient (requirements.md §8)
- [ ] Confirm no secrets appear in logs (especially email send errors)

## Phase 10 — Pre-Launch
- [ ] All secrets confirmed loaded from environment, none in source
- [ ] Rotate/replace any credential that was ever shared in plaintext (e.g. the Gmail app password from early planning notes)
- [ ] Basic seed data / fixtures for demo (sample doctors, slots)
- [ ] Manual end-to-end walkthrough: register → verify → book → get reminder → mark taken → cancel

---

## Localization Pass — Persian/RTL/Jalali → English/LTR/Gregorian (2026-08-24)

This reverses the original Persian/RTL/Jalali requirement. All dates are now Gregorian throughout; no Jalali conversion layer remains. `TIME_ZONE=Asia/Tehran` and all datetime math are unchanged.

- [x] **Settings & static assets:** `LANGUAGE_CODE="en"`, removed `dir="rtl"` from CSS, replaced Vazirmatn font with English font stack, removed `flatpickr-fa.js`/`jalali-date.js`, replaced `flatpickr-jalali-init.js` with standard Gregorian `flatpickr-init.js`
- [x] **Base template:** `lang="en" dir="ltr"`, all nav/footer text English, removed Jalali JS includes
- [x] **Forms:** `JalaliDateField` → `GregorianDateField` (accepts YYYY/MM/DD directly), all labels/help texts/validation messages English
- [x] **Models:** all `verbose_name`/`choices` values English, removed `jalali_date` property from `TimeSlot`
- [x] **Views:** removed all `jdatetime` usage, all `messages.*` strings English, dates passed through as Gregorian
- [x] **All templates:** Persian text → English, `slot.jalali_date` → `slot.date|date:"Y/m/d"`, Jalali calendar JS → standard flatpickr
- [x] **Email templates:** Persian → English, `dir="rtl"` → `dir="ltr"`, Vazirmatn → system font stack
- [x] **Admin:** Persian `verbose_name`/`description` strings → English
- [x] **Tests:** Persian assertions → English, removed `import jdatetime`, Jalali date strings → Gregorian
- [x] **Docs:** `requirements.md` updated (§0, §7, §8, §9), `plan.md` updated with this phase

> **Note:** Phase 7 (Jalali Date Handling Pass) and Phase 8 (RTL & Visual Polish) from the original plan are superseded by this localization pass and are no longer applicable.

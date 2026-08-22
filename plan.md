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
- [ ] `TimeSlot` model + doctor "define working hours" flow (Jalali date input → 30-min slot generation)
- [ ] Slot generation dedupes against existing slots for that date; trailing-minute remainder handled per requirements.md §8
- [ ] Doctor: delete a time slot (blocked if booked) — **test per requirements.md §8**
- [ ] Patient: book a slot from doctor profile page, using `select_for_update()` transaction — **test the race condition per requirements.md §8**
- [ ] Instant confirmation on booking (on-screen + email)
- [ ] Patient: view/cancel appointments, 12-hour window enforced — **test per requirements.md §8**
- [ ] Doctor: view today's / all appointments, add visit summary notes (visible to patient once completed)

## Phase 4 — Medications & Reminders
- [ ] `Medication` + `MedicationSchedule` models and CRUD UI
- [ ] Schedule form supporting all 4 frequency types with correct conditional fields
- [ ] `MedicationIntake` generation task (7-day rolling window) on schedule create/update
- [ ] Celery Beat: daily window-extension task
- [ ] Celery Beat: per-minute reminder-send task, grouping same-time intakes into one email per patient
- [ ] Signed-token one-click Taken/Skipped email links + corresponding views
- [ ] Email-send failure handling: log, no infinite retry — **test per requirements.md §8**
- [ ] Patient dashboard: today's medications widget with Taken/Skipped actions
- [ ] Test: schedule generation correctness for each of the 4 frequency types

## Phase 5 — Medical Test Results
- [ ] `MedicalTestResult` model, upload form (PDF only via content-type/magic-byte check, not just extension; 5MB max, local filesystem storage)
- [ ] List, download, delete views (patient-owned only, enforce ownership check)
- [ ] Test: oversized file rejected, non-PDF disguised as `.pdf` rejected

## Phase 6 — Dashboards
- [ ] Patient dashboard: upcoming appointments + today's medications + quick actions
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
- [ ] Confirm no secrets appear in logs (especially email send errors)

## Phase 10 — Pre-Launch
- [ ] All secrets confirmed loaded from environment, none in source
- [ ] Rotate/replace any credential that was ever shared in plaintext (e.g. the Gmail app password from early planning notes)
- [ ] Basic seed data / fixtures for demo (sample doctors, slots)
- [ ] Manual end-to-end walkthrough: register → verify → book → get reminder → mark taken → cancel

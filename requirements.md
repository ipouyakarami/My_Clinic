# MyClinic — Product & Technical Specification

> **Amendment (2026-08-24):** This project has been converted from Persian/RTL/Jalali to **English/LTR/Gregorian**. All UI text is now in English, layout direction is `dir="ltr"`, and all dates use the plain Gregorian calendar. The Jalali↔Gregorian conversion layer has been removed entirely — dates are stored, displayed, and entered as Gregorian. `TIME_ZONE=Asia/Tehran` and all datetime math remain unchanged. This reverses the original Persian/RTL/Jalali requirement.

## 0. Overview

MyClinic is an English-language, LTR, web application for booking in-person doctor appointments and managing patient medication schedules with email reminders. All dates are displayed and entered in the Gregorian calendar. All times are Iran Standard Time (`Asia/Tehran`) unless otherwise noted.

Reference designs:
- **Home/landing page** — style of Alef.ba (clean, minimal, hero + features + CTAs, typography-focused, generous whitespace).
- **Doctor listing/cards** — style of drdr.ir (grid of doctor cards, filter bar), but **without ratings, reviews, or recommendation percentages**.
- **Medication tracking UI** — style of MyTherapy (simple daily schedule view, Taken/Skipped actions, health-oriented color palette).

---

## 1. Open Decisions — Resolve Before / During Build

These were not fully specified in the source requirements. Reasonable defaults are proposed; confirm or override before the relevant phase starts.

| # | Question | Proposed default | Final decision |
|---|---|---|---|
| 1 | Timezone for all datetime math (reminders, "within 12 hours" cancellation window) | `Asia/Tehran`, fixed (no DST assumptions) | ✅ Default adopted — `Asia/Tehran` for all datetime math |
| 2 | Working-hour range doesn't divide evenly into 30-min slots (e.g. 09:00–09:45) | Generate full slots only; drop the leftover remainder, show doctor a warning at entry time listing how many minutes were dropped | ✅ Default adopted — full slots only + trailing-minutes warning |
| 3 | `every_x_hours` reminder end condition | Recurs from anchor start datetime every X hours until `end_date` (23:59:59 that day); if `end_date` is null, recurs indefinitely until medication is marked inactive | ✅ Default adopted — anchor-based recurrence, capped by `end_date` 23:59:59 if set, else indefinite until deactivation (still bounded by the 7-day generation window) |
| 4 | Test framework / CI | Django's built-in `TestCase` (pytest-django is an acceptable substitute) — at minimum, tests required for booking race condition, cancellation window, and reminder generation logic (see §8 acceptance criteria) | ✅ Default adopted — Django built-in `TestCase`; all §8 criteria get automated tests |
| 5 | What "instant confirmation" shows the patient | On-screen success message + confirmation email; no SMS | ✅ Default adopted — on-screen + confirmation email, no SMS |
| 6 | Doctor visit summary — can patient see it? | Yes, read-only, visible on the patient's appointment detail page once status is `completed` | ✅ Default adopted — patient sees summary read-only once `completed` |
| 7 | Un-actioned reminder — what happens if patient never taps Taken/Skipped? | A Celery Beat task (runs every minute, same cadence as the reminder task) auto-transitions any `MedicationIntake` still `pending` **30 minutes** after `scheduled_time` to a new `missed` status. `reminder_sent` is unaffected; no further email is sent for that intake. | ✅ Default adopted — 30-minute grace window, no "missed" notification email |
| 8 | Doctor access to a patient's medication schedule | A doctor may view, add, and edit medication/schedule records **only** for a patient with whom they have at least one `Appointment` (any status), accessed via that appointment's detail page. No open-ended doctor→patient medication access outside an appointment relationship. | ✅ Default adopted — any appointment status grants access |
| 9 | Inventory decrement & refill-reminder trigger | `Medication.current_inventory` decrements by 1 each time an intake tied to it is marked `taken` (not on `skipped`/`missed`). When `current_inventory <= refill_reminder_threshold`, a one-time "time to refill" email is sent (separate template from the dose reminder) and `refill_reminder_sent=True` is set so it doesn't repeat; it resets to `False` whenever the patient edits `current_inventory` back above the threshold. | ✅ Default adopted — decrement-by-1 per taken intake |
| 10 | Frontend date/time picker library | `flatpickr` + a Jalali/Persian locale plugin (e.g. `jalali-flatpickr`) for calendar inputs; `flatpickr`'s time mode (`enableTime`, `noCalendar: true`) for single time inputs; two linked `flatpickr` time instances (or its `mode: "range"`) for start+end time inputs. One library covers all four widget types needed. | ✅ Default adopted — flatpickr + custom Jalali conversion JS (jalali-date.js) |

---

## 2. Tech Stack

| Layer | Choice |
|---|---|
| Backend + Frontend | Django (server-side rendered templates) |
| Auth | django-allauth integrated with a custom `AbstractBaseUser` model, email as `USERNAME_FIELD` |
| Database | PostgreSQL |
| Background jobs / scheduling | Celery + Redis (Celery Beat for periodic tasks) |
| Email | Gmail SMTP — credentials must be supplied via environment variables (`EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`), never committed to source control |
| File storage | Local filesystem (PDFs, profile pictures) |
| Dates | Gregorian `DateField`/`DateTimeField` in the DB, stored in UTC internally, rendered in `Asia/Tehran`; no calendar conversion — Gregorian throughout |
| CSS | Tailwind CSS (or equivalent), LTR layout via `dir="ltr"` and CSS logical properties |
| Date/time input widgets | `flatpickr` (default Gregorian mode) — used as: calendar picker (all date fields), time picker (single-time fields), time-range picker (start+end time fields, e.g. doctor working hours) — see §1 row 10 |
| Payments | None — booking is free |
| Tests | Django `TestCase` (or pytest-django) |

**Environment variables required (document in `.env.example`, never hardcode):**
```
DJANGO_SECRET_KEY=
DATABASE_URL=
REDIS_URL=
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
DEFAULT_FROM_EMAIL=
TIME_ZONE=Asia/Tehran
```

---

## 3. Data Model

### User (custom `AbstractBaseUser`)
- `email` (unique, `USERNAME_FIELD`)
- `first_name`, `last_name`
- `is_active`, `is_staff`, `is_superuser`
- `user_type`: `patient` | `doctor`
- `date_joined`, `last_login`

### Patient (OneToOne → User)
- `phone_number` (optional)

### Doctor (OneToOne → User)
- `specialty` (CharField)
- `description` (TextField)
- `profile_picture` (ImageField, local storage)
- `is_verified` (Boolean)

### TimeSlot
- `doctor` (FK → Doctor)
- `date` (Gregorian DateField, stored converted from Jalali input)
- `start_time`, `end_time` (TimeField, 30-minute increments)
- `is_booked` (Boolean)
- `created_at`

### Appointment
- `patient` (FK → Patient)
- `time_slot` (OneToOne → TimeSlot)
- `status`: `active` | `completed` | `cancelled`
- `doctor_summary` (TextField, nullable — doctor's notes after visit; visible to patient once `completed`)
- `created_at`, `cancelled_at` (nullable)

### MedicalTestResult
- `patient` (FK → Patient)
- `category` (CharField)
- `pdf_file` (FileField, max 5MB, PDF only)
- `uploaded_at`

### Medication
- `patient` (FK → Patient)
- `name`, `unit` (CharField)
- `dosage` (CharField — amount taken per intake, e.g. "1 tablet", "5ml")
- `current_inventory` (Integer — units currently on hand, patient-entered/edited)
- `refill_reminder_threshold` (Integer, nullable — "remind me when inventory reaches this" value; null = refill reminders off)
- `refill_reminder_sent` (Boolean, default `False` — set once the refill email fires; reset to `False` when `current_inventory` is edited back above the threshold)
- `is_active` (Boolean)
- `created_at`

### MedicationSchedule
- `medication` (FK → Medication)
- `frequency_type`: `daily` | `specific_days` | `every_n_days` | `every_x_hours`
- `specific_days` (JSONField, e.g. `["sat","mon","wed"]`) — used when `specific_days`
- `n_days_interval` (Integer, nullable) — used when `every_n_days`
- `x_hours_interval` (Integer, nullable) — used when `every_x_hours`
- `medication_times` (JSONField, list of `"HH:MM"`) — used for `daily`/`specific_days`/`every_n_days`
- `anchor_datetime` (DateTimeField, nullable) — used when `every_x_hours`, first reminder instant
- `start_date`, `end_date` (nullable) — stored as Gregorian, entered/displayed as Jalali
- `created_at`, `updated_at`

### MedicationIntake
- `medication` (FK → Medication)
- `scheduled_time` (DateTimeField)
- `status`: `pending` | `taken` | `skipped` | `missed` (auto-set — see §1 row 7)
- `recorded_at` (DateTimeField, nullable)
- `reminder_sent` (Boolean)
- `token` (unique signed token for one-click email actions)

---

## 4. Auth Flows

- **Patient self-registration:** register → account created with `is_active=False` → verification email sent → patient clicks link, sets password → account activated. If an unverified patient attempts login, resend verification email and show "check your inbox" message (do not reveal whether email exists, for privacy).
- **Doctor registration:** admin-only, via Django admin (custom form: email, name, specialty, description). Verification email sent to doctor to activate and set password.
- **Password reset:** standard django-allauth flow (request via email → reset link → new password).
- **Login/logout:** standard allauth, email-based.

---

## 5. Routes / Pages

### Public (no login required)
- `/` — Home/landing page
- `/doctors/` — Doctor listing (grid, filter by specialty)
- `/doctors/<id>/` — Doctor profile + available time slots

### Patient (login + patient role required)
- `/dashboard/` — Upcoming appointments, today's medications, quick actions
- `/appointments/` — My appointments (list, filter by status)
- `/appointments/<id>/cancel/` — Cancel appointment (blocked if within 12 hours of slot)
- `/medications/` — Medication list
- `/medications/add/` — Add medication: single-page step wizard (name+unit → frequency → schedule fields → dosage → inventory+refill threshold), see §9 for step detail
- `/medications/<id>/edit/` — Edit a medication and its schedule (same step-wizard UI, pre-filled); regenerates future `MedicationIntake` rows from the edit point forward
- `/medications/<id>/delete/` — Delete a medication (and its schedule + future pending intakes; past intake history retained)
- `/medications/calendar/` — Calendar picker (Gregorian); selecting a date shows that day's medications grouped/sorted by time
- `/medications/calendar/<date>/` — Same view, direct link for a specific date
- `/medications/intake/<token>/taken/` — One-click mark taken (from email)
- `/medications/intake/<token>/skipped/` — One-click mark skipped (from email)
- `/medications/intake/mark-batch/` — POST: mark every `pending` intake sharing one `scheduled_time` (the "mark all as taken" dashboard button) as `taken` in one action
- `/medical-tests/` — Medical test results list
- `/medical-tests/add/` — Upload PDF (max 5MB)
- `/medical-tests/<id>/download/`
- `/medical-tests/<id>/delete/`
- `/profile/` — Edit profile

### Doctor (login + doctor role required)
- `/dashboard/` — Today's appointments, quick stats, quick actions
- `/working-hours/` — Define working hours for a date (generates 30-min TimeSlots)
- `/working-hours/<id>/delete/` — Remove a slot (blocked if booked)
- `/appointments/` — All appointments
- `/appointments/today/`
- `/appointments/<id>/` — Appointment detail, add visit summary
- `/appointments/<id>/medications/` — View that patient's medication schedule (read panel embedded in/linked from appointment detail; access requires an `Appointment` between this doctor and this patient — see §1 row 8)
- `/appointments/<id>/medications/add/` — Add a medication for that patient (same step wizard as the patient-facing one)
- `/appointments/<id>/medications/<med_id>/edit/` — Edit that patient's medication/schedule
- `/profile/` — Edit profile

### Auth (django-allauth)
- `/accounts/login/`, `/accounts/register/patient/`, `/accounts/verify-email/`, `/accounts/password/reset/`, `/accounts/logout/`

---

## 6. Medication Reminder System (Celery)

**Approach:** pre-generate, don't compute on the fly per-minute.

1. When a `MedicationSchedule` is created or updated, a Celery task generates `MedicationIntake` records (`status=pending`) for the next **7 days** based on the frequency rule.
2. A Celery Beat task runs **daily** to extend the generated window by another day (rolling 7-day horizon), and a separate Beat task runs **every minute** to:
   - Find `MedicationIntake` rows where `scheduled_time` has arrived (in `Asia/Tehran`) and `reminder_sent=False`.
   - Group by patient + exact scheduled time (so multiple medications due at once go in **one email** with individual Taken/Skipped links per medication).
   - Send the email, set `reminder_sent=True`.
3. Each intake gets a unique signed `token` (Django `signing.dumps`, not guessable). Email buttons link to `/medications/intake/<token>/taken/` and `/skipped/`. These require login; if not logged in, redirect to login then back to the action.
4. Frequency rule generation:
   - `daily` + `medication_times`: one intake per listed time, every day, within `[start_date, end_date]`.
   - `specific_days` + `medication_times`: one intake per listed time, only on listed weekdays.
   - `every_n_days` + `medication_times`: one intake per listed time, every N days counted from `start_date`.
   - `every_x_hours`: intakes recur every X hours from `anchor_datetime`, until `end_date` 23:59:59 (or indefinitely if null, capped at the 7-day generation window like everything else).
5. **Missed-intake transition (new):** the same per-minute Beat task (or a sibling task on the same schedule) also finds `MedicationIntake` rows still `status=pending` where `scheduled_time + 30 minutes <= now()` and sets `status=missed`. No email is sent for a `missed` transition; it only affects what the dashboard/calendar show. See §1 row 7.
6. **Bulk "mark all as taken" (new):** the patient dashboard's today's-medications widget groups intakes by identical `scheduled_time` and shows one "Mark all as taken" button per group, alongside the existing per-medication Taken/Skipped controls, posting to `/medications/intake/mark-batch/`.
7. **Inventory & refill reminder (new):** whenever an intake transitions to `taken` (via email link, dashboard action, or bulk action), decrement that medication's `current_inventory` by 1 (floor at 0). After the decrement, if `current_inventory <= refill_reminder_threshold` and `refill_reminder_sent=False`, send a "time to refill" email (separate template from the dose reminder — names the medication and remaining count) and set `refill_reminder_sent=True`. Editing `current_inventory` back above the threshold resets `refill_reminder_sent=False`. See §1 row 9.

---

## 7. Gregorian Date Handling

- All dates/datetimes are stored as **Gregorian/UTC** in PostgreSQL.
- All dates are displayed and entered as **Gregorian** — no calendar conversion is applied at the presentation or input layer.
- Date input uses the default Gregorian mode of the `flatpickr` picker widget.
- Validate date input strictly; reject malformed dates with a clear English-language error, without silently falling back to a default date.

---

## 8. Edge Cases, Validation Rules & Acceptance Criteria

For each risky behavior, an agent implementing it should write an automated test matching the acceptance criteria below.

### Double-booking race condition
- **Rule:** two patients attempt to book the same open slot at nearly the same time.
- **Acceptance criteria:** wrap the booking check + `Appointment` creation + `TimeSlot.is_booked=True` update in a single DB transaction using `select_for_update()` on the `TimeSlot` row. The second request must fail with a user-facing error ("This slot has just been booked") and be redirected back to the doctor's available-slots list. Test: simulate two near-simultaneous booking calls against the same slot; assert exactly one `Appointment` is created and the slot ends up `is_booked=True`.

### Cancellation window
- **Rule:** patient cannot cancel within 12 hours of the appointment's `start_time` (computed in `Asia/Tehran`).
- **Acceptance criteria:** cancel view checks `time_slot.start_datetime - now() < timedelta(hours=12)`; if true, return an error and do not change status. Test: appointment 11 hours out → cancel blocked; appointment 13 hours out → cancel succeeds, status becomes `cancelled`, `cancelled_at` set, slot's `is_booked` reset to `False` so it becomes bookable again.

### Working-hour slot generation
- **Rule:** doctor enters a Gregorian date + start/end time; system generates 30-minute `TimeSlot`s.
- **Acceptance criteria:** existing slots for that date are never duplicated (dedupe by `date` + `start_time`); if the entered range doesn't divide evenly into 30-minute blocks, generate full slots only and show the doctor how many trailing minutes were dropped. Test: 09:00–10:45 entered → slots for 09:00, 09:30, 10:00, 10:30 created; UI shows "15 minutes could not be scheduled."

### Deleting a booked slot
- **Rule:** doctor cannot delete a `TimeSlot` where `is_booked=True`.
- **Acceptance criteria:** delete view returns an error and takes no action if booked. Test: attempt delete on booked slot → 4xx/error message, slot still exists.

### Medication reminder bounce
- **Rule:** email send failure should not retry indefinitely.
- **Acceptance criteria:** Celery task catches send exceptions, logs them with the intake ID, and does **not** re-attempt that specific reminder (leave `reminder_sent=False` but record a `last_error` note, or a separate `EmailFailureLog`, so it's visible in admin — do not loop).

### File upload validation
- **Rule:** medical test uploads must be PDF, max 5MB.
- **Acceptance criteria:** form validation rejects non-PDF content-types and files over 5MB with a specific English-language error naming which rule failed (type vs size). Test: 6MB PDF rejected with size error; valid `.docx` renamed to `.pdf` rejected via content-type/magic-byte check, not just extension.

### Gregorian date parse failure
- **Rule:** malformed Gregorian date input must not silently default.
- **Acceptance criteria:** form-level validation error shown inline, field retains user's raw input, no date is saved.

### Missed-intake auto-transition
- **Rule:** an intake untouched 30 minutes past its `scheduled_time` becomes `missed`, not left `pending` forever.
- **Acceptance criteria:** the Beat task only touches rows that are still `pending` at `scheduled_time + 30min`; a row marked `taken`/`skipped` before that moment is never overwritten. Test: intake at T with no action → status is `missed` at T+31min; intake at T marked `taken` at T+5min → still `taken` at T+31min (task is a no-op for it).

### Bulk "mark all as taken"
- **Rule:** the batch-complete action must only affect the requesting patient's own intakes at that exact time.
- **Acceptance criteria:** `/medications/intake/mark-batch/` only updates `pending` intakes belonging to the logged-in patient with the given `scheduled_time`; intakes for other patients, or already `taken`/`skipped`/`missed`, are untouched. Test: two patients each have an intake at the same clock time → one patient's bulk action does not affect the other's row.

### Inventory decrement & refill reminder
- **Rule:** refill email fires once per threshold crossing, not on every subsequent `taken` intake.
- **Acceptance criteria:** decrement happens only on transition to `taken`; refill email sends the first time `current_inventory` drops to/below `refill_reminder_threshold`, and `refill_reminder_sent=True` suppresses repeats until inventory is edited back above the threshold. Test: threshold=5, inventory drops 6→5 → one email sent; further `taken` intakes (5→4→3…) send no additional email; patient edits inventory to 10 → next drop to ≤5 sends exactly one more email.

### Doctor access boundary on patient medications
- **Rule:** a doctor may only reach a patient's medication data through an appointment they share with that patient.
- **Acceptance criteria:** `/appointments/<id>/medications/...` views 403/404 if the requesting doctor is not the doctor on that appointment's `Appointment.patient` relationship; a doctor cannot reach another patient's medications by guessing IDs, and cannot use these routes at all for a patient they have no appointment with. Test: Doctor A with an appointment with Patient X can view/edit Patient X's medications; Doctor A attempting the same for Patient Y (no shared appointment) is denied.

---

## 9. UI Requirements

- Entire app in **English**, `dir="ltr"`, left-to-right layout throughout (including forms, tables, navigation).
- **Home page:** hero section, feature highlights, description, Login/Register CTAs — Alef.ba-inspired.
- **Doctor cards:** profile picture, name, specialty, "Book Appointment" button. No ratings/reviews/recommendation %.
- **Doctor profile page:** picture, name, specialty, description, calendar/slot picker for booking.
- **Form input component standard (new):** applies wherever the field type matches, across the app —
  - Any date-only field → Gregorian **calendar picker** (`flatpickr`, default mode). Applies to: `MedicationSchedule.start_date`/`end_date`, working-hours date entry, medication calendar view.
  - Any field with a fixed set of mutually-exclusive options → **radio buttons** (not a `<select>`). Applies to: `frequency_type` (`daily` / `specific_days` / `every_n_days` / `every_x_hours`). Note: `specific_days` itself is a *multi*-select (several weekdays at once), so it uses **checkboxes**, not radio buttons, even though it's a "limited options" field.
  - Any field capturing a single point in time (start time only, no matching end) → **time picker**. Applies to: each entry in `medication_times`, the time component of `anchor_datetime`.
  - Any field pair capturing a start **and** end time → **time-range picker**. Applies to: doctor working-hours `start_time`/`end_time` (Phase 3) — the one existing start+end time input in the app today.
- **Medication schedule UI — add/edit flow (step wizard, MyTherapy-style):**
  1. Name + unit
  2. Frequency type — radio buttons: `daily` / `specific_days` / `every_n_days` / `every_x_hours`
  3. Schedule fields shown dynamically based on step 2's choice (per the component standard above): `daily` → time picker(s) for `medication_times` + calendar pickers for `start_date`/`end_date`; `specific_days` → weekday checkboxes + time picker(s) + start/end calendar pickers; `every_n_days` → numeric interval + time picker(s) + start/end calendar pickers; `every_x_hours` → calendar+time picker for `anchor_datetime`, numeric hour interval, optional end-date calendar picker
  4. Dosage (amount per intake)
  5. Current inventory (numeric) + "remind me when inventory reaches ___" refill threshold (numeric, optional)
  6. Review & save
  - Edit reuses the same wizard, pre-filled; Delete is a confirm-and-remove action from the medication list.
- **Medication calendar view (new):** a Gregorian calendar picker; selecting a date lists that day's scheduled intakes sorted/grouped by time.
- **Taken/Skipped controls:** per-medication buttons, plus a **"Mark all as taken"** button per time-group on the dashboard's today's-medications widget (bulk action, see §6.6/§8).
- **Doctor's patient-medication panel (new):** reachable from an appointment's detail page — view, add, and edit that specific patient's medications/schedules using the same components and wizard described above.
- **Patient dashboard:** upcoming appointments, today's medications (with per-item and bulk actions), quick actions (Book Appointment, Add Medication, View Medical Tests).
- **Doctor dashboard:** today's appointments, quick stats, quick actions (Define Working Hours, View All Appointments).

---

## 10. Non-Functional Requirements

- All appointment confirmations are **instant** (no approval step, no payment) — on-screen message + confirmation email.
- No online payment integration of any kind — booking is entirely free.
- Only in-person appointment types are supported (no telehealth/video in this version).
- Non-logged-in visitors can browse `/`, `/doctors/`, and doctor profile pages.
- Secrets (DB, email, Django secret key) must load from environment variables only, never committed.
- All server-side datetime logic uses `Asia/Tehran` consistently; store UTC in the DB, convert at the edges.

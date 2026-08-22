# MyClinic — Product & Technical Specification

## 0. Overview

MyClinic is a Persian-language (Farsi), RTL, web application for booking in-person doctor appointments and managing patient medication schedules with email reminders. All dates are displayed and entered in the Jalali (Persian) calendar. All times are Iran Standard Time (`Asia/Tehran`) unless otherwise noted.

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
| Dates | Gregorian `DateField`/`DateTimeField` in the DB, stored in UTC internally, rendered in `Asia/Tehran`; Jalali conversion for all display and input via a library such as `jdatetime` / `django-jalali-date` |
| CSS | Tailwind CSS (or equivalent), full RTL support via `dir="rtl"` and CSS logical properties |
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
- `name`, `dosage`, `unit` (CharField)
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
- `status`: `pending` | `taken` | `skipped`
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
- `/medications/add/` — Add medication
- `/medications/<id>/schedule/` — Add/edit schedule
- `/medications/intake/<token>/taken/` — One-click mark taken (from email)
- `/medications/intake/<token>/skipped/` — One-click mark skipped (from email)
- `/medical-tests/` — Medical test results list
- `/medical-tests/add/` — Upload PDF (max 5MB)
- `/medical-tests/<id>/download/`
- `/medical-tests/<id>/delete/`
- `/profile/` — Edit profile

### Doctor (login + doctor role required)
- `/dashboard/` — Today's appointments, quick stats, quick actions
- `/working-hours/` — Define working hours for a Jalali date (generates 30-min TimeSlots)
- `/working-hours/<id>/delete/` — Remove a slot (blocked if booked)
- `/appointments/` — All appointments
- `/appointments/today/`
- `/appointments/<id>/` — Appointment detail, add visit summary
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

---

## 7. Jalali Date Handling

- Store all dates/datetimes as **Gregorian/UTC** in PostgreSQL (needed for correct sorting/range queries and Celery scheduling math).
- Convert to/from Jalali, in `Asia/Tehran`, only at the **presentation and input layer** using `jdatetime` (or `django-jalali-date` widgets for forms).
- Validate Jalali input strictly; reject malformed dates with a clear Persian-language error, without silently falling back to a default date.

---

## 8. Edge Cases, Validation Rules & Acceptance Criteria

For each risky behavior, an agent implementing it should write an automated test matching the acceptance criteria below.

### Double-booking race condition
- **Rule:** two patients attempt to book the same open slot at nearly the same time.
- **Acceptance criteria:** wrap the booking check + `Appointment` creation + `TimeSlot.is_booked=True` update in a single DB transaction using `select_for_update()` on the `TimeSlot` row. The second request must fail with a user-facing error ("این نوبت قبلاً رزرو شده است" / "This slot has just been booked") and be redirected back to the doctor's available-slots list. Test: simulate two near-simultaneous booking calls against the same slot; assert exactly one `Appointment` is created and the slot ends up `is_booked=True`.

### Cancellation window
- **Rule:** patient cannot cancel within 12 hours of the appointment's `start_time` (computed in `Asia/Tehran`).
- **Acceptance criteria:** cancel view checks `time_slot.start_datetime - now() < timedelta(hours=12)`; if true, return an error and do not change status. Test: appointment 11 hours out → cancel blocked; appointment 13 hours out → cancel succeeds, status becomes `cancelled`, `cancelled_at` set, slot's `is_booked` reset to `False` so it becomes bookable again.

### Working-hour slot generation
- **Rule:** doctor enters a Jalali date + start/end time; system generates 30-minute `TimeSlot`s.
- **Acceptance criteria:** existing slots for that date are never duplicated (dedupe by `date` + `start_time`); if the entered range doesn't divide evenly into 30-minute blocks, generate full slots only and show the doctor how many trailing minutes were dropped. Test: 09:00–10:45 entered → slots for 09:00, 09:30, 10:00, 10:30 created; UI shows "15 minutes could not be scheduled."

### Deleting a booked slot
- **Rule:** doctor cannot delete a `TimeSlot` where `is_booked=True`.
- **Acceptance criteria:** delete view returns an error and takes no action if booked. Test: attempt delete on booked slot → 4xx/error message, slot still exists.

### Medication reminder bounce
- **Rule:** email send failure should not retry indefinitely.
- **Acceptance criteria:** Celery task catches send exceptions, logs them with the intake ID, and does **not** re-attempt that specific reminder (leave `reminder_sent=False` but record a `last_error` note, or a separate `EmailFailureLog`, so it's visible in admin — do not loop).

### File upload validation
- **Rule:** medical test uploads must be PDF, max 5MB.
- **Acceptance criteria:** form validation rejects non-PDF content-types and files over 5MB with a specific Persian-language error naming which rule failed (type vs size). Test: 6MB PDF rejected with size error; valid `.docx` renamed to `.pdf` rejected via content-type/magic-byte check, not just extension.

### Jalali date parse failure
- **Rule:** malformed Jalali date input must not silently default.
- **Acceptance criteria:** form-level validation error shown inline, field retains user's raw input, no date is saved.

---

## 9. UI Requirements

- Entire app in **Persian**, `dir="rtl"`, right-to-left layout throughout (including forms, tables, navigation).
- **Home page:** hero section, feature highlights, description, Login/Register CTAs — Alef.ba-inspired.
- **Doctor cards:** profile picture, name, specialty, "Book Appointment" button. No ratings/reviews/recommendation %.
- **Doctor profile page:** picture, name, specialty, description, calendar/slot picker for booking.
- **Medication schedule UI:** MyTherapy-style daily view, add-medication form, schedule form (frequency type + times + start/end Jalali dates), Taken/Skipped controls.
- **Patient dashboard:** upcoming appointments, today's medications, quick actions (Book Appointment, Add Medication, View Medical Tests).
- **Doctor dashboard:** today's appointments, quick stats, quick actions (Define Working Hours, View All Appointments).

---

## 10. Non-Functional Requirements

- All appointment confirmations are **instant** (no approval step, no payment) — on-screen message + confirmation email.
- No online payment integration of any kind — booking is entirely free.
- Only in-person appointment types are supported (no telehealth/video in this version).
- Non-logged-in visitors can browse `/`, `/doctors/`, and doctor profile pages.
- Secrets (DB, email, Django secret key) must load from environment variables only, never committed.
- All server-side datetime logic uses `Asia/Tehran` consistently; store UTC in the DB, convert at the edges.

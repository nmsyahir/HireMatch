# HireMatch Job Portal

Django-based job portal for job seekers and employers with resume upload, job recommendations, applications, saved jobs, chat, employer analytics, and applicant ranking.

## Setup

```powershell
cd C:\Users\nikgr\FYP\job-portal
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Open `http://127.0.0.1:8000/`.

## Environment

Settings are loaded from `.env`. Use `.env.example` as the template when moving to another machine.

Important values:

- `SECRET_KEY`
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`
- `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`

## Demo Accounts

To create sample users and jobs:

```powershell
python manage.py seed_demo
```

Accounts:

- Employer: `employer@demo.com` / `Password123`
- Job seeker: `jobseeker@demo.com` / `Password123`

## Notes

- Do not share real `.env` values publicly.
- `venv/`, `__pycache__/`, and `.pyc` files are not required in the project zip.
- Run `python manage.py migrate` after extracting a new version that includes migrations.
- Real `.env` files, databases, private resumes, and user uploads must not be included in a submission ZIP.
- Resume files are stored under `private_media/` and can only be opened through an authorized HireMatch session.
- OTP codes expire after 5 minutes, allow 5 attempts, and have a 60-second resend cooldown.

## Render Deployment

Create a Render PostgreSQL database and a Python Web Service connected to this repository.

Build command:

```bash
bash build.sh
```

Start command:

```bash
gunicorn core.wsgi:application
```

Required Render environment variables:

- `DEBUG=False`
- `SECRET_KEY=<long random value>`
- `DATABASE_URL=<Render PostgreSQL internal URL>`
- `CLOUDINARY_URL=cloudinary://<api-key>:<api-secret>@<cloud-name>`
- `ALLOWED_HOSTS=<your-service>.onrender.com`
- `CSRF_TRUSTED_ORIGINS=https://<your-service>.onrender.com`
- `EMAIL_HOST_USER=<Gmail address>`
- `EMAIL_HOST_PASSWORD=<Gmail app password>`

When `CLOUDINARY_URL` is configured, company logos and profile pictures use Cloudinary media storage. Resumes are uploaded as authenticated raw assets and remain accessible only through HireMatch's authorized resume endpoint.

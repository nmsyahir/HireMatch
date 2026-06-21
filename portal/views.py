import re
import secrets
import PyPDF2
from datetime import timedelta
from functools import wraps
from pathlib import Path

from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.messages import get_messages
from django.core.mail import send_mail
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.contrib.auth.password_validation import validate_password
from django.conf import settings
from django.http import FileResponse, HttpResponse
from django.http import JsonResponse
from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone
from django.views.decorators.http import require_POST

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .models import Job, Company, Resume, Recommendation, UserProfile, Application, SavedJob, ChatMessage, Notification
from .forms import JobForm
from .validators import validate_image_upload, validate_resume_upload


# =========================
# HELPER
# =========================
OTP_TTL = timedelta(minutes=5)
OTP_RESEND_COOLDOWN = timedelta(seconds=60)
OTP_MAX_ATTEMPTS = 5
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT = timedelta(minutes=5)


def role_required(required_role):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            user_id = request.session.get('user_id')
            if not user_id:
                return redirect('login')

            user = UserProfile.objects.filter(id=user_id).first()
            if not user:
                request.session.flush()
                return redirect('login')

            if user.role != required_role:
                messages.error(request, 'Access denied.')
                destination = 'employer_dashboard' if user.role == 'employer' else 'dashboard'
                return redirect(destination)

            request.portal_user = user
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator


def send_otp(user, subject, message_template):
    now = timezone.now()
    if user.otp_last_sent_at and now - user.otp_last_sent_at < OTP_RESEND_COOLDOWN:
        seconds = int((OTP_RESEND_COOLDOWN - (now - user.otp_last_sent_at)).total_seconds()) + 1
        return False, f'Please wait {seconds} seconds before requesting another OTP.'

    otp = f'{secrets.randbelow(900000) + 100000:06d}'
    send_mail(
        subject,
        message_template.format(otp=otp),
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False
    )

    user.otp_code = otp
    user.otp_created_at = now
    user.otp_last_sent_at = now
    user.otp_attempts = 0
    user.save(update_fields=['otp_code', 'otp_created_at', 'otp_last_sent_at', 'otp_attempts'])
    return True, 'OTP has been sent to your email.'


def clear_otp(user):
    user.otp_code = ''
    user.otp_created_at = None
    user.otp_attempts = 0
    user.save(update_fields=['otp_code', 'otp_created_at', 'otp_attempts'])


def check_otp(user, entered_otp):
    now = timezone.now()
    if not user.otp_code or not user.otp_created_at or now - user.otp_created_at > OTP_TTL:
        clear_otp(user)
        return False, 'OTP has expired. Please request a new code.'

    if user.otp_attempts >= OTP_MAX_ATTEMPTS:
        clear_otp(user)
        return False, 'Too many incorrect attempts. Please request a new OTP.'

    if not entered_otp.isdigit() or len(entered_otp) != 6 or entered_otp != user.otp_code:
        user.otp_attempts += 1
        attempts_left = OTP_MAX_ATTEMPTS - user.otp_attempts
        if attempts_left <= 0:
            clear_otp(user)
            return False, 'Too many incorrect attempts. Please request a new OTP.'
        user.save(update_fields=['otp_attempts'])
        return False, f'Invalid OTP. {attempts_left} attempts remaining.'

    clear_otp(user)
    return True, ''


def add_form_errors(request, form):
    for errors in form.errors.values():
        for error in errors:
            messages.error(request, error)


def login_lock_seconds(request):
    locked_until = request.session.get('login_locked_until')
    if not locked_until:
        return 0
    remaining = int(locked_until - timezone.now().timestamp())
    if remaining <= 0:
        request.session.pop('login_locked_until', None)
        request.session.pop('login_attempts', None)
        return 0
    return remaining


def record_failed_login(request):
    attempts = request.session.get('login_attempts', 0) + 1
    request.session['login_attempts'] = attempts
    if attempts >= LOGIN_MAX_ATTEMPTS:
        request.session['login_locked_until'] = (timezone.now() + LOGIN_LOCKOUT).timestamp()


def clear_login_attempts(request):
    request.session.pop('login_attempts', None)
    request.session.pop('login_locked_until', None)


def format_phone(phone):
    phone = re.sub(r'[\s\-()]', '', (phone or '').strip())

    if phone.startswith('0'):
        phone = '+60' + phone[1:]
    elif phone.startswith('60'):
        phone = '+' + phone
    elif not phone.startswith('+60'):
        phone = '+60' + phone

    return phone


def preprocess_text(text):
    text = text or ""
    text = text.lower()
    text = re.sub(r'node\.js', 'nodejs', text)
    text = re.sub(r'\.net', 'dotnet', text)
    text = re.sub(r'c\+\+', 'cplusplus', text)
    text = re.sub(r'c#', 'csharp', text)
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def extract_text_from_pdf(pdf_file):
    text = ""
    try:
        reader = PyPDF2.PdfReader(pdf_file)
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text += extracted + " "
    except Exception:
        return ""
    return text.strip()

# =========================
# SIMPLE ATTRIBUTE EXTRACTION
# =========================

def extract_skills(text):

    patterns = [

        r'skills\s*(.*?)(education|experience|projects|certifications|languages|$)',

        r'technical skills\s*(.*?)(education|experience|projects|certifications|languages|$)',

        r'core skills\s*(.*?)(education|experience|projects|certifications|languages|$)',

        r'tools\s*(.*?)(education|experience|projects|certifications|languages|$)',

        r'technologies\s*(.*?)(education|experience|projects|certifications|languages|$)'
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE | re.DOTALL
        )

        if match:

            skills_text = match.group(1)

            # fix broken PDF words
            skills_text = re.sub(r'(\w)-?\n(\w)', r'\1\2', skills_text)

            # replace remaining line breaks
            skills_text = skills_text.replace('\n', ', ')

            # remove extra spaces
            skills_text = re.sub(r'\s+', ' ', skills_text)

            # remove duplicate commas
            skills_text = re.sub(r',\s*,+', ', ', skills_text)

            # clean symbols
            skills_text = skills_text.strip(' :,-')

            # limit too long result
            return skills_text[:300]

    return ''


def extract_education(text):

    education_patterns = [

        r'bachelor[^\n]*',
        r'diploma[^\n]*',
        r'master[^\n]*',
        r'phd[^\n]*',

        r'bachelor of[^\n]*',
        r'diploma in[^\n]*',
        r'master of[^\n]*',

        r'degree in[^\n]*',
        r'computer science[^\n]*',
        r'information technology[^\n]*',
        r'software engineering[^\n]*'
    ]

    for pattern in education_patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:
            return match.group().strip()

    return ''


def extract_experience(text):

    patterns = [

        r'(\d+)\s+years?',
        r'(\d+)\s+months?',
        r'(\d+)\s+yrs?',
        r'(\d+)\s+mos?'
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:
            return match.group(0)

    return ''


def parse_skill_terms(text):

    cleaned = re.sub(r'[^a-zA-Z0-9+#.\s,/-]', ' ', text or '')
    raw_terms = re.split(r'[,/\n]| and ', cleaned)
    terms = []

    for term in raw_terms:

        term = re.sub(r'\s+', ' ', term).strip().lower()

        if len(term) >= 2 and term not in terms:

            terms.append(term)

    return terms[:25]


def build_match_explanation(resume, job):

    resume_text = preprocess_text(
        f"{resume.skills} {resume.resume_text}"
    )

    job_terms = parse_skill_terms(job.skills_required)
    matched = []
    missing = []

    for term in job_terms:
        normalized_term = preprocess_text(term)

        if normalized_term and f' {normalized_term} ' in f' {resume_text} ':

            matched.append(term.title())

        else:

            missing.append(term.title())

    return ', '.join(matched[:8]), ', '.join(missing[:8])


def open_jobs_queryset():

    return Job.objects.select_related('company').annotate(
        accepted_total=Count(
            'applications',
            filter=Q(applications__status='accepted')
        )
    ).filter(
        accepted_total__lt=F('vacancies'),
        status='open'
    )


def filter_jobs_by_salary(jobs, requested_min, requested_max):
    minimum = int(requested_min) if requested_min.isdigit() else None
    maximum = int(requested_max) if requested_max.isdigit() else None
    if minimum is None and maximum is None:
        return jobs

    matching_ids = []
    for job in jobs:
        job_minimum = job.salary_min
        job_maximum = job.salary_max

        if job_minimum is None and job_maximum is None:
            salary_numbers = [
                int(value.replace(',', ''))
                for value in re.findall(r'\d[\d,]*', job.salary or '')
            ]
            if salary_numbers:
                job_minimum = min(salary_numbers)
                job_maximum = max(salary_numbers)

        if job_minimum is None and job_maximum is not None:
            job_minimum = job_maximum
        if job_maximum is None and job_minimum is not None:
            job_maximum = job_minimum
        if job_minimum is None or job_maximum is None:
            continue

        if minimum is not None and job_maximum < minimum:
            continue
        if maximum is not None and job_minimum > maximum:
            continue
        matching_ids.append(job.id)

    return jobs.filter(id__in=matching_ids)


def can_access_job_chat(current_user, other_user, job):
    if not current_user or not other_user or not job:
        return False

    if current_user.id == other_user.id:
        return False

    if current_user.role == 'employer':
        return (
            job.company.employer_id == current_user.id and
            other_user.role == 'job_seeker' and
            Application.objects.filter(user=other_user, job=job).exclude(status='withdrawn').exists()
        )

    return (
        current_user.role == 'job_seeker' and
        other_user.id == job.company.employer_id and
        Application.objects.filter(user=current_user, job=job).exclude(status='withdrawn').exists()
    )


@transaction.atomic
def generate_recommendations_for_user(user, resume, jobs=None):
    jobs = list(jobs if jobs is not None else open_jobs_queryset())

    Recommendation.objects.filter(user=user).delete()

    if not jobs:
        return 0

    combined_resume = preprocess_text(f"""
        {resume.resume_text}
        {resume.skills}
        {resume.experience_years} years experience
        {resume.education}
    """)

    job_texts = [
        preprocess_text(f"""
            {job.title or ''}
            {job.description or ''}
            {job.skills_required or ''}
            {job.experience_required or ''}
            {job.education_required or ''}
            {job.location or ''}
        """)
        for job in jobs
    ]

    vectorizer = TfidfVectorizer(stop_words='english')
    tfidf_matrix = vectorizer.fit_transform([combined_resume] + job_texts)
    scores = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:]).flatten()

    recommendations_to_create = []

    for job, score in zip(jobs, scores):
        matched_skills, missing_skills = build_match_explanation(resume, job)
        recommendations_to_create.append(
            Recommendation(
                user=user,
                resume=resume,
                job=job,
                score=round(score * 100, 2),
                matched_skills=matched_skills,
                missing_skills=missing_skills
            )
        )

    Recommendation.objects.bulk_create(recommendations_to_create)
    return len(recommendations_to_create)


def recommendations_are_stale(user, resume, jobs):
    existing = list(
        Recommendation.objects.filter(user=user).select_related('job')
    )
    expected_job_ids = {job.id for job in jobs}

    if {recommendation.job_id for recommendation in existing} != expected_job_ids:
        return True

    for recommendation in existing:
        if recommendation.resume_id != resume.id:
            return True
        if recommendation.job.updated_at > recommendation.created_at:
            return True

    return False

# =========================
# DASHBOARD
# =========================
@role_required('job_seeker')
def dashboard(request):

    user_id = request.session.get(
        'user_id'
    )

    # LOGIN CHECK
    if not user_id:
        return redirect('login')

    try:

        user = UserProfile.objects.get(
            id=user_id
        )

    except UserProfile.DoesNotExist:

        messages.error(
            request,
            "Please login first."
        )

        return redirect('login')

    # SWEETALERT LOGIN SESSION
    login_success = request.session.pop(
        'login_success',
        False
    )

    login_name = request.session.get(
        'login_name',
        ''
    )

    # DASHBOARD STATS
    total_resumes = Resume.objects.filter(
        user=user
    ).count()

    total_applications = Application.objects.filter(
        user=user
    ).count()

    total_saved_jobs = SavedJob.objects.filter(
        user=user
    ).count()

    return render(
        request,
        'portal/dashboard.html',
        {
            'user': user,

            'total_resumes': total_resumes,

            'total_saved_jobs': total_saved_jobs,

            'total_applications': total_applications,

            # SWEETALERT
            'login_success': login_success,

            'login_name': login_name,
        }
    )


## =========================
# JOB DETAIL
# =========================
def job_detail(request, job_id):
    # 🔒 Check login
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    # 🔍 Get job
    try:
        job = Job.objects.select_related('company').get(id=job_id)
    except Job.DoesNotExist:
        messages.error(request, "Job not found.")
        return redirect('user_jobs')

    #Get user safely
    try:
        user = UserProfile.objects.get(id=user_id)
    except UserProfile.DoesNotExist:
        messages.error(request, "User not found. Please login again.")
        return redirect('login')

    #check applied
    applied = Application.objects.filter(
        user_id=user_id,
        job_id=job_id
    ).exists()

    #check saved
    saved = SavedJob.objects.filter(
        user_id=user_id,
        job_id=job_id
    ).exists()

    has_resume = (
        user.role == 'job_seeker' and
        Resume.objects.filter(user=user).exists()
    )

    return render(request, 'portal/job_detail.html', {
        'job': job,
        'user': user,
        'applied': applied,
        'saved': saved,
        'has_resume': has_resume,
        'can_chat': user.role == 'job_seeker' and applied,
        'base_template': 'portal/employer_base.html' if user.role == 'employer' else 'portal/jobseeker_base.html',
        'company_exists': employer_has_company(user) if user.role == 'employer' else False,
    })

# =========================
# USER JOBS
# =========================
@role_required('job_seeker')
def user_jobs(request):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        return redirect('login')

    # CURRENT USER
    user = UserProfile.objects.get(
        id=request.session['user_id']
    )

    # SEARCH
    query = request.GET.get(
        'q',
        ''
    ).strip()

    job_type = request.GET.get('job_type', '').strip()
    salary_min = request.GET.get('salary_min', '').strip()
    salary_max = request.GET.get('salary_max', '').strip()

    # JOBS
    jobs = open_jobs_queryset().order_by(
        '-id'
    )

    # FILTER KEYWORDS
    if query:

        jobs = jobs.filter(
            Q(title__icontains=query) |
            Q(company__name__icontains=query) |
            Q(location__icontains=query) |
            Q(description__icontains=query) |
            Q(skills_required__icontains=query) |
            Q(job_type__icontains=query) |
            Q(education_required__icontains=query) |
            Q(experience_required__icontains=query)
        )

    if job_type:

        jobs = jobs.filter(
            job_type=job_type
        )

    jobs = filter_jobs_by_salary(jobs, salary_min, salary_max)

    # SAVED JOB IDS
    saved_job_ids = SavedJob.objects.filter(
        user=user
    ).values_list(
        'job_id',
        flat=True
    )

    return render(
        request,
        'portal/user_jobs.html',
        {
            'jobs': jobs,
            'query': query,
            'job_type': job_type,
            'salary_min': salary_min,
            'salary_max': salary_max,
            'saved_job_ids': saved_job_ids
        }
    )

# =========================
# PUBLIC JOBS
# =========================

def public_home(request):
    if request.session.get('user_id'):
        return redirect('dashboard')
    return redirect('public_jobs')


def public_jobs(request):
    query = request.GET.get('q', '').strip()
    job_type = request.GET.get('job_type', '').strip()
    salary_min = request.GET.get('salary_min', '').strip()
    salary_max = request.GET.get('salary_max', '').strip()

    jobs = open_jobs_queryset().order_by('-id')

    if query:
        jobs = jobs.filter(
            Q(title__icontains=query) |
            Q(company__name__icontains=query) |
            Q(location__icontains=query) |
            Q(description__icontains=query) |
            Q(skills_required__icontains=query) |
            Q(job_type__icontains=query) |
            Q(education_required__icontains=query) |
            Q(experience_required__icontains=query)
        )

    if job_type:
        jobs = jobs.filter(job_type=job_type)

    jobs = filter_jobs_by_salary(jobs, salary_min, salary_max)

    return render(request, 'portal/public_jobs.html', {
        'jobs': jobs,
        'query': query,
        'job_type': job_type,
        'salary_min': salary_min,
        'salary_max': salary_max,
    })


def public_companies(request):
    query = request.GET.get('q', '').strip()
    companies = Company.objects.all().order_by('name')

    if query:
        companies = companies.filter(name__icontains=query)

    return render(request, 'portal/public_companies.html', {
        'companies': companies,
        'query': query
    })


def public_company_detail(request, company_id):

    company = Company.objects.filter(
        id=company_id
    ).first()

    if not company:

        messages.error(
            request,
            "Company not found."
        )

        return redirect('public_companies')

    jobs = open_jobs_queryset().filter(
        company=company
    ).order_by('-created_at')

    base_template = 'portal/public_base.html'
    user_id = request.session.get('user_id')

    if user_id:

        user = UserProfile.objects.filter(
            id=user_id
        ).first()

        if user and user.role == 'employer':

            base_template = 'portal/employer_base.html'

        else:

            base_template = 'portal/jobseeker_base.html'

    return render(
        request,
        'portal/public_company_detail.html',
        {
            'company': company,
            'jobs': jobs,
            'base_template': base_template,
            'company_exists': Company.objects.filter(
                employer_id=user_id
            ).exists() if user_id else False
        }
    )


def public_about(request):
    return render(request, 'portal/public_about.html')


def public_contact(request):
    return render(request, 'portal/public_contact.html')


# =========================
# AUTH
# =========================

def signup_view(request):

    if request.method == 'POST':

        first_name = request.POST.get(
            'first_name',
            ''
        ).strip()

        last_name = request.POST.get(
            'last_name',
            ''
        ).strip()

        email = request.POST.get(
            'email',
            ''
        ).strip().lower()

        phone = format_phone(
            request.POST.get('phone')
        )

        password = request.POST.get(
            'password',
            ''
        )

        role = request.POST.get(
            'role',
            'job_seeker'
        )

        # =========================
        # SAFE ROLE VALIDATION
        # =========================
        if role not in [
            'job_seeker',
            'employer'
        ]:

            role = 'job_seeker'

        if not first_name or not last_name or not email or not request.POST.get('phone', '').strip():
            messages.error(request, 'First name, last name, email, and phone number are required.')
            return render(request, 'portal/signup.html', {'old_data': request.POST})

        try:
            validate_email(email)
        except ValidationError:
            messages.error(request, 'Enter a valid email address.')
            return render(request, 'portal/signup.html', {'old_data': request.POST})

        if not re.fullmatch(r'\+60\d{8,11}', phone):
            messages.error(request, 'Enter a valid Malaysian phone number.')
            return render(request, 'portal/signup.html', {'old_data': request.POST})

        # =========================
        # PASSWORD VALIDATION
        # =========================
        if len(password) < 8:

            messages.error(
                request,
                "Password must be at least 8 characters."
            )

            context = {
                'old_data': request.POST
            }

            return render(
                request,
                'portal/signup.html',
                context
            )

        # =========================
        # EMAIL CHECK
        # =========================
        if UserProfile.objects.filter(
            email=email
        ).exists():

            messages.error(
                request,
                "Email already exists."
            )

            context = {
                'old_data': request.POST
            }

            return render(
                request,
                'portal/signup.html',
                context
            )

        # =========================
        # PHONE CHECK
        # =========================
        if UserProfile.objects.filter(
            phone=phone
        ).exists():

            messages.error(
                request,
                "Phone number already exists."
            )

            context = {
                'old_data': request.POST
            }

            return render(
                request,
                'portal/signup.html',
                context
            )

        # =========================
        # CREATE USER
        # =========================
        user = UserProfile(

            first_name=first_name,

            last_name=last_name,

            email=email,

            phone=phone,

            role=role,

            is_verified=False
        )

        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            for error in exc.messages:
                messages.error(request, error)
            return render(request, 'portal/signup.html', {'old_data': request.POST})

        user.set_password(password)

        user.save()

        # =========================
        # OTP SESSION
        # =========================
        request.session[
            'verify_user'
        ] = user.id

        request.session[
            'otp_purpose'
        ] = 'signup'

        try:
            sent, otp_message = send_otp(user, 'Account Verification OTP', 'Your HireMatch verification OTP is {otp}. It expires in 5 minutes.')
            messages.success(request, otp_message)
        except Exception:
            messages.error(request, 'We could not send the OTP. Please use Resend OTP to try again.')

        return redirect(
            'verify_otp'
        )

    return render(
        request,
        'portal/signup.html'
    )

# =========================
# LOGIN
# =========================
def login_view(request):

    # SUCCESS MESSAGE AFTER VERIFY
    if request.session.pop('otp_verified', False):

        messages.success(
            request,
            "Account verified successfully. Please login."
        )

    # =========================
    # LOGIN FORM
    # =========================
    if request.method == 'POST':

        login_type = request.POST.get(
            'login_type',
            'password'
        )

        email = request.POST.get(
            'email',
            ''
        ).strip().lower()

        # FIND USER
        user = UserProfile.objects.filter(
            email=email
        ).first()

        # =========================
        # PASSWORD LOGIN
        # =========================
        if login_type == 'password':

            lock_seconds = login_lock_seconds(request)
            if lock_seconds:
                messages.error(request, f'Too many login attempts. Try again in {lock_seconds} seconds.')
                return render(request, 'portal/login.html')

            # USER NOT FOUND
            if not user:
                record_failed_login(request)
                return render(request, 'portal/login.html', {'account_not_registered': True})

            password = request.POST.get(
                'password',
                ''
            )

            # NOT VERIFIED
            if not user.is_verified:

                request.session['verify_user'] = user.id

                request.session['otp_purpose'] = 'signup'

                messages.error(
                    request,
                    "Please verify your account first."
                )

                return redirect('verify_otp')

            # PASSWORD CORRECT
            if user.check_password(password):

                clear_login_attempts(request)
                request.session.cycle_key()
                request.session['user_id'] = user.id

                # SWEETALERT SESSION
                request.session['login_success'] = True

                request.session['login_name'] = (
                    user.first_name
                )

                # ROLE REDIRECT
                if user.role == 'employer':

                    # COMPANY NOT CREATED
                    if not hasattr(user, 'company'):
                        return redirect('setup_company')

                    return redirect('employer_dashboard')

                return redirect('dashboard')

            # WRONG PASSWORD
            record_failed_login(request)
            return render(
                request,
                'portal/login.html',
                {
                    'wrong_password': True
                }
            )

        # =========================
        # OTP LOGIN
        # =========================
        elif login_type == 'otp':

            # USER NOT FOUND
            if not user:

                return render(
                    request,
                    'portal/login.html',
                    {
                        'otp_user_not_found': True
                    }
                )

            request.session['verify_user'] = user.id

            # NOT VERIFIED
            if not user.is_verified:

                request.session['otp_purpose'] = 'signup'

                try:
                    sent, otp_message = send_otp(user, 'Account Verification OTP', 'Your HireMatch verification OTP is {otp}. It expires in 5 minutes.')
                except Exception:
                    messages.error(request, 'We could not send the OTP. Please try again.')
                    return redirect('login')

                messages.error(
                    request,
                    otp_message
                )

                return redirect('verify_otp')

            # VERIFIED LOGIN OTP
            request.session['otp_purpose'] = 'login'

            try:
                sent, otp_message = send_otp(user, 'HireMatch Login OTP', 'Your HireMatch login OTP is {otp}. It expires in 5 minutes.')
            except Exception:
                messages.error(request, 'We could not send the OTP. Please try again.')
                return redirect('login')

            if sent:
                messages.success(request, otp_message)
            else:
                messages.warning(request, otp_message)

            return redirect('verify_otp')

        # INVALID LOGIN TYPE
        messages.error(
            request,
            "Invalid login type."
        )

        return render(
            request,
            'portal/login.html'
        )

    return render(
        request,
        'portal/login.html'
    )


# =========================
# VERIFY OTP
# =========================
def verify_otp(request):

    user_id = request.session.get(
        'verify_user'
    )

    otp_purpose = request.session.get(
        'otp_purpose'
    )

    # INVALID SESSION
    if not user_id or not otp_purpose:

        messages.error(
            request,
            "OTP session expired. Please try again."
        )

        return redirect('login')

    # GET USER
    user = UserProfile.objects.filter(
        id=user_id
    ).first()

    # USER NOT FOUND
    if not user:

        request.session.pop(
            'verify_user',
            None
        )

        request.session.pop(
            'otp_purpose',
            None
        )

        messages.error(
            request,
            "User not found."
        )

        return redirect('signup')

    # =========================
    # VERIFY OTP
    # =========================
    if request.method == 'POST':

        entered_otp = request.POST.get(
            'otp',
            ''
        ).strip()

        otp_valid, otp_error = check_otp(user, entered_otp)

        if otp_valid:

            # =========================
            # SIGNUP FLOW
            # =========================
            if otp_purpose == 'signup':

                user.is_verified = True

                user.save(update_fields=['is_verified'])

                request.session.pop(
                    'verify_user',
                    None
                )

                request.session.pop(
                    'otp_purpose',
                    None
                )

                request.session['otp_verified'] = True

                return redirect('login')

            # =========================
            # LOGIN FLOW
            # =========================
            elif otp_purpose == 'login':

                if not user.is_verified:

                    messages.error(
                        request,
                        "Your account is not verified yet."
                    )

                    return redirect('login')

                request.session.cycle_key()
                request.session['user_id'] = user.id

                # SWEETALERT SESSION
                request.session['login_success'] = True

                request.session['login_name'] = (
                    user.first_name
                )

                request.session.pop(
                    'verify_user',
                    None
                )

                request.session.pop(
                    'otp_purpose',
                    None
                )

                # ROLE REDIRECT
                if user.role == 'employer':

                    # COMPANY NOT CREATED
                    if not hasattr(user, 'company'):
                        return redirect('setup_company')

                    return redirect('employer_dashboard')

                return redirect('dashboard')

            elif otp_purpose == 'reset_password':
                request.session.pop('verify_user', None)
                request.session.pop('otp_purpose', None)
                request.session['password_reset_verified_user'] = user.id
                request.session['password_reset_verified_at'] = timezone.now().timestamp()
                messages.success(request, 'OTP verified. You may now create a new password.')
                return redirect('reset_password')

        else:
            messages.error(request, otp_error)

    return render(
        request,
        'portal/verify_otp.html',
        {'otp_purpose': otp_purpose}
    )


@require_POST
def resend_otp(request):
    user_id = request.session.get('verify_user')
    purpose = request.session.get('otp_purpose')
    user = UserProfile.objects.filter(id=user_id).first() if user_id else None

    if not user or purpose not in {'signup', 'login', 'reset_password'}:
        messages.error(request, 'OTP session expired. Please start again.')
        return redirect('login')

    otp_templates = {
        'signup': ('Account Verification OTP', 'Your HireMatch verification OTP is {otp}. It expires in 5 minutes.'),
        'login': ('HireMatch Login OTP', 'Your HireMatch login OTP is {otp}. It expires in 5 minutes.'),
        'reset_password': ('Password Reset OTP', 'Your HireMatch password reset OTP is {otp}. It expires in 5 minutes.'),
    }
    subject, body = otp_templates[purpose]

    try:
        sent, otp_message = send_otp(user, subject, body)
        if sent:
            messages.success(request, otp_message)
        else:
            messages.warning(request, otp_message)
    except Exception:
        messages.error(request, 'We could not send the OTP. Please check the email configuration and try again.')

    return redirect('verify_otp')


# =========================
# LOGOUT
# =========================
@require_POST
def logout_view(request):

    storage = get_messages(request)

    for _ in storage:
        pass

    request.session.flush()

    return redirect('login')


# =========================
# FORGOT PASSWORD
# =========================
def forgot_password(request):

    if request.method == 'POST':

        email = request.POST.get(
            'email',
            ''
        ).strip().lower()

        user = UserProfile.objects.filter(
            email=email
        ).first()

        # USER NOT FOUND
        if not user:

            messages.error(
                request,
                "No account found with this email."
            )

            return redirect('forgot_password')

        request.session.pop('password_reset_verified_user', None)
        request.session.pop('password_reset_verified_at', None)
        request.session['verify_user'] = user.id
        request.session['otp_purpose'] = 'reset_password'

        try:
            sent, otp_message = send_otp(user, 'Password Reset OTP', 'Your HireMatch password reset OTP is {otp}. It expires in 5 minutes.')
        except Exception:
            messages.error(request, 'We could not send the OTP. Please try again.')
            return redirect('forgot_password')

        if sent:
            messages.success(request, otp_message)
        else:
            messages.warning(request, otp_message)

        return redirect('verify_otp')

    return render(
        request,
        'portal/forgot_password.html'
    )

# =========================
# RESET PASSWORD
# =========================
def reset_password(request):

    user_id = request.session.get('password_reset_verified_user')
    verified_at = request.session.get('password_reset_verified_at')

    # INVALID SESSION
    if not user_id or not verified_at or timezone.now().timestamp() - verified_at > 600:

        request.session.pop('password_reset_verified_user', None)
        request.session.pop('password_reset_verified_at', None)

        messages.error(
            request,
            "Reset session expired."
        )

        return redirect('forgot_password')

    user = UserProfile.objects.filter(
        id=user_id
    ).first()

    # USER NOT FOUND
    if not user:

        request.session.pop('password_reset_verified_user', None)
        request.session.pop('password_reset_verified_at', None)

        messages.error(
            request,
            "User not found."
        )

        return redirect('forgot_password')

    # =========================
    # RESET PROCESS
    # =========================
    if request.method == 'POST':

        new_password = request.POST.get(
            'new_password',
            ''
        )

        confirm_password = request.POST.get(
            'confirm_password',
            ''
        )

        # PASSWORD MATCH
        if new_password != confirm_password:

            messages.error(
                request,
                "Passwords do not match."
            )

            return redirect('reset_password')

        try:
            validate_password(new_password, user=user)
        except ValidationError as exc:
            for error in exc.messages:
                messages.error(request, error)
            return redirect('reset_password')

        # UPDATE PASSWORD
        user.set_password(new_password)

        user.save()

        # CLEAR SESSION
        request.session.pop('password_reset_verified_user', None)
        request.session.pop('password_reset_verified_at', None)

        messages.success(
            request,
            "Password reset successful. Please login."
        )

        return redirect('login')

    return render(
        request,
        'portal/reset_password.html'
    )

# =========================
# RESUME GENERATOR
# =========================
def generate_resume(request):
    base_template = 'portal/public_base.html'
    user = None

    if request.session.get('user_id'):
        base_template = 'portal/jobseeker_base.html'
        user = UserProfile.objects.filter(
            id=request.session.get('user_id')
        ).first()

    if request.method == 'POST':

        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        skills = request.POST.get('skills', '').strip()
        education = request.POST.get('education', '').strip()
        experience_years = request.POST.get('experience_years', '').strip()
        experience = request.POST.get('experience', '').strip()

        full_name = f"{first_name} {last_name}".strip()

        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="resume.pdf"'

        doc = SimpleDocTemplate(response)
        styles = getSampleStyleSheet()

        content = [
            Paragraph(f"<b>{full_name}</b>", styles['Title']),
            Spacer(1, 12),

            Paragraph(f"Email: {email}", styles['Normal']),
            Paragraph(f"Phone: {phone}", styles['Normal']),
            Spacer(1, 12),

            Paragraph("<b>Skills</b>", styles['Heading2']),
            Paragraph(skills or "-", styles['Normal']),
            Spacer(1, 12),

            Paragraph("<b>Education</b>", styles['Heading2']),
            Paragraph(education or "-", styles['Normal']),
            Spacer(1, 12),

            Paragraph("<b>Experience Years</b>", styles['Heading2']),
            Paragraph(experience_years or "-", styles['Normal']),
            Spacer(1, 12),

            Paragraph("<b>Experience</b>", styles['Heading2']),
            Paragraph(experience or "-", styles['Normal']),
        ]

        doc.build(content)
        return response

    return render(request, 'portal/resume_generator.html', {
        'base_template': base_template,
        'user': user
    })


# =========================
# UPLOAD RESUME
# =========================
@role_required('job_seeker')
def upload_resume(request):
    if not request.session.get('user_id'):
        return redirect('login')

    user = UserProfile.objects.get(id=request.session['user_id'])

    try:
        requested_job_id = int(request.GET.get('job_id', ''))
    except (TypeError, ValueError):
        requested_job_id = None

    if requested_job_id and Job.objects.filter(id=requested_job_id).exists():
        request.session['pending_apply_job_id'] = requested_job_id
        request.session['resume_required_notice'] = True

    if request.method == 'POST':

        skills = request.POST.get('skills', '').strip()
        experience_years = request.POST.get('experience_years', '').strip()
        education = request.POST.get('education', '').strip()
        resume_file = request.FILES.get('resume_file')

        if not resume_file:
            messages.error(request, "Please upload a resume.")
            return redirect('upload_resume')

        try:
            validate_resume_upload(resume_file)
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
            return redirect('upload_resume')

        resume_text_raw = extract_text_from_pdf(resume_file)

        if not resume_text_raw:
            messages.error(
                request,
                "Unable to extract text from the PDF. Please upload a readable resume."
            )
            return redirect('upload_resume')

        # =========================
        # AUTO EXTRACT ATTRIBUTES
        # =========================

        if not skills:
            skills = extract_skills(resume_text_raw)

        if not education:
            education = extract_education(resume_text_raw)

        if not experience_years:
            experience_years = extract_experience(resume_text_raw)

        resume = Resume.objects.create(
            user=user,
            resume_file=resume_file,
            resume_text=resume_text_raw,
            skills=skills,
            experience_years=experience_years,
            education=education,
        )

        recommendation_count = generate_recommendations_for_user(user, resume)

        if recommendation_count:
            messages.success(
                request,
                "Resume uploaded successfully. Recommendations have been generated."
            )
        else:
            messages.info(
                request,
                "Resume uploaded successfully. No open jobs are available for matching yet."
            )

        pending_job_id = request.session.pop('pending_apply_job_id', None)

        if pending_job_id and Job.objects.filter(id=pending_job_id).exists():
            return redirect('job_detail', job_id=pending_job_id)

        return redirect('recommendations')

    pending_job_id = request.session.get('pending_apply_job_id')
    pending_job = Job.objects.filter(id=pending_job_id).first() if pending_job_id else None

    return render(request, 'portal/upload_resume.html', {
        'resume_required': request.session.pop('resume_required_notice', False),
        'pending_job': pending_job,
    })

# =========================
# RECOMMENDATIONS PAGE
# =========================
@role_required('job_seeker')
def recommendations(request):
    if not request.session.get('user_id'):
        return redirect('login')

    user = UserProfile.objects.get(id=request.session['user_id'])

    latest_resume = Resume.objects.filter(
        user=user
    ).order_by('-uploaded_at').first()

    current_jobs = list(open_jobs_queryset())

    if latest_resume and recommendations_are_stale(user, latest_resume, current_jobs):
        generate_recommendations_for_user(user, latest_resume, current_jobs)

    recommendations = Recommendation.objects.filter(
        user=user,
        job__status='open'
    ).select_related(
        'job',
        'job__company'
    ).annotate(
        accepted_total=Count(
            'job__applications',
            filter=Q(job__applications__status='accepted')
        )
    ).filter(
        accepted_total__lt=F('job__vacancies')
    ).order_by('-score')[:10]

    return render(request, 'portal/recommendations.html', {
        'recommendations': recommendations
    })

# =========================
# APPLY JOB 
# =========================
@require_POST
def apply_job(request, job_id):
    if not request.session.get('user_id'):
        return redirect('login')

    user = UserProfile.objects.get(id=request.session['user_id'])
    job = Job.objects.filter(id=job_id).first()

    if user.role != 'job_seeker':
        messages.error(request, "Only job seekers can apply for jobs.")
        return redirect('employer_dashboard')

    if not job:
        messages.error(request, "Job not found.")
        return redirect('user_jobs')

    if job.status != 'open':
        messages.error(request, "Applications for this job are closed.")
        return redirect('job_detail', job_id=job.id)

    if job.is_filled:

        messages.error(
            request,
            "This job is no longer accepting applications."
        )

        return redirect('user_jobs')

    if not Resume.objects.filter(user=user).exists():
        request.session['pending_apply_job_id'] = job.id
        request.session['resume_required_notice'] = True
        messages.warning(
            request,
            'Please upload your resume before applying for this job.'
        )
        return redirect('upload_resume')

    recommendation = Recommendation.objects.filter(user=user, job=job).first()
    match_snapshot = {
        'match_score': recommendation.score if recommendation else 0,
        'matched_skills': recommendation.matched_skills if recommendation else '',
        'missing_skills': recommendation.missing_skills if recommendation else '',
    }
    application = Application.objects.filter(user=user, job=job).first()
    if not application:
        Application.objects.create(user=user, job=job, **match_snapshot)
        Notification.objects.create(
            user=job.company.employer,
            title="New job application",
            message=f"{user.full_name} applied for {job.title}."
        )
        messages.success(request, "Application submitted successfully.")
    elif application.status == 'withdrawn':
        application.status = 'pending'
        application.withdrawn_at = None
        application.match_score = match_snapshot['match_score']
        application.matched_skills = match_snapshot['matched_skills']
        application.missing_skills = match_snapshot['missing_skills']
        application.save(update_fields=['status', 'withdrawn_at', 'match_score', 'matched_skills', 'missing_skills'])
        Notification.objects.create(
            user=job.company.employer,
            title="Application resubmitted",
            message=f"{user.full_name} applied again for {job.title}."
        )
        messages.success(request, "Application resubmitted successfully.")
    else:
        messages.info(request, "You already applied for this job.")

    return redirect('job_detail', job_id=job.id)

# =========================
# APPLICATIONS
# =========================
@role_required('job_seeker')
def my_applications(request):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        return redirect('login')

    user = UserProfile.objects.get(
        id=request.session['user_id']
    )

    status_filter = request.GET.get(
        'status',
        ''
    ).strip()

    sort = request.GET.get(
        'sort',
        'newest'
    ).strip()

    base_applications = Application.objects.filter(
        user=user
    ).select_related(
        'job',
        'job__company'
    )

    applications = base_applications

    if status_filter in ['pending', 'accepted', 'rejected', 'withdrawn']:

        applications = applications.filter(
            status=status_filter
        )

    if sort == 'oldest':

        applications = applications.order_by('applied_at')

    elif sort == 'title':

        applications = applications.order_by('job__title')

    else:

        applications = applications.order_by('-applied_at')

    # =========================
    # COUNTS
    # =========================
    pending_count = base_applications.filter(
        status='pending'
    ).count()

    accepted_count = base_applications.filter(
        status='accepted'
    ).count()

    rejected_count = base_applications.filter(
        status='rejected'
    ).count()

    withdrawn_count = base_applications.filter(
        status='withdrawn'
    ).count()

    total_application_count = base_applications.count()

    Notification.objects.filter(
        user=user,
        is_read=False
    ).update(
        is_read=True
    )

    return render(
        request,
        'portal/my_applications.html',
        {
            'applications': applications,

            # STATS
            'pending_count': pending_count,
            'accepted_count': accepted_count,
            'rejected_count': rejected_count,
            'withdrawn_count': withdrawn_count,
            'total_application_count': total_application_count,
            'status_filter': status_filter,
            'sort': sort,
        }
    )


# =========================
# CANCEL APPLICATION
# =========================
@require_POST
@role_required('job_seeker')
def cancel_application(request, job_id):

    if not request.session.get('user_id'):

        return redirect('login')

    user = UserProfile.objects.get(
        id=request.session['user_id']
    )

    try:

        application = Application.objects.get(
            user=user,
            job_id=job_id
        )

        # ONLY PENDING CAN BE WITHDRAWN
        if application.status == 'pending':
            application.status = 'withdrawn'
            application.withdrawn_at = timezone.now()
            application.save(update_fields=['status', 'withdrawn_at'])

            Notification.objects.create(
                user=application.job.company.employer,
                title='Application withdrawn',
                message=f'{user.full_name} withdrew their application for {application.job.title}.'
            )

            messages.success(
                request,
                "Application withdrawn successfully."
            )

        else:

            messages.error(
                request,
                "You cannot cancel this application."
            )

    except Application.DoesNotExist:

        messages.error(
            request,
            "Application not found."
        )

    return redirect('my_applications')

# =========================
# SAVE JOB
# =========================
@require_POST
def save_job(request, job_id):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':

            return JsonResponse(
                {
                    'status': 'error',
                    'message': 'Please login first.'
                },
                status=401
            )

        return redirect('login')

    user = UserProfile.objects.get(
        id=request.session['user_id']
    )

    if user.role != 'job_seeker':
        return JsonResponse({'status': 'error', 'message': 'Access denied.'}, status=403)

    job = Job.objects.get(
        id=job_id
    )

    # SAVE JOB
    saved_job, created = SavedJob.objects.get_or_create(
        user=user,
        job=job
    )

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':

        return JsonResponse(
            {
                'status': 'success',
                'saved': True,
                'message': 'Job saved successfully.'
            }
        )

    # STAY SAME PAGE
    return redirect(
        request.META.get(
            'HTTP_REFERER',
            'user_jobs'
        )
    )

# =========================
# REMOVE SAVED JOB
# =========================
@require_POST
def remove_saved_job(request, job_id):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':

            return JsonResponse(
                {
                    'status': 'error',
                    'message': 'Please login first.'
                },
                status=401
            )

        return redirect('login')

    user = UserProfile.objects.get(
        id=request.session['user_id']
    )

    if user.role != 'job_seeker':
        return JsonResponse({'status': 'error', 'message': 'Access denied.'}, status=403)

    # REMOVE
    SavedJob.objects.filter(
        user=user,
        job_id=job_id
    ).delete()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':

        return JsonResponse(
            {
                'status': 'success',
                'saved': False,
                'message': 'Job removed from saved list.'
            }
        )

    # STAY SAME PAGE
    return redirect(
        request.META.get(
            'HTTP_REFERER',
            'user_jobs'
        )
    )


# =========================
# SAVED JOBS PAGE
# =========================
@role_required('job_seeker')
def saved_jobs(request):
    if not request.session.get('user_id'):
        return redirect('login')

    user = UserProfile.objects.get(id=request.session['user_id'])

    query = request.GET.get('q', '').strip()
    sort = request.GET.get('sort', 'newest').strip()

    saved_jobs = SavedJob.objects.filter(user=user)\
        .select_related('job', 'job__company')\
        .annotate(
            accepted_total=Count(
                'job__applications',
                filter=Q(job__applications__status='accepted')
            )
        )

    if query:

        saved_jobs = saved_jobs.filter(
            Q(job__title__icontains=query) |
            Q(job__company__name__icontains=query) |
            Q(job__location__icontains=query)
        )

    if sort == 'oldest':

        saved_jobs = saved_jobs.order_by('saved_at')

    elif sort == 'title':

        saved_jobs = saved_jobs.order_by('job__title')

    elif sort == 'company':

        saved_jobs = saved_jobs.order_by('job__company__name')

    else:

        saved_jobs = saved_jobs.order_by('-saved_at')

    return render(request, 'portal/saved_jobs.html', {
        'saved_jobs': saved_jobs,
        'query': query,
        'sort': sort
    })

# =========================
# MANAGE RESUME
# =========================
@role_required('job_seeker')
def manage_resume(request):
    if not request.session.get('user_id'):
        return redirect('login')

    user = UserProfile.objects.get(id=request.session['user_id'])

    resume = Resume.objects.filter(user=user).order_by('-uploaded_at').first()

    return render(request, 'portal/manage_resume.html', {
        'resume': resume
    })


def view_resume(request, resume_id):
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('login')

    current_user = UserProfile.objects.filter(id=user_id).first()
    resume = Resume.objects.select_related('user').filter(id=resume_id).first()
    if not current_user or not resume:
        return HttpResponse('Resume not found.', status=404)

    is_owner = resume.user_id == current_user.id
    is_authorized_employer = (
        current_user.role == 'employer' and
        Application.objects.filter(
            user=resume.user,
            job__company__employer=current_user
        ).exists()
    )
    if not is_owner and not is_authorized_employer:
        return HttpResponse('Access denied.', status=403)

    try:
        resume_file = resume.resume_file.open('rb')
    except FileNotFoundError:
        return HttpResponse('Resume file not found.', status=404)

    filename = Path(resume.resume_file.name).name.replace('"', '')
    response = FileResponse(resume_file, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response

# =========================
# FAQ
# =========================
def faq(request):

    # DEFAULT
    base_template = 'portal/public_base.html'

    user_id = request.session.get(
        'user_id'
    )

    # USER LOGGED IN
    if user_id:

        try:

            user = UserProfile.objects.get(
                id=user_id
            )

            # EMPLOYER
            if user.role == 'employer':

                base_template = (
                    'portal/employer_base.html'
                )

            # JOB SEEKER
            else:

                base_template = (
                    'portal/jobseeker_base.html'
                )

        except UserProfile.DoesNotExist:

            pass

    return render(
        request,
        'portal/faq.html',
        {
            'base_template': base_template
        }
    )

# =========================
# EMPLOYER COMPANY CHECK
# =========================
def employer_has_company(user):

    return Company.objects.filter(
        employer=user
    ).exists()


# =========================
# EMPLOYER DASHBOARD
# =========================
@role_required('employer')
def employer_dashboard(request):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        return redirect('login')

    # CURRENT USER
    user = UserProfile.objects.get(
        id=request.session['user_id']
    )

    # EMPLOYER ONLY
    if user.role != 'employer':

        messages.error(
            request,
            'Access denied.'
        )

        return redirect('dashboard')

    # MUST SETUP COMPANY FIRST
    if not employer_has_company(user):

        return redirect('setup_company')

    # GET COMPANY
    company = Company.objects.get(
        employer=user
    )

    # TOTAL JOBS
    total_jobs = Job.objects.filter(
        company=company
    ).count()

    jobs_with_hires = Job.objects.filter(company=company).annotate(
        accepted_total=Count(
            'applications',
            filter=Q(applications__status='accepted')
        )
    )

    filled_jobs = jobs_with_hires.filter(
        accepted_total__gte=F('vacancies')
    ).count()

    open_jobs = jobs_with_hires.filter(
        status='open',
        accepted_total__lt=F('vacancies')
    ).count()

    closed_jobs = jobs_with_hires.filter(
        status='closed',
        accepted_total__lt=F('vacancies')
    ).count()

    # TOTAL APPLICANTS
    total_applicants = Application.objects.filter(
        job__company=company
    ).exclude(
        status='withdrawn'
    ).count()

    pending_applicants = Application.objects.filter(
        job__company=company,
        status='pending'
    ).count()

    accepted_applicants = Application.objects.filter(
        job__company=company,
        status='accepted'
    ).count()

    rejected_applicants = Application.objects.filter(
        job__company=company,
        status='rejected'
    ).count()

    top_jobs = Job.objects.filter(
        company=company
    ).annotate(
        applicant_total=Count(
            'applications',
            filter=~Q(applications__status='withdrawn')
        )
    ).order_by(
        '-applicant_total',
        '-created_at'
    )[:5]

    # SWEETALERT LOGIN SESSION
    login_success = request.session.pop(
        'login_success',
        False
    )

    login_name = request.session.get(
        'login_name',
        ''
    )

    return render(
        request,
        'portal/employer_dashboard.html',
        {
            'user': user,
            'company': company,

            'company_exists': True,

            'total_jobs': total_jobs,
            'total_applicants': total_applicants,
            'open_jobs': open_jobs,
            'closed_jobs': closed_jobs,
            'filled_jobs': filled_jobs,
            'pending_applicants': pending_applicants,
            'accepted_applicants': accepted_applicants,
            'rejected_applicants': rejected_applicants,
            'top_jobs': top_jobs,

            'login_success': login_success,
            'login_name': login_name,
        }
    )


# =========================
# POST JOB
# =========================
@role_required('employer')
def post_job(request):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        return redirect('login')

    # CURRENT USER
    user = UserProfile.objects.get(
        id=request.session['user_id']
    )

    # EMPLOYER ONLY
    if user.role != 'employer':

        messages.error(
            request,
            "Access denied."
        )

        return redirect('dashboard')

    # GET COMPANY
    company = Company.objects.filter(
        employer=user
    ).first()

    # MUST SETUP COMPANY
    if not company:

        return redirect('setup_company')

    # FORM SUBMIT
    if request.method == 'POST':
        form_data = request.POST.copy()
        form_data['status'] = 'open'
        form = JobForm(form_data)

        if not form.is_valid():
            add_form_errors(request, form)
            return render(request, 'portal/post_job.html', {
                'company': company,
                'company_exists': True,
                'old_data': request.POST,
            })

        job = form.save(commit=False)
        job.company = company
        job.save()

        messages.success(
            request,
            "Job posted successfully."
        )

        return redirect('manage_jobs')

    return render(
        request,
        'portal/post_job.html',
        {
            'company': company,
            'company_exists': True
        }
    )

# =========================
# SETUP COMPANY
# =========================
@role_required('employer')
def setup_company(request):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        return redirect('login')

    # CURRENT USER
    user = UserProfile.objects.get(
        id=request.session['user_id']
    )

    # EMPLOYER ONLY
    if user.role != 'employer':

        messages.error(
            request,
            "Access denied."
        )

        return redirect('dashboard')

    # COMPANY ALREADY EXISTS
    if Company.objects.filter(
        employer=user
    ).exists():

        return redirect('employer_dashboard')

    # FORM SUBMIT
    if request.method == 'POST':

        name = request.POST.get('name')
        industry = request.POST.get('industry')
        location = request.POST.get('location')

        website = request.POST.get('website')
        description = request.POST.get('description')

        logo = request.FILES.get('logo')

        if logo:
            try:
                validate_image_upload(logo)
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
                return redirect('setup_company')

        # REQUIRED VALIDATION
        if not name:

            messages.error(
                request,
                "Company name is required."
            )

            return redirect('setup_company')

        # CREATE COMPANY
        Company.objects.create(

            employer=user,

            name=name,
            industry=industry,
            location=location,

            website=website,
            description=description,

            logo=logo
        )

        messages.success(
            request,
            "Company profile created successfully."
        )

        return redirect('employer_dashboard')

    return render(
        request,
        'portal/setup_company.html',
        {
            'company_exists': False
        }
    )


# =========================
# COMPANY PROFILE
# =========================
@role_required('employer')
def company_profile(request):

    if not request.session.get('user_id'):

        return redirect('login')

    user = UserProfile.objects.get(
        id=request.session['user_id']
    )

    if user.role != 'employer':

        messages.error(
            request,
            "Access denied."
        )

        return redirect('dashboard')

    company = Company.objects.filter(
        employer=user
    ).first()

    if not company:

        return redirect('setup_company')

    if request.method == 'POST':

        company.name = request.POST.get('name', '').strip()
        company.industry = request.POST.get('industry', '').strip()
        company.location = request.POST.get('location', '').strip()
        company.website = request.POST.get('website', '').strip()
        company.description = request.POST.get('description', '').strip()

        if request.FILES.get('logo'):
            logo = request.FILES.get('logo')
            try:
                validate_image_upload(logo)
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
                return redirect('company_profile')
            company.logo = logo

        company.save()

        messages.success(
            request,
            "Company profile updated successfully."
        )

        return redirect('company_profile')

    return render(
        request,
        'portal/company_profile.html',
        {
            'company': company,
            'company_exists': True
        }
    )


# =========================
# MANAGE JOBS
# =========================
@role_required('employer')
def manage_jobs(request):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        return redirect('login')

    # CURRENT USER
    user = UserProfile.objects.get(
        id=request.session['user_id']
    )

    # EMPLOYER ONLY
    if user.role != 'employer':

        return redirect('dashboard')

    # GET COMPANY
    company = Company.objects.filter(
        employer=user
    ).first()

    if not company:

        return redirect('setup_company')

    # GET JOBS
    jobs = Job.objects.filter(
        company=company
    )

    query = request.GET.get('q', '').strip()
    location = request.GET.get('location', '').strip()
    status = request.GET.get('status', '').strip()

    if query:

        jobs = jobs.filter(
            title__icontains=query
        )

    if location:

        jobs = jobs.filter(
            location__icontains=location
        )

    jobs = jobs.annotate(
        applicant_total=Count(
            'applications',
            filter=~Q(applications__status='withdrawn'),
            distinct=True
        ),
        accepted_total=Count(
            'applications',
            filter=Q(applications__status='accepted'),
            distinct=True
        )
    )

    if status == 'filled':
        jobs = jobs.filter(accepted_total__gte=F('vacancies'))
    elif status == 'open':
        jobs = jobs.filter(
            status='open',
            accepted_total__lt=F('vacancies')
        )
    elif status == 'closed':
        jobs = jobs.filter(
            status='closed',
            accepted_total__lt=F('vacancies')
        )

    jobs = jobs.order_by('-created_at')

    # TOTAL APPLICANTS
    total_applicants = Application.objects.filter(
        job__company=company
    ).exclude(status='withdrawn').count()

    return render(
        request,
        'portal/manage_job.html',
        {
            'jobs': jobs,
            'company_exists': True,
            'company': company,
            'total_applicants': total_applicants,
            'query': query,
            'location': location,
            'status': status
        }
    )

# =========================
# EMPLOYER JOB DETAIL
# =========================
@role_required('employer')
def employer_job_detail(request, job_id):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        return redirect('login')

    # CURRENT USER
    user = UserProfile.objects.get(
        id=request.session['user_id']
    )

    # EMPLOYER ONLY
    if user.role != 'employer':

        messages.error(
            request,
            "Access denied."
        )

        return redirect('dashboard')

    # GET COMPANY
    company = Company.objects.filter(
        employer=user
    ).first()

    if not company:

        return redirect('setup_company')

    # GET JOB
    job = Job.objects.filter(
        id=job_id,
        company=company
    ).first()

    if not job:

        messages.error(
            request,
            "Job not found."
        )

        return redirect('manage_jobs')

    # APPLICANT COUNT
    applicant_count = Application.objects.filter(
        job=job
    ).count()

    return render(
        request,
        'portal/employer_job_detail.html',
        {
            'job': job,
            'company': company,
            'company_exists': True,
            'applicant_count': applicant_count
        }
    )

# =========================
# VIEW APPLICANTS
# =========================
@role_required('employer')
def view_applicants(request):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        return redirect('login')

    # CURRENT USER
    user = UserProfile.objects.get(
        id=request.session['user_id']
    )

    # EMPLOYER ONLY
    if user.role != 'employer':

        messages.error(
            request,
            "Access denied."
        )

        return redirect('dashboard')

    # GET COMPANY
    company = Company.objects.filter(
        employer=user
    ).first()

    if not company:

        return redirect('setup_company')

    Notification.objects.filter(
        user=user,
        is_read=False
    ).update(is_read=True)

    job_filter = request.GET.get('job', '').strip()
    status_filter = request.GET.get('status', '').strip()
    sort = request.GET.get('sort', 'match').strip()

    # GET APPLICATIONS
    applications = Application.objects.filter(
        job__company=company
    ).select_related(
        'user',
        'job'
    )

    if job_filter.isdigit():

        applications = applications.filter(
            job_id=int(job_filter)
        )

    if status_filter in ['pending', 'accepted', 'rejected', 'withdrawn']:

        applications = applications.filter(
            status=status_filter
        )

    if sort == 'oldest':

        applications = applications.order_by('applied_at')

    else:

        applications = applications.order_by('-applied_at')

    applications = list(applications)

    for application in applications:

        application.latest_resume = Resume.objects.filter(
            user=application.user
        ).order_by(
            '-uploaded_at'
        ).first()

    if sort == 'match':

        applications.sort(
            key=lambda application: application.match_score,
            reverse=True
        )

    employer_jobs = Job.objects.filter(
        company=company
    ).order_by('title')

    return render(
        request,
        'portal/applicants.html',
        {
            'applications': applications,
            'company_exists': True,
            'company': company,
            'employer_jobs': employer_jobs,
            'job_filter': job_filter,
            'status_filter': status_filter,
            'sort': sort
        }
    )


# =========================
# EDIT JOB
# =========================
@role_required('employer')
def edit_job(request, job_id):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        return redirect('login')

    # CURRENT USER
    user = UserProfile.objects.get(
        id=request.session.get('user_id')
    )

    # GET COMPANY
    company = Company.objects.filter(
        employer=user
    ).first()

    if not company:

        return redirect('setup_company')

    # GET JOB
    job = Job.objects.filter(
        id=job_id,
        company=company
    ).first()

    if not job:

        messages.error(
            request,
            "Job not found."
        )

        return redirect('manage_jobs')

    # UPDATE JOB
    if request.method == 'POST':
        form = JobForm(request.POST, instance=job)
        if not form.is_valid():
            add_form_errors(request, form)
            return render(request, 'portal/edit_job.html', {
                'job': job,
                'company': company,
                'company_exists': True,
            })

        form.save()

        messages.success(
            request,
            "Job updated successfully."
        )

        return redirect('manage_jobs')

    return render(
        request,
        'portal/edit_job.html',
        {
            'job': job,
            'company': company,
            'company_exists': True
        }
    )


# =========================
# DELETE JOB
# =========================
@require_POST
@role_required('employer')
def delete_job(request, job_id):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        return redirect('login')

    # CURRENT USER
    user = UserProfile.objects.get(
        id=request.session.get('user_id')
    )

    # GET COMPANY
    company = Company.objects.filter(
        employer=user
    ).first()

    if not company:

        return redirect('setup_company')

    # GET JOB
    job = Job.objects.filter(
        id=job_id,
        company=company
    ).first()

    if not job:

        messages.error(
            request,
            "Job not found."
        )

        return redirect('manage_jobs')

    # DELETE JOB
    job.delete()

    messages.success(
        request,
        "Job deleted successfully."
    )

    return redirect('manage_jobs')


# =========================
# TOGGLE JOB STATUS
# =========================
@require_POST
@role_required('employer')
def toggle_job_status(request, job_id):

    if not request.session.get('user_id'):

        return redirect('login')

    user = UserProfile.objects.get(
        id=request.session.get('user_id')
    )

    company = Company.objects.filter(
        employer=user
    ).first()

    if not company:

        return redirect('setup_company')

    job = Job.objects.filter(
        id=job_id,
        company=company
    ).first()

    if not job:

        messages.error(
            request,
            "Job not found."
        )

        return redirect('manage_jobs')

    if job.status == 'closed' and job.is_filled:
        messages.error(request, 'Increase the number of vacancies before reopening a filled job.')
        return redirect('manage_jobs')

    job.status = 'closed' if job.status == 'open' else 'open'
    job.save()

    if job.status == 'open':

        messages.success(
            request,
            "Job status changed to Open."
        )

    else:

        messages.warning(
            request,
            "Job status changed to Closed."
        )

    return redirect('manage_jobs')
# =========================
# CHAT ROOM
# =========================
def open_chat(request, job_id, user_id):

    if not request.session.get('user_id'):

        return redirect('login')

    current_user = UserProfile.objects.filter(
        id=request.session.get('user_id')
    ).first()

    other_user = UserProfile.objects.filter(
        id=user_id
    ).first()

    job = Job.objects.select_related('company__employer').filter(
        id=job_id
    ).first()

    if not can_access_job_chat(current_user, other_user, job):
        messages.error(request, "You do not have access to this conversation.")
        return redirect('chat_list')

    if current_user and other_user:

        ChatMessage.objects.filter(
            sender=other_user,
            receiver=current_user,
            job=job,
            is_read=False
        ).update(is_read=True)

    return redirect('chat_room', job_id=job_id, user_id=user_id)


@require_POST
def delete_chat(request, job_id, user_id):

    if not request.session.get('user_id'):

        return redirect('login')

    current_user = UserProfile.objects.filter(
        id=request.session.get('user_id')
    ).first()

    other_user = UserProfile.objects.filter(
        id=user_id
    ).first()

    job = Job.objects.select_related('company__employer').filter(
        id=job_id
    ).first()

    if can_access_job_chat(current_user, other_user, job):

        conversation_messages = ChatMessage.objects.filter(
            Q(sender=current_user, receiver=other_user) |
            Q(sender=other_user, receiver=current_user),
            job=job
        )

        for chat_message in conversation_messages:
            chat_message.hidden_for.add(current_user)

        messages.success(
            request,
            "Conversation deleted successfully."
        )

    else:
        messages.error(request, "You do not have access to this conversation.")

    return redirect('chat_list')


def chat_room(
    request,
    job_id,
    user_id
):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        return redirect('login')

    current_user = UserProfile.objects.get(
        id=request.session.get('user_id')
    )

    other_user = UserProfile.objects.get(
        id=user_id
    )

    job = Job.objects.select_related('company__employer').filter(id=job_id).first()

    if not can_access_job_chat(current_user, other_user, job):
        messages.error(request, "You do not have access to this conversation.")
        return redirect('chat_list')

    # =========================
    # MARK AS READ
    # =========================
    ChatMessage.objects.filter(

        sender=other_user,
        receiver=current_user,
        job=job,
        is_read=False

    ).update(is_read=True)

    # =========================
    # GET MESSAGES
    # =========================
    messages = ChatMessage.objects.filter(
        job=job
    ).exclude(
        hidden_for=current_user
    ).filter(
        sender__in=[current_user, other_user],
        receiver__in=[current_user, other_user]
    ).order_by('timestamp')

    # =========================
    # TEMPLATE
    # =========================
    if current_user.role == 'employer':

        template = 'portal/chat_room_employer.html'

    else:

        template = 'portal/chat_room_jobseeker.html'

    return render(
        request,
        template,
        {
            'job': job,
            'current_user': current_user,
            'other_user': other_user,
            'messages': messages
        }
    )

# =========================
# SEND MESSAGE
# =========================
@require_POST
def send_message(request):

    sender = UserProfile.objects.filter(
        id=request.session.get('user_id')
    ).first()

    if not sender:
        return JsonResponse({'status': 'error', 'message': 'Login required.'}, status=401)

    receiver_id = request.POST.get('receiver_id')

    job_id = request.POST.get('job_id')

    message_text = request.POST.get('message', '').strip()

    receiver = UserProfile.objects.filter(id=receiver_id).first()

    job = Job.objects.select_related('company__employer').filter(id=job_id).first()

    if not can_access_job_chat(sender, receiver, job):
        return JsonResponse({'status': 'error', 'message': 'Access denied.'}, status=403)

    if not message_text:
        return JsonResponse({'status': 'error', 'message': 'Message cannot be empty.'}, status=400)

    if len(message_text) > 2000:
        return JsonResponse({'status': 'error', 'message': 'Message is too long.'}, status=400)

    ChatMessage.objects.create(
        sender=sender,
        receiver=receiver,
        job=job,
        message=message_text,
        is_read=False
    )

    return JsonResponse({'status': 'success'})

# =========================
# FETCH MESSAGES
# =========================
def fetch_messages(request, job_id, user_id):

    if not request.session.get('user_id'):
        return JsonResponse({'status': 'error', 'message': 'Login required.'}, status=401)

    current_user = UserProfile.objects.filter(
        id=request.session.get('user_id')
    ).first()

    other_user = UserProfile.objects.filter(
        id=user_id
    ).first()

    job = Job.objects.filter(
        id=job_id
    ).first()

    if not can_access_job_chat(current_user, other_user, job):
        return JsonResponse({'status': 'error', 'message': 'Access denied.'}, status=403)

    ChatMessage.objects.filter(
        sender=other_user,
        receiver=current_user,
        job=job,
        is_read=False
    ).update(is_read=True)

    messages = ChatMessage.objects.exclude(
        hidden_for=current_user
    ).filter(

        Q(sender=current_user, receiver=other_user) |
        Q(sender=other_user, receiver=current_user),

        job=job

    ).order_by('timestamp')

    data = []

    for msg in messages:

        data.append({

            'sender': msg.sender.first_name,
            'message': msg.message,
            'time': timezone.localtime(msg.timestamp).strftime('%I:%M %p'),
            'is_me': msg.sender == current_user

        })

    return JsonResponse({

        'messages': data

    })

# =========================
# CHAT LIST
# =========================
def chat_list(request):

    if not request.session.get('user_id'):

        return redirect('login')

    current_user = UserProfile.objects.get(
        id=request.session.get('user_id')
    )

    # =========================
    # COMPANY EXISTS CHECK
    # =========================
    company_exists = False

    if current_user.role == 'employer':

        company_exists = Company.objects.filter(
            employer=current_user
        ).exists()

    # GET LATEST CHAT PER CONVERSATION
    chats = ChatMessage.objects.exclude(
        hidden_for=current_user
    ).filter(

        Q(sender=current_user) |
        Q(receiver=current_user)

    ).order_by('-timestamp')

    unique_chats = []

    seen = set()

    for chat in chats:

        other_user = (
            chat.receiver
            if chat.sender == current_user
            else chat.sender
        )

        if not can_access_job_chat(current_user, other_user, chat.job):
            continue

        key = f"{chat.job.id}-{other_user.id}"

        if key not in seen:

            seen.add(key)

            unread_count = ChatMessage.objects.exclude(
                hidden_for=current_user
            ).filter(
                sender=other_user,
                receiver=current_user,
                job=chat.job,
                is_read=False
            ).count()

            unique_chats.append({

                'job': chat.job,

                'other_user': other_user,

                'last_message': chat.message,

                'timestamp': chat.timestamp,

                'unread_count': unread_count

            })

    # TEMPLATE
    if current_user.role == 'employer':

        template = 'portal/chat_list_employer.html'

    else:

        template = 'portal/chat_list_jobseeker.html'

    return render(
        request,
        template,
        {
            'chat_list': unique_chats,
            'company_exists': company_exists
        }
    )


# =========================
# UPDATE APPLICATION STATUS
# =========================
@require_POST
@role_required('employer')
def update_application_status(
    request,
    application_id,
    status
):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        return redirect('login')

    user = UserProfile.objects.get(id=request.session['user_id'])

    if user.role != 'employer':
        messages.error(request, "Only employers can update application status.")
        return redirect('dashboard')

    # VALID STATUS
    if status not in ['accepted', 'rejected']:

        messages.error(
            request,
            "Invalid status."
        )

        return redirect('view_applicants')

    auto_rejected_applications = []

    with transaction.atomic():
        application = Application.objects.select_for_update().select_related(
            'user',
            'job__company'
        ).filter(
            id=application_id,
            job__company__employer=user
        ).first()

        if not application:
            messages.error(request, "Application not found or access denied.")
            return redirect('view_applicants')

        if application.status == 'withdrawn':
            messages.error(request, 'A withdrawn application cannot be accepted or rejected.')
            return redirect('view_applicants')

        if application.status == status:
            messages.info(request, f"Applicant is already {status}.")
            return redirect('view_applicants')

        job = Job.objects.select_for_update().get(id=application.job_id)

        if status == 'accepted':
            accepted_count = Application.objects.filter(
                job=job,
                status='accepted'
            ).exclude(id=application.id).count()

            if accepted_count >= job.vacancies:
                messages.error(request, "This job has already reached its hiring limit.")
                return redirect('view_applicants')

        application.status = status
        application.save(update_fields=['status'])

        if status == 'accepted' and accepted_count + 1 >= job.vacancies:
            job.status = 'closed'
            job.save(update_fields=['status', 'updated_at'])

            auto_rejected_applications = list(
                Application.objects.select_for_update().select_related('user').filter(
                    job=job,
                    status='pending'
                )
            )

            if auto_rejected_applications:
                Application.objects.filter(
                    id__in=[item.id for item in auto_rejected_applications]
                ).update(status='rejected')

                Notification.objects.bulk_create([
                    Notification(
                        user=item.user,
                        title='Position filled',
                        message=f"The position for {job.title} has been filled."
                    )
                    for item in auto_rejected_applications
                ])

    if status == 'accepted':

        title = "Application accepted"
        message = f"Your application for {application.job.title} was accepted."
        email_message = (
            "Congratulations! Your application has been accepted by the employer. "
            "You may now communicate with the employer for the next steps in the hiring process."
        )

    else:

        title = "Application rejected"
        message = f"Your application for {application.job.title} was not selected."
        email_message = (
            "We appreciate your interest in this position. Unfortunately, your application was not selected for this role. "
            "We encourage you to continue applying for other opportunities on HireMatch."
        )

    Notification.objects.create(
        user=application.user,
        title=title,
        message=message
    )

    failed_email_count = 0

    try:
        send_mail(
            title,
            email_message,
            settings.DEFAULT_FROM_EMAIL,
            [application.user.email],
            fail_silently=False
        )
    except Exception:
        failed_email_count += 1

    for rejected_application in auto_rejected_applications:
        try:
            send_mail(
                'Position filled',
                (
                    f"Thank you for your interest in {job.title}. "
                    "The position has now been filled, and your application was not selected. "
                    "We encourage you to continue applying for other opportunities on HireMatch."
                ),
                settings.DEFAULT_FROM_EMAIL,
                [rejected_application.user.email],
                fail_silently=False
            )
        except Exception:
            failed_email_count += 1

    if status == 'accepted':
        messages.success(
            request,
            'The applicant has been accepted.',
            extra_tags='application-accepted'
        )
    else:
        messages.success(request, 'Applicant rejected successfully.')

    if failed_email_count:
        messages.warning(
            request,
            (
                'The application status was updated, but '
                f'{failed_email_count} email notification'
                f'{"s" if failed_email_count != 1 else ""} could not be sent.'
            )
        )

    return redirect('view_applicants')


@require_POST
def mark_notifications_read(request):
    user_id = request.session.get('user_id')
    if not user_id:
        return JsonResponse({'status': 'error', 'message': 'Login required.'}, status=401)

    updated_count = Notification.objects.filter(
        user_id=user_id,
        is_read=False
    ).update(is_read=True)

    return JsonResponse({'status': 'success', 'updated_count': updated_count})


@require_POST
def clear_notifications(request):
    if not request.session.get('user_id'):
        return redirect('login')

    Notification.objects.filter(
        user_id=request.session.get('user_id')
    ).delete()

    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

# =========================
# PROFILE PAGE
# =========================
def profile_view(request):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        return redirect('login')

    user = UserProfile.objects.get(
        id=request.session.get('user_id')
    )

    return render(
        request,
        'portal/profile.html',
        {
            'user_profile': user
        }
    )


# =========================
# EDIT PROFILE
# =========================
def edit_profile(request):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        return redirect('login')

    user = UserProfile.objects.get(
        id=request.session.get('user_id')
    )

    # UPDATE PROFILE
    if request.method == 'POST':

        user.first_name = request.POST.get(
            'first_name'
        )

        user.last_name = request.POST.get(
            'last_name'
        )

        user.phone = request.POST.get(
            'phone'
        )

        user.location = request.POST.get(
            'location'
        )

        user.bio = request.POST.get(
            'bio'
        )

        # PROFILE IMAGE
        if request.FILES.get('profile_image'):
            profile_image = request.FILES.get('profile_image')
            try:
                validate_image_upload(profile_image)
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
                return redirect('manage_profile')
            user.profile_image = profile_image

        user.save()

        messages.success(
            request,
            'Profile updated successfully.'
        )

        return redirect('profile')

    return render(
        request,
        'portal/edit_profile.html',
        {
            'user_profile': user
        }
    )

# =========================
# MANAGE PROFILE
# =========================
def manage_profile(request):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        return redirect('login')

    user = UserProfile.objects.get(
        id=request.session.get('user_id')
    )

    # UPDATE PROFILE
    if request.method == 'POST':

        first_name = request.POST.get(
            'first_name',
            ''
        ).strip()

        last_name = request.POST.get(
            'last_name',
            ''
        ).strip()

        phone = format_phone(request.POST.get(
            'phone',
            ''
        ))

        bio = request.POST.get(
            'bio',
            ''
        ).strip()

        skills = request.POST.get(
            'skills',
            ''
        ).strip()

        location = request.POST.get(
            'location',
            ''
        ).strip()

        linkedin = request.POST.get(
            'linkedin',
            ''
        ).strip()

        github = request.POST.get(
            'github',
            ''
        ).strip()

        if not re.fullmatch(r'\+60\d{8,11}', phone):
            messages.error(request, 'Enter a valid Malaysian phone number.')
            return redirect('manage_profile')

        if UserProfile.objects.filter(phone=phone).exclude(id=user.id).exists():
            messages.error(request, 'This phone number is already registered to another account.')
            return redirect('manage_profile')

        # UPDATE TEXT
        user.first_name = first_name
        user.last_name = last_name
        user.phone = phone
        user.bio = bio
        user.skills = skills
        user.location = location
        user.linkedin = linkedin
        user.github = github

        # PROFILE IMAGE
        if request.FILES.get('profile_image'):
            profile_image = request.FILES.get('profile_image')
            try:
                validate_image_upload(profile_image)
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
                return redirect('manage_profile')
            user.profile_image = profile_image

        user.save()

        messages.success(
            request,
            'Profile updated successfully.'
        )

        return redirect('manage_profile')

    return render(
        request,
        'portal/manage_profile.html',
        {
            'user_profile': user
        }
    )

# =========================
# ACCOUNT SETTINGS
# =========================
def account_settings(request):

    # LOGIN CHECK
    if not request.session.get('user_id'):

        return redirect('login')

    # CURRENT USER
    user_profile = UserProfile.objects.get(
        id=request.session.get('user_id')
    )

    # =========================
    # DYNAMIC BASE TEMPLATE
    # =========================
    if user_profile.role == 'employer':

        base_template = 'portal/employer_base.html'

    else:

        base_template = 'portal/jobseeker_base.html'

    # =========================
    # SAVE CHANGES
    # =========================
    if request.method == 'POST':

        current_password = request.POST.get(
            'current_password'
        )

        new_password = request.POST.get(
            'new_password'
        )

        confirm_password = request.POST.get(
            'confirm_password'
        )

        # =========================
        # CURRENT PASSWORD CHECK
        # =========================
        if not user_profile.check_password(
            current_password
        ):

            messages.error(
                request,
                'Current password is incorrect.',
                extra_tags='account'
            )

            return redirect(
                'account_settings'
            )

        # =========================
        # PASSWORD MATCH
        # =========================
        if new_password != confirm_password:

            messages.error(
                request,
                'New passwords do not match.',
                extra_tags='account'
            )

            return redirect(
                'account_settings'
            )

        try:
            validate_password(new_password, user=user_profile)
        except ValidationError as exc:
            for error in exc.messages:
                messages.error(request, error, extra_tags='account')
            return redirect('account_settings')

        # =========================
        # UPDATE PASSWORD
        # =========================
        user_profile.set_password(
            new_password
        )

        user_profile.save()

        request.session.cycle_key()

        messages.success(
            request,
            'Password updated successfully.',
            extra_tags='account'
        )

        return redirect(
            'account_settings'
        )

    # =========================
    # RENDER PAGE
    # =========================
    return render(
        request,
        'portal/account_settings.html',
        {
            'user': user_profile,

            'base_template': base_template,

            'company_exists':
            employer_has_company(user_profile)
            if user_profile.role == 'employer'
            else False
        }
    )

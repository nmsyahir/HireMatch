from django.db import models
from django.contrib.auth.hashers import make_password, check_password
from django.db.models import Q

from .storage import private_resume_storage
from .validators import validate_image_upload, validate_resume_upload


# =========================
# USER MODEL
# =========================
class UserProfile(models.Model):

    ROLE_CHOICES = [
        ('job_seeker', 'Job Seeker'),
        ('employer', 'Employer'),
    ]

    first_name = models.CharField(
        max_length=100
    )

    last_name = models.CharField(
        max_length=100
    )

    email = models.EmailField(
        unique=True
    )

    phone = models.CharField(
        max_length=20,
        unique=True
    )

    # PROFILE
    profile_image = models.ImageField(
        upload_to='profile_images/',
        blank=True,
        null=True,
        validators=[validate_image_upload]
    )

    bio = models.TextField(
        blank=True
    )

    location = models.CharField(
        max_length=200,
        blank=True
    )

    skills = models.TextField(
        blank=True
    )

    linkedin = models.URLField(
        blank=True,
        null=True
    )

    github = models.URLField(
        blank=True,
        null=True
    )

    # AUTH
    password = models.CharField(
        max_length=255
    )

    is_verified = models.BooleanField(
        default=False
    )

    otp_code = models.CharField(
        max_length=6,
        blank=True,
        null=True
    )

    otp_created_at = models.DateTimeField(blank=True, null=True)

    otp_last_sent_at = models.DateTimeField(blank=True, null=True)

    otp_attempts = models.PositiveSmallIntegerField(default=0)

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='job_seeker'
    )

    is_staff = models.BooleanField(
        default=False
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    # FULL NAME
    @property
    def full_name(self):

        return f"{self.first_name} {self.last_name}"

    # INITIALS
    @property
    def initials(self):

        return f"{self.first_name[:1]}{self.last_name[:1]}".upper()

    # PASSWORD HASH
    def set_password(self, raw_password):

        self.password = make_password(raw_password)

    # PASSWORD CHECK
    def check_password(self, raw_password):

        return check_password(
            raw_password,
            self.password
        )

    def __str__(self):

        return f"{self.email} ({self.role})"


# =========================
# COMPANY
# =========================
class Company(models.Model):

    employer = models.OneToOneField(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='company'
    )

    name = models.CharField(
        max_length=200
    )

    industry = models.CharField(
        max_length=200,
        blank=True
    )

    location = models.CharField(
        max_length=200,
        blank=True
    )

    website = models.URLField(
        blank=True,
        null=True
    )

    description = models.TextField(
        blank=True
    )

    logo = models.ImageField(
        upload_to='company_logos/',
        blank=True,
        null=True,
        validators=[validate_image_upload]
    )

    def __str__(self):

        return self.name


# =========================
# JOB
# =========================
class Job(models.Model):

    STATUS_CHOICES = [
        ('open', 'Open'),
        ('closed', 'Closed'),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='jobs'
    )

    title = models.CharField(
        max_length=200
    )

    description = models.TextField()

    location = models.CharField(
        max_length=200,
        blank=True
    )

    job_type = models.CharField(
        max_length=100,
        blank=True
    )

    salary = models.CharField(
        max_length=100,
        blank=True
    )

    salary_min = models.PositiveIntegerField(
        blank=True,
        null=True
    )

    salary_max = models.PositiveIntegerField(
        blank=True,
        null=True
    )

    vacancies = models.PositiveIntegerField(
        default=1
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='open'
    )

    skills_required = models.TextField(
        blank=True
    )

    experience_required = models.CharField(
        max_length=100,
        blank=True
    )

    education_required = models.CharField(
        max_length=200,
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    class Meta:

        constraints = [
            models.CheckConstraint(
                condition=Q(vacancies__gte=1),
                name='job_vacancies_at_least_one'
            ),
            models.CheckConstraint(
                condition=Q(salary_min__isnull=True) | Q(salary_max__isnull=True) | Q(salary_min__lte=models.F('salary_max')),
                name='job_salary_range_valid'
            ),
        ]

    def __str__(self):

        return f"{self.title} - {self.company.name}"

    @property
    def accepted_count(self):

        return self.applications.filter(status='accepted').count()

    @property
    def is_filled(self):

        return self.accepted_count >= self.vacancies

    @property
    def display_status(self):

        if self.is_filled:
            return 'filled'

        return self.status


# =========================
# RESUME
# =========================
class Resume(models.Model):

    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='resumes'
    )

    resume_file = models.FileField(
        upload_to='resumes/',
        storage=private_resume_storage,
        validators=[validate_resume_upload]
    )

    resume_text = models.TextField(
        blank=True
    )

    skills = models.TextField(
        blank=True
    )

    experience_years = models.CharField(
        max_length=50,
        blank=True
    )

    education = models.CharField(
        max_length=200,
        blank=True
    )

    uploaded_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):

        return f"{self.user.full_name} Resume"


# =========================
# RECOMMENDATION
# =========================
class Recommendation(models.Model):

    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='recommendations'
    )

    resume = models.ForeignKey(
        Resume,
        on_delete=models.CASCADE,
        related_name='recommendations'
    )

    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        related_name='recommendations'
    )

    score = models.FloatField(
        default=0.0
    )

    matched_skills = models.TextField(
        blank=True
    )

    missing_skills = models.TextField(
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:

        ordering = ['-score']

        unique_together = (
            'user',
            'job'
        )

    def __str__(self):

        return f"{self.user.full_name} -> {self.job.title} ({self.score:.2f}%)"


# =========================
# APPLICATION
# =========================
class Application(models.Model):

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('withdrawn', 'Withdrawn'),
    ]

    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='applications'
    )

    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        related_name='applications'
    )

    applied_at = models.DateTimeField(
        auto_now_add=True
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )

    withdrawn_at = models.DateTimeField(blank=True, null=True)

    match_score = models.FloatField(default=0.0)

    matched_skills = models.TextField(blank=True)

    missing_skills = models.TextField(blank=True)

    class Meta:

        unique_together = (
            'user',
            'job'
        )

    def __str__(self):

        return f"{self.user.full_name} applied for {self.job.title} ({self.status})"


# =========================
# SAVED JOB
# =========================
class SavedJob(models.Model):

    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='saved_jobs'
    )

    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        related_name='saved_jobs'
    )

    saved_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:

        unique_together = (
            'user',
            'job'
        )

    def __str__(self):

        return f"{self.user.full_name} saved {self.job.title}"


# =========================
# CHAT MESSAGE
# =========================
class ChatMessage(models.Model):

    sender = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='sent_messages'
    )

    receiver = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='received_messages'
    )

    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        related_name='chat_messages'
    )

    message = models.TextField()

    timestamp = models.DateTimeField(
        auto_now_add=True
    )

    is_read = models.BooleanField(
        default=False
    )

    hidden_for = models.ManyToManyField(
        UserProfile,
        related_name='hidden_chat_messages',
        blank=True
    )

    def __str__(self):

        return f"{self.sender.first_name} -> {self.receiver.first_name}"


# =========================
# NOTIFICATION
# =========================
class Notification(models.Model):

    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name='notifications'
    )

    title = models.CharField(
        max_length=150
    )

    message = models.TextField()

    is_read = models.BooleanField(
        default=False
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:

        ordering = ['-created_at']

    def __str__(self):

        return f"{self.user.full_name}: {self.title}"

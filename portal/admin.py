from django.contrib import admin

from .models import (
    Company,
    Job,
    Resume,
    Recommendation,
    UserProfile,
    Application,
    SavedJob,
    ChatMessage,
    Notification
)


# =========================
# USER PROFILE
# =========================
@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):

    list_display = (
        'first_name',
        'last_name',
        'email',
        'phone',
        'role',
        'is_verified',
        'created_at'
    )

    search_fields = (
        'first_name',
        'last_name',
        'email',
        'phone'
    )

    list_filter = (
        'role',
        'is_verified',
        'created_at'
    )


# =========================
# COMPANY
# =========================
@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):

    list_display = (
        'name',
        'industry',
        'location'
    )

    search_fields = (
        'name',
        'industry',
        'location'
    )


# =========================
# JOB
# =========================
@admin.register(Job)
class JobAdmin(admin.ModelAdmin):

    list_display = (
        'title',
        'company',
        'location',
        'job_type',
        'status',
        'vacancies',
        'created_at'
    )

    search_fields = (
        'title',
        'description',
        'skills_required',
        'education_required',
        'location'
    )

    list_filter = (
        'job_type',
        'status',
        'location',
        'created_at'
    )


# =========================
# RESUME
# =========================
@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):

    list_display = (
        'user',
        'uploaded_at'
    )

    search_fields = (
        'user__first_name',
        'user__last_name',
        'skills',
        'education'
    )

    list_filter = (
        'uploaded_at',
    )


# =========================
# RECOMMENDATION
# =========================
@admin.register(Recommendation)
class RecommendationAdmin(admin.ModelAdmin):

    list_display = (
        'user',
        'job',
        'score',
        'created_at'
    )

    search_fields = (
        'user__first_name',
        'user__last_name',
        'job__title'
    )

    list_filter = (
        'created_at',
    )

    ordering = (
        '-score',
    )


# =========================
# APPLICATION
# =========================
@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):

    list_display = (
        'user',
        'job',
        'status',
        'applied_at'
    )

    search_fields = (
        'user__first_name',
        'user__last_name',
        'job__title'
    )

    list_filter = (
        'status',
        'applied_at'
    )


# =========================
# SAVED JOB
# =========================
@admin.register(SavedJob)
class SavedJobAdmin(admin.ModelAdmin):

    list_display = (
        'user',
        'job',
        'saved_at'
    )


# =========================
# CHAT MESSAGE
# =========================
@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):

    list_display = (
        'sender',
        'receiver',
        'job',
        'timestamp',
        'is_read'
    )

    search_fields = (
        'sender__first_name',
        'sender__last_name',
        'receiver__first_name',
        'receiver__last_name',
        'message'
    )

    list_filter = (
        'is_read',
        'timestamp'
    )


# =========================
# NOTIFICATION
# =========================
@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):

    list_display = (
        'user',
        'title',
        'is_read',
        'created_at'
    )

    search_fields = (
        'user__first_name',
        'user__last_name',
        'title',
        'message'
    )

    list_filter = (
        'is_read',
        'created_at'
    )

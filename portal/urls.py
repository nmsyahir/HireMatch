from django.urls import path
from django.views.generic import RedirectView
from . import views

urlpatterns = [

    # =========================
    # ROOT
    # =========================
    path(
        '',
        RedirectView.as_view(
            pattern_name='public_jobs',
            permanent=False
        )
    ),

    # =========================
    # DASHBOARD
    # =========================
    path(
        'dashboard/',
        views.dashboard,
        name='dashboard'
    ),

    path(
        'employer/dashboard/',
        views.employer_dashboard,
        name='employer_dashboard'
    ),

    # =========================
    # JOBS
    # =========================
    path(
        'jobs/',
        views.public_jobs,
        name='public_jobs'
    ),

    path(
        'user/jobs/',
        views.user_jobs,
        name='user_jobs'
    ),

    path(
        'job/<int:job_id>/',
        views.job_detail,
        name='job_detail'
    ),

    path(
        'employer/job/<int:job_id>/',
        views.employer_job_detail,
        name='employer_job_detail'
    ),

    path(
        'employer/post-job/',
        views.post_job,
        name='post_job'
    ),

    path(
        'employer/manage-jobs/',
        views.manage_jobs,
        name='manage_jobs'
    ),

    path(
        'employer/edit-job/<int:job_id>/',
        views.edit_job,
        name='edit_job'
    ),

    path(
        'employer/delete-job/<int:job_id>/',
        views.delete_job,
        name='delete_job'
    ),

    path(
        'employer/toggle-job-status/<int:job_id>/',
        views.toggle_job_status,
        name='toggle_job_status'
    ),

    # =========================
    # COMPANY
    # =========================
    path(
        'setup-company/',
        views.setup_company,
        name='setup_company'
    ),

    path(
        'company-profile/',
        views.company_profile,
        name='company_profile'
    ),

    # =========================
    # RESUME / ML
    # =========================
    path(
        'resume-generator/',
        views.generate_resume,
        name='resume_generator'
    ),

    path(
        'upload-resume/',
        views.upload_resume,
        name='upload_resume'
    ),

    path(
        'manage-resume/',
        views.manage_resume,
        name='manage_resume'
    ),

    path(
        'resume/<int:resume_id>/view/',
        views.view_resume,
        name='view_resume'
    ),

    path(
        'recommendations/',
        views.recommendations,
        name='recommendations'
    ),

    # =========================
    # APPLICANTS
    # =========================
    path(
        'applicants/',
        views.view_applicants,
        name='view_applicants'
    ),

    path(
        'update-application-status/<int:application_id>/<str:status>/',
        views.update_application_status,
        name='update_application_status'
    ),

    # =========================
    # APPLICATION SYSTEM
    # =========================
    path(
        'apply/<int:job_id>/',
        views.apply_job,
        name='apply_job'
    ),

    path(
        'my-applications/',
        views.my_applications,
        name='my_applications'
    ),

    path(
        'notifications/clear/',
        views.clear_notifications,
        name='clear_notifications'
    ),

    path(
        'notifications/read/',
        views.mark_notifications_read,
        name='mark_notifications_read'
    ),

    path(
        'cancel-application/<int:job_id>/',
        views.cancel_application,
        name='cancel_application'
    ),

    # =========================
    # SAVED JOBS
    # =========================
    path(
        'save-job/<int:job_id>/',
        views.save_job,
        name='save_job'
    ),

    path(
        'remove-saved-job/<int:job_id>/',
        views.remove_saved_job,
        name='remove_saved_job'
    ),

    path(
        'saved-jobs/',
        views.saved_jobs,
        name='saved_jobs'
    ),

    # =========================
    # PROFILE
    # =========================
    path(
        'profile/',
        views.manage_profile,
        name='manage_profile'
    ),

    # =========================
    # ACCOUNT SETTINGS
    # =========================
    path(
        'account-settings/',
        views.account_settings,
        name='account_settings'
    ),

    # =========================
    # CHAT SYSTEM
    # =========================
    path(
        'chat-list/',
        views.chat_list,
        name='chat_list'
    ),

    path(
        'chat/<int:job_id>/<int:user_id>/',
        views.chat_room,
        name='chat_room'
    ),

    path(
        'chat/open/<int:job_id>/<int:user_id>/',
        views.open_chat,
        name='open_chat'
    ),

    path(
        'chat/delete/<int:job_id>/<int:user_id>/',
        views.delete_chat,
        name='delete_chat'
    ),

    path(
        'send-message/',
        views.send_message,
        name='send_message'
    ),

    path(
        'fetch-messages/<int:job_id>/<int:user_id>/',
        views.fetch_messages,
        name='fetch_messages'
    ),

    # =========================
    # PUBLIC PAGES
    # =========================
    path(
        'companies/',
        views.public_companies,
        name='public_companies'
    ),

    path(
        'companies/<int:company_id>/',
        views.public_company_detail,
        name='public_company_detail'
    ),

    path(
        'about/',
        views.public_about,
        name='public_about'
    ),

    path(
        'contact/',
        views.public_contact,
        name='public_contact'
    ),

    path(
        'faq/',
        views.faq,
        name='faq'
    ),

    # =========================
    # AUTH
    # =========================
    path(
        'login/',
        views.login_view,
        name='login'
    ),

    path(
        'signup/',
        views.signup_view,
        name='signup'
    ),

    path(
        'verify-otp/',
        views.verify_otp,
        name='verify_otp'
    ),

    path(
        'resend-otp/',
        views.resend_otp,
        name='resend_otp'
    ),

    path(
        'logout/',
        views.logout_view,
        name='logout'
    ),

    path(
        'forgot-password/',
        views.forgot_password,
        name='forgot_password'
    ),

    path(
        'reset-password/',
        views.reset_password,
        name='reset_password'
    ),

]

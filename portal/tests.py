from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path
from unittest.mock import patch

from django.core import mail
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone

from .forms import JobForm
from .views import check_otp, send_otp

from .models import (
    Application,
    ChatMessage,
    Company,
    Job,
    Notification,
    Recommendation,
    Resume,
    UserProfile,
)


class HireMatchFlowTests(TestCase):
    def setUp(self):
        self.seeker = UserProfile.objects.create(
            first_name='Aisyah',
            last_name='Rahman',
            email='seeker@example.com',
            phone='+60111111111',
            role='job_seeker',
            is_verified=True,
            password='unused',
        )
        self.employer = UserProfile.objects.create(
            first_name='Emir',
            last_name='Owner',
            email='employer@example.com',
            phone='+60222222222',
            role='employer',
            is_verified=True,
            password='unused',
        )
        self.other_employer = UserProfile.objects.create(
            first_name='Other',
            last_name='Employer',
            email='other@example.com',
            phone='+60333333333',
            role='employer',
            is_verified=True,
            password='unused',
        )
        self.company = Company.objects.create(
            employer=self.employer,
            name='Alpha Tech',
        )
        self.other_company = Company.objects.create(
            employer=self.other_employer,
            name='Beta Tech',
        )
        self.job = Job.objects.create(
            company=self.company,
            title='Python Developer',
            description='Build Django applications using Python and SQL.',
            skills_required='Python, Django, SQL',
            status='open',
            vacancies=1,
        )

    def login_as(self, user):
        session = self.client.session
        session['user_id'] = user.id
        session.save()

    def test_closed_job_cannot_be_applied_to(self):
        self.job.status = 'closed'
        self.job.save()
        self.login_as(self.seeker)

        response = self.client.post(reverse('apply_job', args=[self.job.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Application.objects.filter(user=self.seeker, job=self.job).exists())

    def test_open_job_requires_resume_before_applying(self):
        self.login_as(self.seeker)

        response = self.client.post(reverse('apply_job', args=[self.job.id]))

        self.assertRedirects(
            response,
            reverse('upload_resume'),
            fetch_redirect_response=False,
        )
        self.assertFalse(Application.objects.filter(user=self.seeker, job=self.job).exists())
        self.assertEqual(self.client.session['pending_apply_job_id'], self.job.id)

        upload_page = self.client.get(reverse('upload_resume'))
        self.assertContains(upload_page, 'Resume Required')
        self.assertContains(upload_page, self.job.title)

        detail_page = self.client.get(reverse('job_detail', args=[self.job.id]))
        self.assertContains(detail_page, 'Upload Resume to Apply')

    def test_successful_resume_upload_returns_to_pending_job(self):
        self.login_as(self.seeker)
        self.client.get(reverse('upload_resume'), {'job_id': self.job.id})
        resume_file = SimpleUploadedFile(
            'resume.pdf',
            b'%PDF-1.4 test resume',
            content_type='application/pdf',
        )

        with patch('portal.views.validate_resume_upload'), \
             patch('portal.views.extract_text_from_pdf', return_value='Python Django SQL'):
            response = self.client.post(
                reverse('upload_resume'),
                {'resume_file': resume_file},
            )

        self.assertRedirects(response, reverse('job_detail', args=[self.job.id]))
        self.assertTrue(Resume.objects.filter(user=self.seeker).exists())
        self.assertNotIn('pending_apply_job_id', self.client.session)

    def test_state_changing_actions_reject_get(self):
        application = Application.objects.create(user=self.seeker, job=self.job)
        self.login_as(self.employer)

        protected_urls = [
            reverse('delete_job', args=[self.job.id]),
            reverse('toggle_job_status', args=[self.job.id]),
            reverse('update_application_status', args=[application.id, 'accepted']),
            reverse('delete_chat', args=[self.job.id, self.seeker.id]),
            reverse('logout'),
            reverse('mark_notifications_read'),
        ]

        for url in protected_urls:
            self.assertEqual(self.client.get(url).status_code, 405)

    def test_employer_can_open_own_company_profile(self):
        self.login_as(self.employer)

        response = self.client.get(reverse('company_profile'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.company.name)

    def test_only_job_owner_can_update_application_status(self):
        application = Application.objects.create(user=self.seeker, job=self.job)
        self.login_as(self.other_employer)

        self.client.post(
            reverse('update_application_status', args=[application.id, 'accepted'])
        )

        application.refresh_from_db()
        self.assertEqual(application.status, 'pending')

    def test_status_update_is_idempotent(self):
        application = Application.objects.create(user=self.seeker, job=self.job)
        self.login_as(self.employer)
        url = reverse('update_application_status', args=[application.id, 'accepted'])

        self.client.post(url)
        self.client.post(url)

        application.refresh_from_db()
        self.assertEqual(application.status, 'accepted')
        self.assertEqual(
            Notification.objects.filter(user=self.seeker, title='Application accepted').count(),
            1,
        )

    def test_filled_job_closes_and_rejects_remaining_pending_applicants(self):
        second_seeker = UserProfile.objects.create(
            first_name='Daniel',
            last_name='Lee',
            email='second@example.com',
            phone='+60444444444',
            role='job_seeker',
            is_verified=True,
            password='unused',
        )
        accepted_application = Application.objects.create(user=self.seeker, job=self.job)
        pending_application = Application.objects.create(user=second_seeker, job=self.job)
        self.login_as(self.employer)

        self.client.post(
            reverse('update_application_status', args=[accepted_application.id, 'accepted'])
        )

        self.job.refresh_from_db()
        pending_application.refresh_from_db()
        self.assertEqual(self.job.status, 'closed')
        self.assertTrue(self.job.is_filled)
        self.assertEqual(pending_application.status, 'rejected')
        self.assertTrue(
            Notification.objects.filter(user=second_seeker, title='Position filled').exists()
        )
        self.assertEqual(len(mail.outbox), 2)

        self.client.post(
            reverse('update_application_status', args=[accepted_application.id, 'rejected'])
        )
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'closed')

    def test_chat_requires_a_valid_application_relationship(self):
        self.login_as(self.seeker)
        response = self.client.post(
            reverse('send_message'),
            {'receiver_id': self.employer.id, 'job_id': self.job.id, 'message': 'Hello'},
        )
        self.assertEqual(response.status_code, 403)

        Application.objects.create(user=self.seeker, job=self.job)
        response = self.client.post(
            reverse('send_message'),
            {'receiver_id': self.employer.id, 'job_id': self.job.id, 'message': 'Hello'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ChatMessage.objects.count(), 1)

    def test_delete_chat_only_hides_it_for_requesting_user(self):
        Application.objects.create(user=self.seeker, job=self.job)
        chat_message = ChatMessage.objects.create(
            sender=self.seeker,
            receiver=self.employer,
            job=self.job,
            message='Hello',
        )
        self.login_as(self.seeker)

        self.client.post(reverse('delete_chat', args=[self.job.id, self.employer.id]))

        self.assertTrue(ChatMessage.objects.filter(id=chat_message.id).exists())
        self.assertTrue(chat_message.hidden_for.filter(id=self.seeker.id).exists())
        self.assertFalse(chat_message.hidden_for.filter(id=self.employer.id).exists())

    def test_recommendations_refresh_for_current_open_jobs(self):
        Resume.objects.create(
            user=self.seeker,
            resume_file='resumes/test.pdf',
            resume_text='Python Django SQL developer',
            skills='Python, Django, SQL',
        )
        self.login_as(self.seeker)

        self.client.get(reverse('recommendations'))
        self.assertTrue(Recommendation.objects.filter(user=self.seeker, job=self.job).exists())

        self.job.status = 'closed'
        self.job.save()
        new_job = Job.objects.create(
            company=self.company,
            title='Backend Engineer',
            description='Python backend systems',
            skills_required='Python',
            status='open',
        )

        self.client.get(reverse('recommendations'))

        self.assertFalse(Recommendation.objects.filter(user=self.seeker, job=self.job).exists())
        self.assertTrue(Recommendation.objects.filter(user=self.seeker, job=new_job).exists())

    def test_low_top_ranked_job_is_not_labelled_best_match(self):
        self.job.status = 'closed'
        self.job.save()
        low_match_job = Job.objects.create(
            company=self.company,
            title='Marine Biologist',
            description='Study coral reef ecosystems and marine wildlife.',
            skills_required='Marine biology, Oceanography',
            status='open',
        )
        Resume.objects.create(
            user=self.seeker,
            resume_file='resumes/low-match.pdf',
            resume_text='Python Django SQL software developer',
            skills='Python, Django, SQL',
        )
        self.login_as(self.seeker)

        response = self.client.get(reverse('recommendations'))
        recommendation = Recommendation.objects.get(user=self.seeker, job=low_match_job)

        self.assertLess(recommendation.score, 10)
        self.assertContains(response, 'Low Match')
        self.assertNotContains(response, 'best-match-card')

    def test_dashboard_job_status_counts_are_exclusive(self):
        closed_job = Job.objects.create(
            company=self.company,
            title='Closed Role',
            description='Closed',
            status='closed',
        )
        filled_job = Job.objects.create(
            company=self.company,
            title='Filled Role',
            description='Filled',
            status='open',
            vacancies=1,
        )
        Application.objects.create(
            user=self.seeker,
            job=filled_job,
            status='accepted',
        )
        self.login_as(self.employer)

        response = self.client.get(reverse('employer_dashboard'))

        self.assertEqual(response.context['open_jobs'], 1)
        self.assertEqual(response.context['closed_jobs'], 1)
        self.assertEqual(response.context['filled_jobs'], 1)
        self.assertEqual(
            response.context['open_jobs'] +
            response.context['closed_jobs'] +
            response.context['filled_jobs'],
            Job.objects.filter(company=self.company).count(),
        )

    def test_role_guards_block_cross_role_pages(self):
        self.login_as(self.employer)
        response = self.client.get(reverse('user_jobs'))
        self.assertRedirects(response, reverse('employer_dashboard'))

        self.client.cookies.clear()
        self.login_as(self.seeker)
        response = self.client.get(reverse('manage_jobs'))
        self.assertRedirects(response, reverse('dashboard'))

    def test_withdraw_preserves_history_notifies_employer_and_allows_reapply(self):
        application = Application.objects.create(user=self.seeker, job=self.job)
        Resume.objects.create(
            user=self.seeker,
            resume_file='resumes/reapply.pdf',
            resume_text='Python Django SQL',
        )
        self.login_as(self.seeker)

        self.client.post(reverse('cancel_application', args=[self.job.id]))

        application.refresh_from_db()
        self.assertEqual(application.status, 'withdrawn')
        self.assertIsNotNone(application.withdrawn_at)
        self.assertTrue(
            Notification.objects.filter(user=self.employer, title='Application withdrawn').exists()
        )
        chat_response = self.client.post(
            reverse('send_message'),
            {'receiver_id': self.employer.id, 'job_id': self.job.id, 'message': 'Hello'},
        )
        self.assertEqual(chat_response.status_code, 403)

        self.client.post(reverse('apply_job', args=[self.job.id]))
        application.refresh_from_db()
        self.assertEqual(application.status, 'pending')
        self.assertIsNone(application.withdrawn_at)

    def test_filled_job_cannot_be_reopened_without_more_vacancies(self):
        Application.objects.create(
            user=self.seeker,
            job=self.job,
            status='accepted',
        )
        self.job.status = 'closed'
        self.job.save()
        self.login_as(self.employer)

        self.client.post(reverse('toggle_job_status', args=[self.job.id]))

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'closed')

    def test_job_form_rejects_invalid_vacancies_and_salary_range(self):
        form = JobForm(data={
            'title': 'Developer',
            'description': 'Build software',
            'job_type': 'Full-Time',
            'vacancies': 0,
            'salary_min': 5000,
            'salary_max': 3000,
            'status': 'open',
        })

        self.assertFalse(form.is_valid())
        self.assertIn('vacancies', form.errors)
        self.assertIn('salary_max', form.errors)

    def test_private_resume_requires_owner_or_related_employer(self):
        resume = Resume.objects.create(
            user=self.seeker,
            resume_file=SimpleUploadedFile('resume.pdf', b'%PDF-1.4 test', content_type='application/pdf'),
            resume_text='Python developer',
        )
        Application.objects.create(user=self.seeker, job=self.job)

        self.login_as(self.other_employer)
        self.assertEqual(self.client.get(reverse('view_resume', args=[resume.id])).status_code, 403)

        self.client.cookies.clear()
        self.login_as(self.employer)
        employer_response = self.client.get(reverse('view_resume', args=[resume.id]))
        self.assertEqual(employer_response.status_code, 200)
        employer_response.close()

        self.client.cookies.clear()
        self.login_as(self.seeker)
        owner_response = self.client.get(reverse('view_resume', args=[resume.id]))
        self.assertEqual(owner_response.status_code, 200)
        owner_response.close()

    def test_resume_upload_rejects_spoofed_pdf(self):
        self.login_as(self.seeker)
        fake_pdf = SimpleUploadedFile(
            'resume.pdf',
            b'not really a pdf',
            content_type='application/pdf',
        )

        self.client.post(reverse('upload_resume'), {'resume_file': fake_pdf})

        self.assertFalse(Resume.objects.filter(user=self.seeker).exists())

    def test_otp_expires_and_resend_has_cooldown(self):
        sent, _ = send_otp(
            self.seeker,
            'Test OTP',
            'Your code is {otp}',
        )
        self.assertTrue(sent)

        sent_again, cooldown_message = send_otp(
            self.seeker,
            'Test OTP',
            'Your code is {otp}',
        )
        self.assertFalse(sent_again)
        self.assertIn('Please wait', cooldown_message)

        self.seeker.otp_created_at = timezone.now() - timedelta(minutes=6)
        self.seeker.save(update_fields=['otp_created_at'])
        valid, error = check_otp(self.seeker, self.seeker.otp_code)
        self.assertFalse(valid)
        self.assertIn('expired', error)

    def test_application_keeps_match_snapshot_after_recommendation_is_removed(self):
        resume = Resume.objects.create(
            user=self.seeker,
            resume_file='resumes/snapshot.pdf',
            resume_text='Python Django SQL',
        )
        Recommendation.objects.create(
            user=self.seeker,
            resume=resume,
            job=self.job,
            score=42.5,
            matched_skills='Python, Django',
            missing_skills='Docker',
        )
        self.login_as(self.seeker)
        self.client.post(reverse('apply_job', args=[self.job.id]))
        Recommendation.objects.filter(user=self.seeker, job=self.job).delete()

        application = Application.objects.get(user=self.seeker, job=self.job)
        self.assertEqual(application.match_score, 42.5)
        self.assertEqual(application.matched_skills, 'Python, Django')

        self.client.cookies.clear()
        self.login_as(self.employer)
        response = self.client.get(reverse('view_applicants'))
        self.assertEqual(response.context['applications'][0].match_score, 42.5)

    def test_all_portal_templates_parse(self):
        template_dir = Path(__file__).resolve().parent / 'templates' / 'portal'
        for template_path in template_dir.glob('*.html'):
            get_template(f'portal/{template_path.name}')

    def test_login_distinguishes_unregistered_account_from_wrong_password(self):
        unregistered_response = self.client.post(reverse('login'), {
            'login_type': 'password',
            'email': 'notregistered@example.com',
            'password': 'Password123!',
        })
        self.assertTrue(unregistered_response.context['account_not_registered'])
        self.assertContains(unregistered_response, 'Account Not Registered')

        self.seeker.set_password('Password123!')
        self.seeker.save(update_fields=['password'])
        wrong_password_response = self.client.post(reverse('login'), {
            'login_type': 'password',
            'email': self.seeker.email,
            'password': 'DefinitelyWrong123!',
        })
        self.assertTrue(wrong_password_response.context['wrong_password'])
        self.assertContains(wrong_password_response, 'Wrong password entered.')

    def test_public_contact_and_employer_filter_updates_render(self):
        contact_response = self.client.get(reverse('public_contact'))
        self.assertContains(contact_response, 'support@hirematch.com')

        self.login_as(self.employer)
        manage_response = self.client.get(reverse('manage_jobs'))
        self.assertContains(manage_response, 'select-filter-arrow')

    def test_forgot_password_verifies_otp_before_showing_password_form(self):
        self.seeker.set_password('OldPassword123!')
        self.seeker.save(update_fields=['password'])

        response = self.client.post(reverse('forgot_password'), {'email': self.seeker.email})
        self.assertRedirects(response, reverse('verify_otp'))
        self.seeker.refresh_from_db()
        otp = self.seeker.otp_code
        self.assertTrue(otp)

        separate_client_response = self.client.get(reverse('reset_password'))
        self.assertRedirects(separate_client_response, reverse('forgot_password'))

        session = self.client.session
        session['verify_user'] = self.seeker.id
        session['otp_purpose'] = 'reset_password'
        session.save()
        verify_response = self.client.post(reverse('verify_otp'), {'otp': otp})
        self.assertRedirects(verify_response, reverse('reset_password'))
        self.seeker.refresh_from_db()
        self.assertFalse(self.seeker.otp_code)

        reset_page = self.client.get(reverse('reset_password'))
        self.assertEqual(reset_page.status_code, 200)
        self.assertNotContains(reset_page, 'name="otp"')

        reset_response = self.client.post(reverse('reset_password'), {
            'new_password': 'password',
            'confirm_password': 'password',
        })
        self.assertRedirects(reset_response, reverse('login'))
        self.seeker.refresh_from_db()
        self.assertTrue(self.seeker.check_password('password'))

    def test_common_password_validator_is_disabled(self):
        validator_names = {item['NAME'] for item in settings.AUTH_PASSWORD_VALIDATORS}
        self.assertNotIn(
            'django.contrib.auth.password_validation.CommonPasswordValidator',
            validator_names,
        )

    def test_employer_can_view_public_company_job_details(self):
        self.login_as(self.employer)
        response = self.client.get(reverse('job_detail', args=[self.job.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Job Seekers Only')
        self.assertContains(response, reverse('public_company_detail', args=[self.company.id]))

    def test_public_pages_use_standardized_hero(self):
        urls = [
            reverse('public_jobs'),
            reverse('public_companies'),
            reverse('public_about'),
            reverse('public_contact'),
            reverse('public_company_detail', args=[self.company.id]),
        ]
        for url in urls:
            self.assertContains(self.client.get(url), 'public-page-header')

    def test_manage_profile_rejects_duplicate_phone_number(self):
        original_phone = self.seeker.phone
        self.login_as(self.seeker)

        response = self.client.post(reverse('manage_profile'), {
            'first_name': self.seeker.first_name,
            'last_name': self.seeker.last_name,
            'phone': self.employer.phone,
            'bio': '',
            'skills': '',
            'location': '',
            'linkedin': '',
            'github': '',
        }, follow=True)

        self.seeker.refresh_from_db()
        self.assertEqual(self.seeker.phone, original_phone)
        self.assertContains(response, 'already registered to another account')

    def test_job_keyword_search_includes_company_description_and_skills(self):
        searchable_job = Job.objects.create(
            company=self.company,
            title='Cloud Engineer',
            description='Build resilient microservices for enterprise platforms.',
            location='Putrajaya',
            skills_required='Kubernetes, Docker',
            experience_required='Two years',
            education_required='Bachelor of Software Engineering',
            status='open',
        )

        for keyword in [
            'Alpha Tech', 'microservices', 'Kubernetes', 'Putrajaya',
            'Two years', 'Bachelor of Software Engineering'
        ]:
            response = self.client.get(reverse('public_jobs'), {'q': keyword})
            self.assertContains(response, searchable_job.title)

        self.login_as(self.seeker)
        response = self.client.get(reverse('user_jobs'), {'q': 'Docker'})
        self.assertContains(response, searchable_job.title)

        for url_name in ['public_jobs', 'user_jobs']:
            response = self.client.get(reverse(url_name))
            self.assertContains(response, 'Search jobs, companies, skills, or locations')
            self.assertNotContains(response, 'name="location"')
            self.assertNotContains(response, 'name="education"')

    def test_salary_filter_supports_legacy_salary_text(self):
        legacy_job = Job.objects.create(
            company=self.company,
            title='Legacy Salary Role',
            description='Legacy salary data',
            salary='RM3,000 - RM4,500',
            salary_min=None,
            salary_max=None,
            status='open',
        )

        matching_response = self.client.get(reverse('public_jobs'), {'salary_min': '4000'})
        self.assertContains(matching_response, legacy_job.title)

        nonmatching_response = self.client.get(reverse('public_jobs'), {'salary_min': '4600'})
        self.assertNotContains(nonmatching_response, legacy_job.title)

    def test_job_seeker_salary_filter_uses_numeric_salary_fields(self):
        matching_job = Job.objects.create(
            company=self.company,
            title='Contained Salary Role',
            description='Salary fully inside selected range',
            salary_min=2500,
            salary_max=3500,
            status='open',
        )
        over_max_job = Job.objects.create(
            company=self.company,
            title='Over Maximum Salary Role',
            description='Salary exceeds selected maximum',
            salary_min=2800,
            salary_max=4200,
            status='open',
        )
        numeric_job = Job.objects.create(
            company=self.company,
            title='Numeric Salary Role',
            description='Current salary data',
            salary='Outdated text',
            salary_min=4000,
            salary_max=6000,
            status='open',
        )
        legacy_job = Job.objects.create(
            company=self.company,
            title='Legacy Job Seeker Role',
            description='No numeric salary data',
            salary='RM1,000 - RM2,000',
            status='open',
        )
        self.login_as(self.seeker)

        response = self.client.get(reverse('user_jobs'), {
            'salary_min': '1000',
            'salary_max': '4000',
        })

        self.assertContains(response, matching_job.title)
        self.assertNotContains(response, over_max_job.title)
        self.assertNotContains(response, numeric_job.title)
        self.assertNotContains(response, legacy_job.title)

    def test_job_seeker_search_sorting_and_salary_display(self):
        lower_job = Job.objects.create(
            company=self.company,
            title='Alpha Analyst',
            description='Python reporting',
            salary='Old salary value',
            salary_min=2500,
            salary_max=3500,
            status='open',
        )
        higher_job = Job.objects.create(
            company=self.company,
            title='Zulu Engineer',
            description='Python engineering',
            salary_min=5000,
            salary_max=7000,
            status='open',
        )
        self.login_as(self.seeker)

        response = self.client.get(reverse('user_jobs'), {'sort': 'salary_low'})
        jobs = list(response.context['jobs'])
        self.assertLess(jobs.index(lower_job), jobs.index(higher_job))
        self.assertContains(response, 'RM2,500 - RM3,500')
        self.assertContains(response, 'Salary: Low to High')

    def test_resume_generator_creates_structured_pdf(self):
        response = self.client.post(reverse('resume_generator'), {
            'first_name': 'Aisyah',
            'last_name': 'Rahman',
            'professional_title': 'Software Developer',
            'email': 'aisyah@example.com',
            'phone': '+60111111111',
            'location': 'Kuala Lumpur',
            'summary': 'Developer focused on reliable web applications.',
            'technical_skills': 'Python, Django, SQL',
            'soft_skills': 'Communication, teamwork',
            'work_job_title': ['Developer Intern'],
            'work_company': ['Alpha Tech'],
            'work_start': ['Jan 2025'],
            'work_end': ['Jun 2025'],
            'work_responsibilities': ['Built and tested Django features.'],
            'education_qualification': ['Bachelor of Computer Science'],
            'education_institution': ['Example University'],
            'education_year': ['2026'],
            'education_cgpa': ['3.70'],
            'project_name': ['HireMatch'],
            'project_technologies': ['Django, PostgreSQL'],
            'project_description': ['Developed a job recommendation portal.'],
            'certifications': 'Cloud Fundamentals',
            'languages': 'Malay, English',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_dashboard_shows_five_most_recent_applications(self):
        self.login_as(self.seeker)
        Application.objects.create(user=self.seeker, job=self.job)

        response = self.client.get(reverse('dashboard'))

        self.assertContains(response, 'Recent Applications')
        self.assertContains(response, self.job.title)
        self.assertContains(response, 'View All Applications')

    def test_manage_resume_shows_improved_guidance(self):
        Resume.objects.create(
            user=self.seeker,
            resume_file='resumes/tips.pdf',
            resume_text='Python developer',
        )
        self.login_as(self.seeker)

        response = self.client.get(reverse('manage_resume'))

        self.assertContains(response, 'Tailor your resume to the role')
        self.assertContains(response, 'measurable results')
        self.assertContains(response, 'Proofread your contact details')

    def test_application_status_survives_email_delivery_failure(self):
        application = Application.objects.create(user=self.seeker, job=self.job)
        self.login_as(self.employer)

        with patch('portal.views.send_mail', side_effect=RuntimeError('SMTP unavailable')):
            response = self.client.post(
                reverse('update_application_status', args=[application.id, 'rejected']),
                follow=True,
            )

        application.refresh_from_db()
        self.assertEqual(application.status, 'rejected')
        self.assertContains(response, 'email notification could not be sent')

    def test_notification_dropdown_endpoint_marks_only_current_user_notifications_read(self):
        own_notification = Notification.objects.create(
            user=self.seeker,
            title='Application update',
            message='Your application was updated.',
        )
        other_notification = Notification.objects.create(
            user=self.employer,
            title='New applicant',
            message='A candidate applied.',
        )
        self.login_as(self.seeker)

        response = self.client.post(reverse('mark_notifications_read'))

        own_notification.refresh_from_db()
        other_notification.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(own_notification.is_read)
        self.assertFalse(other_notification.is_read)

    def test_manage_jobs_status_filters_keep_filled_separate_from_closed(self):
        Application.objects.create(
            user=self.seeker,
            job=self.job,
            status='accepted',
        )
        closed_job = Job.objects.create(
            company=self.company,
            title='Closed Without Hire',
            description='Applications closed manually.',
            status='closed',
            vacancies=2,
        )
        self.login_as(self.employer)

        filled_response = self.client.get(reverse('manage_jobs'), {'status': 'filled'})
        self.assertContains(filled_response, self.job.title)
        self.assertNotContains(filled_response, closed_job.title)

        closed_response = self.client.get(reverse('manage_jobs'), {'status': 'closed'})
        self.assertContains(closed_response, closed_job.title)
        self.assertNotContains(closed_response, self.job.title)

        open_response = self.client.get(reverse('manage_jobs'), {'status': 'open'})
        self.assertNotContains(open_response, self.job.title)

    def test_logout_requires_post_and_clears_session(self):
        self.login_as(self.seeker)

        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        response = self.client.post(reverse('logout'))

        self.assertRedirects(response, reverse('login'))
        self.assertNotIn('user_id', self.client.session)

    def test_project_uses_malaysia_timezone(self):
        self.assertEqual(settings.TIME_ZONE, 'Asia/Kuala_Lumpur')

    def test_chat_api_formats_message_time_in_malaysia_timezone(self):
        Application.objects.create(user=self.seeker, job=self.job)
        message = ChatMessage.objects.create(
            sender=self.seeker,
            receiver=self.employer,
            job=self.job,
            message='Hello',
        )
        utc_time = datetime(2026, 6, 21, 4, 6, tzinfo=datetime_timezone.utc)
        ChatMessage.objects.filter(id=message.id).update(timestamp=utc_time)
        self.login_as(self.seeker)

        response = self.client.get(
            reverse('fetch_messages', args=[self.job.id, self.employer.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['messages'][0]['time'], '12:06 PM')

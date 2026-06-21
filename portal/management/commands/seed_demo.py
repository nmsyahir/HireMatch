from django.core.management.base import BaseCommand

from portal.models import Company, Job, UserProfile


class Command(BaseCommand):
    help = "Create demo users, company, and jobs for presentation/testing."

    def handle(self, *args, **options):
        employer, created = UserProfile.objects.get_or_create(
            email='employer@demo.com',
            defaults={
                'first_name': 'Demo',
                'last_name': 'Employer',
                'phone': '+60111111111',
                'role': 'employer',
                'is_verified': True,
            }
        )

        if created:
            employer.set_password('Password123')
            employer.save()

        seeker, created = UserProfile.objects.get_or_create(
            email='jobseeker@demo.com',
            defaults={
                'first_name': 'Demo',
                'last_name': 'Seeker',
                'phone': '+60222222222',
                'role': 'job_seeker',
                'is_verified': True,
            }
        )

        if created:
            seeker.set_password('Password123')
            seeker.save()

        company, _ = Company.objects.get_or_create(
            employer=employer,
            defaults={
                'name': 'DemoTech Solutions',
                'industry': 'Information Technology',
                'location': 'Kuala Lumpur',
                'website': 'https://example.com',
                'description': 'DemoTech Solutions builds modern software platforms and hires early-career technology talent.',
            }
        )

        demo_jobs = [
            {
                'title': 'Junior Django Developer',
                'description': 'Build and maintain Django web applications, write clean backend code, and work with PostgreSQL APIs.',
                'location': 'Kuala Lumpur',
                'job_type': 'Full-Time',
                'salary': 'RM3000 - RM4500',
                'salary_min': 3000,
                'salary_max': 4500,
                'vacancies': 2,
                'skills_required': 'Python, Django, SQL, REST API',
                'experience_required': 'Fresh Graduate',
                'education_required': 'Diploma or Bachelor Degree in Computer Science',
            },
            {
                'title': 'IT Support Intern',
                'description': 'Assist with troubleshooting, documentation, user support, and basic network administration tasks.',
                'location': 'Cyberjaya',
                'job_type': 'Internship',
                'salary': 'RM800 - RM1200',
                'salary_min': 800,
                'salary_max': 1200,
                'vacancies': 3,
                'skills_required': 'Troubleshooting, Windows, Networking, Communication',
                'experience_required': 'Internship',
                'education_required': 'Diploma in Information Technology',
            },
        ]

        for job_data in demo_jobs:
            Job.objects.get_or_create(
                company=company,
                title=job_data['title'],
                defaults=job_data
            )

        self.stdout.write(self.style.SUCCESS('Demo data ready.'))
        self.stdout.write('Employer: employer@demo.com / Password123')
        self.stdout.write('Job seeker: jobseeker@demo.com / Password123')

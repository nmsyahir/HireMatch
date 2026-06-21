from django import forms

from .models import Job


class JobForm(forms.ModelForm):
    ALLOWED_JOB_TYPES = {'', 'Full-Time', 'Part-Time', 'Internship'}

    class Meta:
        model = Job
        fields = [
            'title', 'description', 'location', 'job_type', 'salary',
            'salary_min', 'salary_max', 'vacancies', 'status',
            'skills_required', 'experience_required', 'education_required',
        ]

    def clean_title(self):
        title = (self.cleaned_data.get('title') or '').strip()
        if not title:
            raise forms.ValidationError('Job title is required.')
        return title

    def clean_description(self):
        description = (self.cleaned_data.get('description') or '').strip()
        if not description:
            raise forms.ValidationError('Job description is required.')
        return description

    def clean_job_type(self):
        job_type = (self.cleaned_data.get('job_type') or '').strip()
        if job_type not in self.ALLOWED_JOB_TYPES:
            raise forms.ValidationError('Select a valid job type.')
        return job_type

    def clean(self):
        cleaned = super().clean()
        salary_min = cleaned.get('salary_min')
        salary_max = cleaned.get('salary_max')
        vacancies = cleaned.get('vacancies')
        status = cleaned.get('status')

        if vacancies is not None and vacancies < 1:
            self.add_error('vacancies', 'Number of vacancies must be at least 1.')

        if salary_min is not None and salary_max is not None and salary_min > salary_max:
            self.add_error('salary_max', 'Maximum salary must be greater than or equal to minimum salary.')

        if self.instance.pk and vacancies is not None:
            accepted_count = self.instance.applications.filter(status='accepted').count()
            if vacancies < accepted_count:
                self.add_error('vacancies', f'Vacancies cannot be lower than the {accepted_count} accepted applicants.')
            if status == 'open' and accepted_count >= vacancies:
                self.add_error('status', 'Increase the number of vacancies before reopening a filled job.')

        return cleaned

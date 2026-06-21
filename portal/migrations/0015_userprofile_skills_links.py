from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0014_job_status_salary_recommendation_explain'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='github',
            field=models.URLField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='linkedin',
            field=models.URLField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='skills',
            field=models.TextField(blank=True),
        ),
    ]

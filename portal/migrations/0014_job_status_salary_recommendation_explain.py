from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0013_job_vacancies_notification'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='salary_max',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='job',
            name='salary_min',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='job',
            name='status',
            field=models.CharField(choices=[('open', 'Open'), ('closed', 'Closed')], default='open', max_length=20),
        ),
        migrations.AddField(
            model_name='recommendation',
            name='matched_skills',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='recommendation',
            name='missing_skills',
            field=models.TextField(blank=True),
        ),
    ]

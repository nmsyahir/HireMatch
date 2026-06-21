from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('portal', '0012_remove_job_employer_userprofile_bio_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='vacancies',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=150)),
                ('message', models.TextField()),
                ('is_read', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notifications', to='portal.userprofile')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]

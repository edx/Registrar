# Generated by Django 1.11.18 on 2019-02-25 15:44


from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('enrollments', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='historicalorganizationprogrammembership',
            name='history_user',
        ),
        migrations.RemoveField(
            model_name='historicalorganizationprogrammembership',
            name='organization',
        ),
        migrations.RemoveField(
            model_name='historicalorganizationprogrammembership',
            name='program',
        ),
        migrations.AlterUniqueTogether(
            name='organizationprogrammembership',
            unique_together=set(),
        ),
        migrations.RemoveField(
            model_name='organizationprogrammembership',
            name='organization',
        ),
        migrations.RemoveField(
            model_name='organizationprogrammembership',
            name='program',
        ),
        migrations.RemoveField(
            model_name='program',
            name='organizations',
        ),
        migrations.AddField(
            model_name='program',
            name='key',
            field=models.CharField(default=None, max_length=255, unique=True),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='program',
            name='managing_organization',
            field=models.ForeignKey(default=None, on_delete=django.db.models.deletion.CASCADE, related_name='programs', to='enrollments.Organization'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='program',
            name='url',
            field=models.URLField(null=True),
        ),
        migrations.AlterField(
            model_name='organization',
            name='key',
            field=models.CharField(max_length=255, unique=True),
        ),
        migrations.DeleteModel(
            name='HistoricalOrganizationProgramMembership',
        ),
        migrations.DeleteModel(
            name='OrganizationProgramMembership',
        ),
    ]

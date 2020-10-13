# Generated by Django 1.11.20 on 2019-04-18 15:28


from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_add_pending_user_organization_group'),
    ]

    operations = [
        migrations.CreateModel(
            name='JobPermissionSupport',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ],
            options={
                'managed': False,
                'permissions': (('job_global_read', 'Global Job status reading'),),
            },
        ),
    ]

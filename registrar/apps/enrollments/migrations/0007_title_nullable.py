# Generated by Django 1.11.20 on 2019-05-09 02:50


from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('enrollments', '0006_remove_learner'),
    ]

    operations = [
        migrations.AlterField(
            model_name='program',
            name='title',
            field=models.CharField(max_length=255, null=True),
        ),
    ]

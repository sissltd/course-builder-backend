from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("courses", "0008_aigenerationjob_aigenerationitem")]

    operations = [
        migrations.AddField(
            model_name="aigenerationitem",
            name="phase",
            field=models.CharField(
                choices=[
                    ("CREATING_CONTENT", "Creating content"),
                    ("PREPARING_DETAILS", "Preparing course details"),
                ],
                default="CREATING_CONTENT",
                max_length=32,
            ),
            preserve_default=False,
        )
    ]

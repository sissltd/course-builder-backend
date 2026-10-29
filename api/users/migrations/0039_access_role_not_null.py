import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Make access_role required. 0038 has already pointed every user at a role."""

    dependencies = [
        ("authorization", "0002_seed_system_roles"),
        ("users", "0038_backfill_user_access_role"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="access_role",
            field=models.ForeignKey(
                help_text=(
                    "The role whose permissions this user holds. Always has "
                    "base_role equal to `role`; defaults to the built-in role for "
                    "`role` when not set explicitly."
                ),
                on_delete=django.db.models.deletion.PROTECT,
                related_name="members",
                to="authorization.role",
                verbose_name="Access role",
            ),
        ),
    ]

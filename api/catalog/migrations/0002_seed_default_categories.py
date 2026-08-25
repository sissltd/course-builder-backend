from django.db import migrations

SEED_CATEGORIES = [
    ("Web Development", "Frontend, backend, and full-stack engineering courses.", "150000.00", "CREATOR_PREFERRED"),
    ("Data Science & Analytics", "Data analysis, machine learning, and visualisation courses.", "180000.00", "CREATOR_PREFERRED"),
    ("Mobile App Development", "iOS, Android, and cross-platform app development courses.", "160000.00", "CREATOR_PREFERRED"),
    ("UI/UX Design", "Interface design, design systems, and user research courses.", "120000.00", "CREATOR_PREFERRED"),
    ("Cloud & DevOps", "Cloud infrastructure, CI/CD, and site-reliability courses.", "200000.00", "CREATOR_PREFERRED"),
    ("Cybersecurity", "Security engineering, offensive security, and compliance courses.", "190000.00", "CREATOR_PREFERRED"),
    ("Digital Marketing", "Growth, SEO, paid media, and content strategy courses.", "90000.00", "CREATOR_PREFERRED"),
    ("Product Management", "Product discovery, delivery, and analytics courses.", "140000.00", "CREATOR_PREFERRED"),
    ("Blockchain & Web3", "Smart contracts, protocols, and decentralised app courses.", "210000.00", "OPEN"),
    ("Business & Entrepreneurship", "Startup operations, finance, and leadership courses.", "100000.00", "OPEN"),
]


def seed_default_categories(apps, schema_editor):
    from django.utils.text import slugify

    Category = apps.get_model("catalog", "Category")
    for name, description, price, track in SEED_CATEGORIES:
        Category.objects.get_or_create(
            name=name,
            defaults={
                "slug": slugify(name)[:160],
                "description": description,
                "creator_price": price,
                "track_preference": track,
            },
        )


def remove_seeded_categories(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    Category.objects.filter(name__in=[name for name, *_ in SEED_CATEGORIES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_default_categories, remove_seeded_categories),
    ]

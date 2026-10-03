from django.contrib.postgres.operations import BtreeGistExtension
from django.db import migrations


class Migration(migrations.Migration):
    """Enables the Postgres extension that lets an exclusion constraint compare court ids with `=`.
    The constraint itself is added in M5."""

    dependencies = [("courts", "0001_initial")]
    operations = [BtreeGistExtension()]

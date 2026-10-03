from django.core.management.base import BaseCommand

from notifications.services import retry_failed


class Command(BaseCommand):
    help = "Retry failed WhatsApp/email messages (up to 3 attempts each). Run every 30 minutes (cron)."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f"{retry_failed()} message(s) sent on retry."))

from django.core.management.base import BaseCommand

from members.services import send_renewal_reminders


class Command(BaseCommand):
    help = "Email members whose membership ends in 14, 7 or 1 days. Run once a day."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f"Sent {send_renewal_reminders()} reminder email(s)."))

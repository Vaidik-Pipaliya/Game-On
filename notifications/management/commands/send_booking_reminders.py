from django.core.management.base import BaseCommand

from notifications.services import send_booking_reminders


class Command(BaseCommand):
    help = "Remind members about sessions starting in the next 2 hours. Run every 15 minutes (cron)."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f"Sent {send_booking_reminders()} reminder(s)."))

"""Audit trail for sensitive actions (PRD XC-02): who did what, to which record, and the before/after.

Call record() inside the same transaction as the change, so the log and the change are saved
together or not at all.
"""

from .models import AuditLog


def record(user, action, target, summary, **details):
    """target is the model instance acted on; details (before/after values) must be JSON-serialisable."""
    return AuditLog.objects.create(
        user=user if getattr(user, "pk", None) else None,
        action=action,
        target_type=target._meta.label,
        target_id=target.pk,
        summary=summary[:300],
        details=details,
    )

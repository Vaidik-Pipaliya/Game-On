from django import template

register = template.Library()


@register.filter
def rupees(paise):
    """Show integer paise as rupees: 120050 -> ₹1,200.50, 120000 -> ₹1,200, -5000 -> -₹50."""
    paise = int(paise)
    sign = "-" if paise < 0 else ""
    whole, rest = divmod(abs(paise), 100)
    return f"{sign}₹{whole:,}.{rest:02d}" if rest else f"{sign}₹{whole:,}"


@register.filter
def rupees_input(paise):
    """Paise as a plain number for a form field: 135050 -> 1350.50, 135000 -> 1350."""
    whole, rest = divmod(int(paise), 100)
    return f"{whole}.{rest:02d}" if rest else str(whole)

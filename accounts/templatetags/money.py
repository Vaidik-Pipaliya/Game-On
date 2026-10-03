from django import template

register = template.Library()


def indian_grouping(number):
    """1234567 -> '12,34,567': the last three digits, then groups of two (lakh, crore)."""
    digits = str(number)
    if len(digits) <= 3:
        return digits
    head, last_three = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [last_three])


@register.filter
def rupees(paise):
    """Show integer paise as rupees: 12500050 -> ₹1,25,000.50, 120000 -> ₹1,200, -5000 -> -₹50."""
    paise = int(paise)
    sign = "-" if paise < 0 else ""
    whole, rest = divmod(abs(paise), 100)
    text = f"{sign}₹{indian_grouping(whole)}"
    return f"{text}.{rest:02d}" if rest else text


@register.filter
def rupees_input(paise):
    """Paise as a plain number for a form field: 135050 -> 1350.50, 135000 -> 1350."""
    whole, rest = divmod(int(paise), 100)
    return f"{whole}.{rest:02d}" if rest else str(whole)

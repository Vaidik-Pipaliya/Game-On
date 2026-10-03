from django import template

register = template.Library()


@register.filter
def rupees(paise):
    """Show integer paise as rupees: 120050 -> ₹1,200.50, 120000 -> ₹1,200."""
    whole, rest = divmod(int(paise), 100)
    return f"₹{whole:,}.{rest:02d}" if rest else f"₹{whole:,}"

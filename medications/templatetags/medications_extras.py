from django import template

register = template.Library()


@register.filter
def split(value, arg="\n"):
    if not value:
        return []
    return [v.strip() for v in value.split(arg) if v.strip()]

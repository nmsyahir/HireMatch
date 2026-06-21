from django import template

register = template.Library()

@register.filter
def split(value, key):
    if value:
        return value.split(key)
    return []
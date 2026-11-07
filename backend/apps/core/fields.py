"""Number field for everything a person types: decimals allowed, whole values stay plain ints in Python."""
from decimal import Decimal, InvalidOperation

from django.core.validators import MinValueValidator
from django.db import models
from rest_framework import serializers

from .calc import plain

QTY_DIGITS = 16
QTY_PLACES = 3


def parse_qty(value):
    """Validate a number from an action payload; workflow actions bypass the serializers."""
    from .api import BusinessRuleError

    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise BusinessRuleError(f"'{value}' is not a valid number.", "validation")
    if not number.is_finite() or number < 0:
        raise BusinessRuleError(f"'{value}' must be zero or more.", "validation")
    if number != number.quantize(Decimal(1).scaleb(-QTY_PLACES)):
        raise BusinessRuleError(f"'{value}' has more than {QTY_PLACES} decimal places.", "validation")
    return plain(number)


class QtyField(models.DecimalField):
    """Non-negative decimal column. Reads back as int when whole and Decimal otherwise, so existing
    whole-number data behaves exactly as it did on an integer column and stays JSON-safe."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", QTY_DIGITS)
        kwargs.setdefault("decimal_places", QTY_PLACES)
        kwargs.setdefault("validators", [MinValueValidator(0)])
        super().__init__(*args, **kwargs)

    def from_db_value(self, value, expression, connection):
        return None if value is None else plain(value)

    def run_validators(self, value):
        super().run_validators(Decimal(value) if isinstance(value, int) else value)

    def clean(self, value, model_instance):
        return plain(super().clean(value, model_instance))


class QtyJsonField(serializers.DecimalField):
    """API form of QtyField: a JSON number, as the integer columns always were."""

    def to_internal_value(self, data):
        return plain(super().to_internal_value(data))

    def to_representation(self, value):
        value = plain(value)
        return float(value) if isinstance(value, Decimal) else value


serializers.ModelSerializer.serializer_field_mapping[QtyField] = QtyJsonField

import datetime

from database.db_helpers import EmptyField, Empty, UserID, BoundRowFields
from database.setup import BaseModel
from peewee import (
    AutoField, BooleanField, TextField, IntegerField, BigIntegerField, DateTimeField
)

class Payments(BaseModel):
    id = AutoField(primary_key=True)
    user_id = BigIntegerField(null=False)
    telegram_payment_charge_id = TextField(default="")

    amount = IntegerField(null=False)
    paid = BooleanField(default=False)
    processed = BooleanField(default=False)
    invoice_payload = TextField(default="")
    created_at = DateTimeField(default=datetime.datetime.now)
    refunded_at = DateTimeField(default=None, null=True)

    @classmethod
    def build_from_fields(
        cls, payment_id: int | EmptyField = Empty,
        user_id: UserID | EmptyField = Empty,
        telegram_payment_charge_id: str | EmptyField = Empty,
        amount: int | EmptyField = Empty,
        invoice_payload: str | EmptyField = Empty
    ):
        return BoundRowFields(cls, {
            cls.id: payment_id,
            cls.user_id: user_id,
            cls.telegram_payment_charge_id: telegram_payment_charge_id,
            cls.amount: amount,
            cls.invoice_payload: invoice_payload
        })
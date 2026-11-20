from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class Reservation:
    platform: str                 # "Airbnb" / "Booking"
    code: str                     # номер бронирования
    guest: str                    # имя клиента
    checkin: Optional[date]
    checkout: Optional[date]
    nights: int
    gross: Decimal                # сумма, которую заплатил гость (без налогов платформы)
    commission: Decimal           # комиссия платформы
    net: Decimal                  # сумма к выплате хозяину
    currency: str
    status: str = "ok"
    listing: str = ""
    country: str = ""
    payout_date: Optional[date] = None
    # заполняется при конвертации в RON
    rate: Optional[Decimal] = None
    rate_date: Optional[date] = None
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> tuple:
        """Уникальный ключ для отбрасывания повторов (в новых выгрузках Airbnb нет кода)."""
        return (self.platform, self.code or f"{self.guest}|{self.checkin}")

    def ron(self, amount: Decimal) -> Decimal:
        if self.currency == "RON":
            return amount
        if self.rate is None:
            raise ValueError(f"Нет курса BNR для {self.platform} {self.code}")
        return (amount * self.rate).quantize(Decimal("0.01"))

    @property
    def gross_ron(self) -> Decimal:
        return self.ron(self.gross)

    @property
    def commission_ron(self) -> Decimal:
        return self.ron(self.commission)

    @property
    def net_ron(self) -> Decimal:
        return self.ron(self.net)

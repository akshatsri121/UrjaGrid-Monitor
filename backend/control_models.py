"""Validated wire protocol for the two-load firmware (all currents in amperes)."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Branch(StrictModel):
    valid: bool = Field(strict=True)
    voltage_v: float | None = Field(default=None, ge=0, le=26)
    current_a: float | None = Field(default=None, ge=-3.2, le=3.2)
    power_w: float | None = Field(default=None, ge=-84, le=84)
    on: bool = Field(strict=True)
    blocked_reason: str = Field(default='', max_length=100)

    @model_validator(mode='after')
    def check_validity(self):
        values = (self.voltage_v, self.current_a, self.power_w)
        if self.valid and any(v is None for v in values):
            raise ValueError('valid branch requires voltage, current and power')
        if not self.valid and any(v is not None for v in values):
            raise ValueError('invalid branch measurements must be null, not zero')
        return self


class Acknowledgement(StrictModel):
    id: UUID
    status: Literal['applied', 'rejected']
    reason: str = Field(default='', max_length=100)


class Telemetry(StrictModel):
    protocol: Literal[1] = 1
    device_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    boot_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    sequence: int = Field(ge=0, le=9_007_199_254_740_991)
    uptime_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    measured_at: float | None = None
    battery_voltage: float | None = Field(default=None, ge=0, le=26)
    soc_estimate: float | None = Field(default=None, ge=0, le=100)
    soc_source: Literal['generic_vrla_voltage_v1'] = 'generic_vrla_voltage_v1'
    temperature_c: float | None = Field(default=None, ge=-55, le=125)
    automatic_protection: bool | None = Field(default=None, strict=True)
    mode_revision: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,64}$')
    bulb: Branch
    fan: Branch
    acks: list[Acknowledgement] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def check_battery(self):
        if (self.automatic_protection is None) != (self.mode_revision is None):
            raise ValueError('automation state and mode revision must be reported together')
        if (self.battery_voltage is None) != (self.soc_estimate is None):
            raise ValueError('battery voltage and SOC estimate must be available together')
        if self.battery_voltage is not None and not (self.bulb.valid or self.fan.valid):
            raise ValueError('battery estimate requires a valid measurement branch')
        return self


class CommandRequest(StrictModel):
    request_id: UUID
    load: Literal['bulb', 'fan', 'automation']
    on: bool = Field(strict=True)


class NoteRequest(StrictModel):
    text: str = Field(min_length=1, max_length=2000)


class LoginRequest(StrictModel):
    email: str = Field(default='', max_length=254)
    password: str = Field(min_length=1, max_length=256)


class DemoBattery(StrictModel):
    soc: float = Field(ge=0, le=100)

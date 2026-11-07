from datetime import date

from pydantic import BaseModel


class SiteOut(BaseModel):
    id: int
    name: str
    site_type: str
    address: str

    class Config:
        from_attributes = True


class WorkOrderOut(BaseModel):
    id: int
    date: date
    issue_type: str
    description: str

    class Config:
        from_attributes = True


class UtilityBillOut(BaseModel):
    id: int
    billing_month: date
    kwh_usage: float
    cost: float

    class Config:
        from_attributes = True


class VendorInvoiceOut(BaseModel):
    id: int
    date: date
    vendor_name: str
    parts_replaced: str | None
    labor_hours: float
    cost: float

    class Config:
        from_attributes = True


class EquipmentOut(BaseModel):
    id: int
    site_id: int
    name: str
    equipment_type: str
    install_date: date
    expected_lifespan_years: float

    class Config:
        from_attributes = True


class RiskScoreOut(BaseModel):
    equipment_id: int
    equipment_name: str
    failure_probability: float
    age_years: float
    expected_lifespan_years: float
    recent_work_orders_6mo: int
    utility_trend_pct: float
    recommendation: str
    preventive_cost_estimate: float
    emergency_cost_estimate: float

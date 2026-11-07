from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    site_type: Mapped[str] = mapped_column(String)  # bank branch, retail, clinic, office
    address: Mapped[str] = mapped_column(String)

    equipment: Mapped[list["Equipment"]] = relationship(back_populates="site")


class Equipment(Base):
    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    equipment_type: Mapped[str] = mapped_column(String)  # HVAC, generator, elevator
    install_date: Mapped[date] = mapped_column(Date)
    expected_lifespan_years: Mapped[float] = mapped_column(Float)

    site: Mapped["Site"] = relationship(back_populates="equipment")
    work_orders: Mapped[list["WorkOrder"]] = relationship(back_populates="equipment")
    utility_bills: Mapped[list["UtilityBill"]] = relationship(back_populates="equipment")
    invoices: Mapped[list["VendorInvoice"]] = relationship(back_populates="equipment")


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"))
    date: Mapped[date] = mapped_column(Date)
    issue_type: Mapped[str] = mapped_column(String)  # repair, inspection, complaint
    description: Mapped[str] = mapped_column(String)

    equipment: Mapped["Equipment"] = relationship(back_populates="work_orders")


class UtilityBill(Base):
    __tablename__ = "utility_bills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"))
    billing_month: Mapped[date] = mapped_column(Date)
    kwh_usage: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float)

    equipment: Mapped["Equipment"] = relationship(back_populates="utility_bills")


class VendorInvoice(Base):
    __tablename__ = "vendor_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"))
    date: Mapped[date] = mapped_column(Date)
    vendor_name: Mapped[str] = mapped_column(String)
    parts_replaced: Mapped[str] = mapped_column(String, nullable=True)
    labor_hours: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float)

    equipment: Mapped["Equipment"] = relationship(back_populates="invoices")

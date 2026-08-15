import uuid
from datetime import date
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update, delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models import (
    Property, PropertyRatePlan, PricingTier, Season, SeasonPeriod,
    PropertySeasonPrice, Promotion, PromotionPropertyPrice, PricingSetting
)

router = APIRouter()

# ==================================================
# SCHEMAS
# ==================================================

class PropertyRatePlanUpdate(BaseModel):
    name: Optional[str] = None
    standard_capacity: Optional[int] = Field(None, ge=1)
    maximum_capacity: Optional[int] = Field(None, ge=1)
    cleaning_fee: Optional[Decimal] = Field(None, ge=0)
    extra_person_fee_per_night: Optional[Decimal] = Field(None, ge=0)
    refundable_deposit: Optional[Decimal] = Field(None, ge=0)
    pets_allowed: Optional[bool] = None
    minimum_nights: Optional[int] = Field(None, ge=1)
    active: Optional[bool] = None

class PropertyUpdate(BaseModel):
    name: Optional[str] = None
    standard_capacity: Optional[int] = Field(None, ge=1)
    maximum_capacity: Optional[int] = Field(None, ge=1)
    pets_allowed: Optional[bool] = None
    active: Optional[bool] = None
    display_order: Optional[int] = None

class PricingTierCreateUpdate(BaseModel):
    code: str
    name: str
    active: bool = True

class SeasonCreateUpdate(BaseModel):
    code: str
    name: str
    priority: int = Field(..., ge=0)
    active: bool = True

class SeasonPeriodCreate(BaseModel):
    season_id: str
    start_date: date
    end_date: date
    notes: Optional[str] = None
    active: bool = True

class SeasonPeriodUpdate(BaseModel):
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    notes: Optional[str] = None
    active: Optional[bool] = None

class SeasonalPriceUpdateItem(BaseModel):
    property_rate_plan_id: str
    season_id: str
    pricing_tier_id: str
    nightly_rate: Decimal = Field(..., ge=0)
    active: bool = True

class BulkPriceAdjustRequest(BaseModel):
    adjustment_type: str  # "percent_increase", "percent_decrease", "fixed_increase", "fixed_decrease"
    amount: Decimal = Field(..., ge=0)
    season_ids: List[str]  # list of season UUIDs to target
    tier_codes: List[str]  # e.g. ["INTERNATIONAL"], ["LOCAL"], or both
    rate_plan_ids: Optional[List[str]] = None  # target specific rate plans or all if null/empty

class PromotionPropertyPriceItem(BaseModel):
    property_rate_plan_id: str
    nightly_rate: Decimal = Field(..., ge=0)
    active: bool = True

class PromotionCreateUpdate(BaseModel):
    name: str
    description: Optional[str] = None
    start_date: date
    end_date: date
    enabled: bool = True
    waive_pet_fee: bool = False
    priority: int = Field(1000, ge=0)
    property_prices: List[PromotionPropertyPriceItem] = []

class PricingSettingsUpdate(BaseModel):
    currency: Optional[str] = "USD"
    default_pet_fee: Optional[Decimal] = Field(None, ge=0)
    default_extra_person_fee: Optional[Decimal] = Field(None, ge=0)
    multi_property_refundable_deposit: Optional[Decimal] = Field(None, ge=0)

# ==================================================
# 1. PROPERTIES & RATE PLANS
# ==================================================

@router.get("/admin/pricing/properties")
async def get_properties(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Property)
        .options(selectinload(Property.rate_plans))
        .order_by(Property.display_order, Property.code)
    )
    result = await db.execute(stmt)
    properties = result.scalars().all()

    out = []
    for p in properties:
        rate_plans = [
            {
                "id": str(rp.id),
                "property_id": str(rp.property_id),
                "code": rp.code,
                "name": rp.name,
                "standard_capacity": rp.standard_capacity,
                "maximum_capacity": rp.maximum_capacity,
                "cleaning_fee": float(rp.cleaning_fee),
                "extra_person_fee_per_night": float(rp.extra_person_fee_per_night),
                "refundable_deposit": float(rp.refundable_deposit),
                "pets_allowed": rp.pets_allowed,
                "minimum_nights": rp.minimum_nights if rp.minimum_nights is not None else 1,
                "active": rp.active,
            }
            for rp in sorted(p.rate_plans, key=lambda x: x.code)
        ]
        out.append({
            "id": str(p.id),
            "code": p.code,
            "name": p.name,
            "standard_capacity": p.standard_capacity,
            "maximum_capacity": p.maximum_capacity,
            "pets_allowed": p.pets_allowed,
            "active": p.active,
            "display_order": p.display_order,
            "rate_plans": rate_plans
        })
    return out

@router.put("/admin/pricing/properties/{property_id}")
async def update_property(property_id: str, req: PropertyUpdate, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Property).filter(Property.id == property_id))
    prop = res.scalar_one_or_none()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")

    if req.name is not None:
        prop.name = req.name
    if req.standard_capacity is not None:
        prop.standard_capacity = req.standard_capacity
    if req.maximum_capacity is not None:
        if req.maximum_capacity < (req.standard_capacity or prop.standard_capacity):
            raise HTTPException(status_code=400, detail="Maximum capacity cannot be less than standard capacity")
        prop.maximum_capacity = req.maximum_capacity
    if req.pets_allowed is not None:
        prop.pets_allowed = req.pets_allowed
    if req.active is not None:
        prop.active = req.active
    if req.display_order is not None:
        prop.display_order = req.display_order

    await db.commit()
    return {"status": "success"}

@router.put("/admin/pricing/rate-plans/{plan_id}")
async def update_rate_plan(plan_id: str, req: PropertyRatePlanUpdate, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(PropertyRatePlan).filter(PropertyRatePlan.id == plan_id))
    plan = res.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Rate plan not found")

    if req.name is not None:
        plan.name = req.name
    if req.standard_capacity is not None:
        plan.standard_capacity = req.standard_capacity
    if req.maximum_capacity is not None:
        if req.maximum_capacity < (req.standard_capacity or plan.standard_capacity):
            raise HTTPException(status_code=400, detail="Maximum capacity cannot be less than standard capacity")
        plan.maximum_capacity = req.maximum_capacity
    if req.cleaning_fee is not None:
        plan.cleaning_fee = req.cleaning_fee
    if req.extra_person_fee_per_night is not None:
        plan.extra_person_fee_per_night = req.extra_person_fee_per_night
    if req.refundable_deposit is not None:
        plan.refundable_deposit = req.refundable_deposit
    if req.pets_allowed is not None:
        plan.pets_allowed = req.pets_allowed
    if req.minimum_nights is not None:
        plan.minimum_nights = req.minimum_nights
    if req.active is not None:
        plan.active = req.active

    await db.commit()
    return {"status": "success"}

# ==================================================
# 2. PRICING TIERS
# ==================================================

@router.get("/admin/pricing/tiers")
async def get_tiers(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(PricingTier).order_by(PricingTier.code))
    tiers = res.scalars().all()
    return [{"id": str(t.id), "code": t.code, "name": t.name, "active": t.active} for t in tiers]

# ==================================================
# 3. SEASONS & SEASON PERIODS
# ==================================================

@router.get("/admin/pricing/seasons")
async def get_seasons(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Season)
        .options(selectinload(Season.periods))
        .order_by(Season.priority.desc(), Season.code)
    )
    res = await db.execute(stmt)
    seasons = res.scalars().all()

    all_periods_res = await db.execute(select(SeasonPeriod).filter(SeasonPeriod.active == True))
    all_active_periods = all_periods_res.scalars().all()

    # Lookup map so the overlap check never hits the DB (was an N+1 in a nested loop)
    season_by_id = {s.id: s for s in seasons}

    out = []
    for s in seasons:
        periods_out = []
        for p in sorted(s.periods, key=lambda x: x.start_date):
            # Check overlap warnings
            has_overlap = False
            overlap_details = []
            for other in all_active_periods:
                if str(other.id) != str(p.id) and other.active:
                    # Date overlap condition: max(start1, start2) <= min(end1, end2)
                    if max(p.start_date, other.start_date) <= min(p.end_date, other.end_date):
                        has_overlap = True
                        other_season = season_by_id.get(other.season_id)
                        s_name = other_season.name if other_season else "Another Season"
                        overlap_details.append(f"Overlaps with {s_name} ({other.start_date} to {other.end_date})")

            periods_out.append({
                "id": str(p.id),
                "season_id": str(p.season_id),
                "start_date": p.start_date.isoformat(),
                "end_date": p.end_date.isoformat(),
                "notes": p.notes or "",
                "active": p.active,
                "has_overlap": has_overlap,
                "overlap_details": overlap_details
            })

        out.append({
            "id": str(s.id),
            "code": s.code,
            "name": s.name,
            "priority": s.priority,
            "active": s.active,
            "periods": periods_out
        })
    return out

@router.post("/admin/pricing/seasons")
async def create_season(req: SeasonCreateUpdate, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Season).filter(Season.code == req.code))
    if res.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="A season with this code already exists")

    s = Season(id=uuid.uuid4(), code=req.code, name=req.name, priority=req.priority, active=req.active)
    db.add(s)
    await db.commit()
    return {"status": "success", "id": str(s.id)}

@router.put("/admin/pricing/seasons/{season_id}")
async def update_season(season_id: str, req: SeasonCreateUpdate, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Season).filter(Season.id == season_id))
    s = res.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Season not found")

    s.name = req.name
    s.priority = req.priority
    s.active = req.active
    await db.commit()
    return {"status": "success"}

@router.post("/admin/pricing/season-periods")
async def create_season_period(req: SeasonPeriodCreate, db: AsyncSession = Depends(get_db)):
    if req.end_date < req.start_date:
        raise HTTPException(status_code=400, detail="End date cannot be earlier than start date")

    period = SeasonPeriod(
        id=uuid.uuid4(),
        season_id=req.season_id,
        start_date=req.start_date,
        end_date=req.end_date,
        notes=req.notes,
        active=req.active
    )
    db.add(period)
    await db.commit()
    return {"status": "success", "id": str(period.id)}

@router.put("/admin/pricing/season-periods/{period_id}")
async def update_season_period(period_id: str, req: SeasonPeriodUpdate, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(SeasonPeriod).filter(SeasonPeriod.id == period_id))
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Season period not found")

    start = req.start_date if req.start_date is not None else p.start_date
    end = req.end_date if req.end_date is not None else p.end_date

    if end < start:
        raise HTTPException(status_code=400, detail="End date cannot be earlier than start date")

    p.start_date = start
    p.end_date = end
    if req.notes is not None:
        p.notes = req.notes
    if req.active is not None:
        p.active = req.active

    await db.commit()
    return {"status": "success"}

@router.delete("/admin/pricing/season-periods/{period_id}")
async def delete_season_period(period_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(SeasonPeriod).filter(SeasonPeriod.id == period_id))
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Season period not found")

    await db.delete(p)
    await db.commit()
    return {"status": "success"}

# ==================================================
# 4. SEASONAL PRICES MATRIX
# ==================================================

@router.get("/admin/pricing/matrix")
async def get_pricing_matrix(db: AsyncSession = Depends(get_db)):
    # Properties with Rate Plans
    props_res = await db.execute(
        select(Property).options(selectinload(Property.rate_plans)).order_by(Property.display_order)
    )
    props = props_res.scalars().all()

    # Seasons
    seasons_res = await db.execute(select(Season).order_by(Season.priority.desc()))
    seasons = seasons_res.scalars().all()

    # Tiers
    tiers_res = await db.execute(select(PricingTier).order_by(PricingTier.code))
    tiers = tiers_res.scalars().all()

    # Seasonal prices
    prices_res = await db.execute(select(PropertySeasonPrice))
    prices = prices_res.scalars().all()

    price_map = {}
    for pr in prices:
        key = f"{pr.property_rate_plan_id}_{pr.season_id}_{pr.pricing_tier_id}"
        price_map[key] = {
            "id": str(pr.id),
            "nightly_rate": float(pr.nightly_rate),
            "active": pr.active
        }

    return {
        "properties": [
            {
                "id": str(p.id),
                "code": p.code,
                "name": p.name,
                "rate_plans": [
                    {
                        "id": str(rp.id),
                        "code": rp.code,
                        "name": rp.name,
                    }
                    for rp in sorted(p.rate_plans, key=lambda x: x.code)
                ]
            }
            for p in props
        ],
        "seasons": [{"id": str(s.id), "code": s.code, "name": s.name, "priority": s.priority} for s in seasons],
        "tiers": [{"id": str(t.id), "code": t.code, "name": t.name} for t in tiers],
        "prices": price_map
    }

@router.put("/admin/pricing/matrix")
async def update_pricing_matrix(items: List[SeasonalPriceUpdateItem], db: AsyncSession = Depends(get_db)):
    # Single bulk upsert instead of one SELECT + UPDATE/INSERT per cell
    stmt = pg_insert(PropertySeasonPrice).values([
        {
            "property_rate_plan_id": item.property_rate_plan_id,
            "season_id": item.season_id,
            "pricing_tier_id": item.pricing_tier_id,
            "nightly_rate": item.nightly_rate,
            "active": item.active,
        }
        for item in items
    ])
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            PropertySeasonPrice.property_rate_plan_id,
            PropertySeasonPrice.season_id,
            PropertySeasonPrice.pricing_tier_id,
        ],
        set_={
            "nightly_rate": stmt.excluded.nightly_rate,
            "active": stmt.excluded.active,
            "updated_at": func.now(),
        },
    )
    await db.execute(stmt)
    await db.commit()
    return {"status": "success", "count": len(items)}

@router.post("/admin/pricing/matrix/bulk-adjust")
async def bulk_adjust_prices(req: BulkPriceAdjustRequest, db: AsyncSession = Depends(get_db)):
    # Fetch targeting tiers
    tier_res = await db.execute(select(PricingTier).filter(PricingTier.code.in_(req.tier_codes)))
    target_tiers = tier_res.scalars().all()
    tier_ids = [t.id for t in target_tiers]

    if not tier_ids:
        raise HTTPException(status_code=400, detail="No matching pricing tiers specified")

    # SQL arithmetic per adjustment type (single UPDATE, no row-by-row Python writes)
    if req.adjustment_type == "percent_increase":
        rate_expr = PropertySeasonPrice.nightly_rate * (Decimal("1") + req.amount / Decimal("100"))
    elif req.adjustment_type == "percent_decrease":
        rate_expr = PropertySeasonPrice.nightly_rate * (Decimal("1") - req.amount / Decimal("100"))
    elif req.adjustment_type == "fixed_increase":
        rate_expr = PropertySeasonPrice.nightly_rate + req.amount
    elif req.adjustment_type == "fixed_decrease":
        rate_expr = PropertySeasonPrice.nightly_rate - req.amount
    else:
        raise HTTPException(status_code=400, detail="Invalid adjustment type")

    stmt = update(PropertySeasonPrice).where(
        PropertySeasonPrice.season_id.in_(req.season_ids),
        PropertySeasonPrice.pricing_tier_id.in_(tier_ids),
    )
    if req.rate_plan_ids:
        stmt = stmt.where(PropertySeasonPrice.property_rate_plan_id.in_(req.rate_plan_ids))

    stmt = stmt.values(
        nightly_rate=func.greatest(func.round(rate_expr, 2), 0),
        updated_at=func.now(),
    )

    result = await db.execute(stmt)
    await db.commit()
    return {"status": "success", "updated_count": result.rowcount}

# ==================================================
# 5. PROMOTIONS
# ==================================================

@router.get("/admin/pricing/promotions")
async def get_promotions(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Promotion)
        .options(
            selectinload(Promotion.property_prices)
            .selectinload(PromotionPropertyPrice.rate_plan)
            .selectinload(PropertyRatePlan.property)
        )
        .order_by(Promotion.priority.desc(), Promotion.created_at.desc())
    )
    res = await db.execute(stmt)
    promos = res.scalars().all()

    # One aggregate query for rate-plan sibling counts instead of eager-loading
    # every property's full rate-plan graph
    prop_ids = {
        pp.rate_plan.property_id
        for pr in promos
        for pp in pr.property_prices
        if pp.rate_plan and pp.rate_plan.property
    }
    sibling_counts = {}
    if prop_ids:
        count_res = await db.execute(
            select(PropertyRatePlan.property_id, func.count(PropertyRatePlan.id))
            .where(PropertyRatePlan.property_id.in_(prop_ids))
            .group_by(PropertyRatePlan.property_id)
        )
        sibling_counts = dict(count_res.all())

    out = []
    for pr in promos:
        pp_list = []
        for pp in pr.property_prices:
            rp = pp.rate_plan
            prop = rp.property if rp else None
            # Build a display name: "Bungalow 5" or "Bungalow 3 — 2BR"
            if prop and rp:
                # Check if property has multiple rate plans by counting siblings
                sibling_count = sibling_counts.get(prop.id, 1)
                if sibling_count > 1:
                    display_name = f"{prop.name} — {rp.name}"
                else:
                    display_name = prop.name
            else:
                display_name = rp.name if rp else ""

            pp_list.append({
                "id": str(pp.id),
                "property_rate_plan_id": str(pp.property_rate_plan_id),
                "rate_plan_code": rp.code if rp else "",
                "rate_plan_name": rp.name if rp else "",
                "property_name": prop.name if prop else "",
                "display_name": display_name,
                "nightly_rate": float(pp.nightly_rate),
                "active": pp.active
            })
        out.append({
            "id": str(pr.id),
            "name": pr.name,
            "description": pr.description or "",
            "start_date": pr.start_date.isoformat(),
            "end_date": pr.end_date.isoformat(),
            "enabled": pr.enabled,
            "waive_pet_fee": pr.waive_pet_fee,
            "priority": pr.priority,
            "property_prices": pp_list
        })
    return out

@router.post("/admin/pricing/promotions")
async def create_promotion(req: PromotionCreateUpdate, db: AsyncSession = Depends(get_db)):
    if req.end_date < req.start_date:
        raise HTTPException(status_code=400, detail="Promotion end date cannot be earlier than start date")

    # Use explicit transaction
    promo = Promotion(
        id=uuid.uuid4(),
        name=req.name,
        description=req.description,
        start_date=req.start_date,
        end_date=req.end_date,
        enabled=req.enabled,
        waive_pet_fee=req.waive_pet_fee,
        priority=req.priority
    )
    db.add(promo)
    await db.flush()

    for item in req.property_prices:
        pp = PromotionPropertyPrice(
            id=uuid.uuid4(),
            promotion_id=promo.id,
            property_rate_plan_id=item.property_rate_plan_id,
            nightly_rate=item.nightly_rate,
            active=item.active
        )
        db.add(pp)

    await db.commit()
    return {"status": "success", "id": str(promo.id)}

@router.put("/admin/pricing/promotions/{promotion_id}")
async def update_promotion(promotion_id: str, req: PromotionCreateUpdate, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(Promotion).options(selectinload(Promotion.property_prices)).filter(Promotion.id == promotion_id)
    )
    promo = res.scalar_one_or_none()
    if not promo:
        raise HTTPException(status_code=404, detail="Promotion not found")

    if req.end_date < req.start_date:
        raise HTTPException(status_code=400, detail="Promotion end date cannot be earlier than start date")

    promo.name = req.name
    promo.description = req.description
    promo.start_date = req.start_date
    promo.end_date = req.end_date
    promo.enabled = req.enabled
    promo.waive_pet_fee = req.waive_pet_fee
    promo.priority = req.priority

    # Delete existing property prices and replace with updated ones
    await db.execute(delete(PromotionPropertyPrice).filter(PromotionPropertyPrice.promotion_id == promo.id))

    for item in req.property_prices:
        pp = PromotionPropertyPrice(
            id=uuid.uuid4(),
            promotion_id=promo.id,
            property_rate_plan_id=item.property_rate_plan_id,
            nightly_rate=item.nightly_rate,
            active=item.active
        )
        db.add(pp)

    await db.commit()
    return {"status": "success"}

@router.delete("/admin/pricing/promotions/{promotion_id}")
async def delete_promotion(promotion_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Promotion).filter(Promotion.id == promotion_id))
    promo = res.scalar_one_or_none()
    if not promo:
        raise HTTPException(status_code=404, detail="Promotion not found")

    await db.delete(promo)
    await db.commit()
    return {"status": "success"}

# ==================================================
# 6. GENERAL PRICING SETTINGS
# ==================================================

import json

class SingleSettingUpdate(BaseModel):
    value: str
    description: Optional[str] = None

@router.get("/admin/pricing/settings")
async def get_pricing_settings(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(PricingSetting).order_by(PricingSetting.key))
    settings = res.scalars().all()
    
    settings_list = [
        {
            "id": str(s.id),
            "key": s.key,
            "value": s.value,
            "description": s.description or ""
        }
        for s in settings
    ]
    
    out_map = {s.key: s.value for s in settings}
    return {
        "settings": settings_list,
        "currency": out_map.get("currency", "USD"),
        "default_pet_fee": float(out_map.get("default_pet_fee", "30.00")) if "default_pet_fee" in out_map else 30.00,
        "default_extra_person_fee": float(out_map.get("default_extra_person_fee", "10.00")) if "default_extra_person_fee" in out_map else 10.00,
        "multi_property_refundable_deposit": float(out_map.get("multi_property_refundable_deposit", "100.00")) if "multi_property_refundable_deposit" in out_map else 100.00,
    }

@router.put("/admin/pricing/settings/{key}")
async def update_single_pricing_setting(key: str, req: SingleSettingUpdate, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(PricingSetting).filter(PricingSetting.key == key))
    setting = res.scalar_one_or_none()

    if setting and setting.value:
        # Validate JSON if existing value parses as JSON
        try:
            json.loads(setting.value)
            # Existing value is valid JSON, so check new value
            try:
                json.loads(req.value)
            except Exception:
                raise HTTPException(status_code=400, detail=f"Invalid JSON payload for setting key '{key}'")
        except HTTPException:
            raise
        except Exception:
            # Existing value was not JSON, normal text assignment
            pass

    if setting:
        setting.value = req.value
        if req.description is not None:
            setting.description = req.description
    else:
        setting = PricingSetting(id=uuid.uuid4(), key=key, value=req.value, description=req.description)
        db.add(setting)

    await db.commit()
    return {"status": "success"}

@router.put("/admin/pricing/settings")
async def update_pricing_settings(req: PricingSettingsUpdate, db: AsyncSession = Depends(get_db)):
    updates = {}
    if req.currency is not None:
        updates["currency"] = req.currency
    if req.default_pet_fee is not None:
        updates["default_pet_fee"] = str(req.default_pet_fee)
    if req.default_extra_person_fee is not None:
        updates["default_extra_person_fee"] = str(req.default_extra_person_fee)
    if req.multi_property_refundable_deposit is not None:
        updates["multi_property_refundable_deposit"] = str(req.multi_property_refundable_deposit)

    for k, v in updates.items():
        res = await db.execute(select(PricingSetting).filter(PricingSetting.key == k))
        setting = res.scalar_one_or_none()
        if setting:
            setting.value = v
        else:
            db.add(PricingSetting(id=uuid.uuid4(), key=k, value=v))

    await db.commit()
    return {"status": "success"}

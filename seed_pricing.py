import asyncio
import os
import uuid
from datetime import date
from sqlalchemy.future import select
from database import async_session_maker, Base, engine
from models import (
    Property, PropertyRatePlan, PricingTier, Season, SeasonPeriod,
    PropertySeasonPrice, Promotion, PromotionPropertyPrice, PricingSetting
)

async def seed_pricing_data():
    # Guard: ~250 sequential queries on every boot only run when explicitly enabled.
    # Run once manually with: python seed_pricing.py
    if os.getenv("RUN_SEED_ON_STARTUP", "").lower() not in ("1", "true", "yes"):
        print("Skipping pricing seed (set RUN_SEED_ON_STARTUP=1 to enable).")
        return

    # Ensure tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        # 1. Pricing Settings
        default_settings = [
            {"key": "currency", "value": "USD", "description": "Global currency symbol / code"},
            {"key": "default_pet_fee", "value": "30.00", "description": "Default pet fee per stay in USD"},
            {"key": "default_extra_person_fee", "value": "10.00", "description": "Default extra person fee per night in USD"},
            {"key": "multi_property_refundable_deposit", "value": "100.00", "description": "Multi-property refundable deposit in USD"},
        ]
        for s in default_settings:
            res = await session.execute(select(PricingSetting).filter(PricingSetting.key == s["key"]))
            if not res.scalar_one_or_none():
                session.add(PricingSetting(id=uuid.uuid4(), **s))

        # 2. Pricing Tiers
        default_tiers = [
            {"code": "INTERNATIONAL", "name": "International USD"},
            {"code": "LOCAL", "name": "Local USD"},
        ]
        tier_map = {}
        for t in default_tiers:
            res = await session.execute(select(PricingTier).filter(PricingTier.code == t["code"]))
            obj = res.scalar_one_or_none()
            if not obj:
                obj = PricingTier(id=uuid.uuid4(), **t)
                session.add(obj)
                await session.flush()
            tier_map[t["code"]] = obj

        # 3. Seasons
        default_seasons = [
            {"code": "LOW", "name": "Low Season", "priority": 100},
            {"code": "MID", "name": "Mid Season", "priority": 200},
            {"code": "HIGH", "name": "High Season", "priority": 300},
            {"code": "HOLIDAY", "name": "Holiday Season", "priority": 400},
        ]
        season_map = {}
        for s in default_seasons:
            res = await session.execute(select(Season).filter(Season.code == s["code"]))
            obj = res.scalar_one_or_none()
            if not obj:
                obj = Season(id=uuid.uuid4(), **s)
                session.add(obj)
                await session.flush()
            season_map[s["code"]] = obj

        # 4. Season Periods (2026 & 2027 default ranges)
        default_periods = [
            # LOW
            {"season_code": "LOW", "start_date": date(2026, 4, 16), "end_date": date(2026, 6, 14), "notes": "Low Season Part 1 (2026)"},
            {"season_code": "LOW", "start_date": date(2026, 8, 15), "end_date": date(2026, 12, 9), "notes": "Low Season Part 2 (2026)"},
            {"season_code": "LOW", "start_date": date(2027, 4, 16), "end_date": date(2027, 6, 14), "notes": "Low Season Part 1 (2027)"},
            {"season_code": "LOW", "start_date": date(2027, 8, 15), "end_date": date(2027, 12, 9), "notes": "Low Season Part 2 (2027)"},
            # MID
            {"season_code": "MID", "start_date": date(2026, 12, 10), "end_date": date(2026, 12, 24), "notes": "Mid Season (2026)"},
            {"season_code": "MID", "start_date": date(2027, 12, 10), "end_date": date(2027, 12, 24), "notes": "Mid Season (2027)"},
            # HIGH
            {"season_code": "HIGH", "start_date": date(2026, 1, 1), "end_date": date(2026, 3, 28), "notes": "High Season Jan-Easter (2026)"},
            {"season_code": "HIGH", "start_date": date(2026, 6, 15), "end_date": date(2026, 8, 14), "notes": "High Season Mid-Year (2026)"},
            {"season_code": "HIGH", "start_date": date(2027, 1, 1), "end_date": date(2027, 4, 10), "notes": "High Season Jan-Easter (2027)"},
            {"season_code": "HIGH", "start_date": date(2027, 6, 15), "end_date": date(2027, 8, 14), "notes": "High Season Mid-Year (2027)"},
            # HOLIDAY
            {"season_code": "HOLIDAY", "start_date": date(2026, 12, 25), "end_date": date(2027, 1, 1), "notes": "Year-End Holiday (2026)"},
            {"season_code": "HOLIDAY", "start_date": date(2026, 3, 29), "end_date": date(2026, 4, 5), "notes": "Easter Week 2026"},
            {"season_code": "HOLIDAY", "start_date": date(2027, 12, 25), "end_date": date(2028, 1, 1), "notes": "Year-End Holiday (2027)"},
            {"season_code": "HOLIDAY", "start_date": date(2027, 4, 11), "end_date": date(2027, 4, 18), "notes": "Easter Week 2027"},
        ]
        for p in default_periods:
            season_obj = season_map[p["season_code"]]
            res = await session.execute(
                select(SeasonPeriod).filter(
                    SeasonPeriod.season_id == season_obj.id,
                    SeasonPeriod.start_date == p["start_date"],
                    SeasonPeriod.end_date == p["end_date"]
                )
            )
            if not res.scalar_one_or_none():
                session.add(SeasonPeriod(
                    id=uuid.uuid4(),
                    season_id=season_obj.id,
                    start_date=p["start_date"],
                    end_date=p["end_date"],
                    notes=p["notes"]
                ))

        # 5. Properties & Rate Plans
        properties_def = [
            {
                "code": "B1", "name": "Bungalow 1", "display_order": 1, "standard_capacity": 6, "maximum_capacity": 7, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B1_2BR_LOFT", "name": "2BR + Loft", "standard_capacity": 6, "maximum_capacity": 7, "cleaning_fee": 75, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 10}
                ]
            },
            {
                "code": "B2", "name": "Bungalow 2 / Sloth House", "display_order": 2, "standard_capacity": 13, "maximum_capacity": 13, "pets_allowed": False,
                "rate_plans": [
                    {"code": "B2_DEFAULT", "name": "Default", "standard_capacity": 13, "maximum_capacity": 13, "cleaning_fee": 150, "refundable_deposit": 500, "pets_allowed": False, "extra_person_fee_per_night": 0}
                ]
            },
            {
                "code": "B3", "name": "Bungalow 3", "display_order": 3, "standard_capacity": 5, "maximum_capacity": 9, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B3_2BR", "name": "2BR", "standard_capacity": 5, "maximum_capacity": 5, "cleaning_fee": 75, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 0},
                    {"code": "B3_3BR", "name": "3BR", "standard_capacity": 7, "maximum_capacity": 9, "cleaning_fee": 85, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 10}
                ]
            },
            {
                "code": "B4", "name": "Bungalow 4", "display_order": 4, "standard_capacity": 4, "maximum_capacity": 4, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B4_DEFAULT", "name": "Default", "standard_capacity": 4, "maximum_capacity": 4, "cleaning_fee": 75, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 0}
                ]
            },
            {
                "code": "B5", "name": "Bungalow 5", "display_order": 5, "standard_capacity": 4, "maximum_capacity": 4, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B5_DEFAULT", "name": "Default", "standard_capacity": 4, "maximum_capacity": 4, "cleaning_fee": 75, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 0}
                ]
            },
            {
                "code": "B6", "name": "Bungalow 6", "display_order": 6, "standard_capacity": 4, "maximum_capacity": 5, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B6_DEFAULT", "name": "Default", "standard_capacity": 4, "maximum_capacity": 5, "cleaning_fee": 75, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 10}
                ]
            },
            {
                "code": "B7", "name": "Bungalow 7", "display_order": 7, "standard_capacity": 6, "maximum_capacity": 6, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B7_DEFAULT", "name": "Default", "standard_capacity": 6, "maximum_capacity": 6, "cleaning_fee": 75, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 0}
                ]
            },
            {
                "code": "B8", "name": "Bungalow 8", "display_order": 8, "standard_capacity": 5, "maximum_capacity": 5, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B8_DEFAULT", "name": "Default", "standard_capacity": 5, "maximum_capacity": 5, "cleaning_fee": 75, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 0}
                ]
            },
            {
                "code": "B9", "name": "Bungalow 9", "display_order": 9, "standard_capacity": 6, "maximum_capacity": 6, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B9_DEFAULT", "name": "Default", "standard_capacity": 6, "maximum_capacity": 6, "cleaning_fee": 75, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 0}
                ]
            },
            {
                "code": "B13", "name": "Bungalow 13", "display_order": 10, "standard_capacity": 6, "maximum_capacity": 6, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B13_DEFAULT", "name": "Default", "standard_capacity": 6, "maximum_capacity": 6, "cleaning_fee": 75, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 0}
                ]
            },
            {
                "code": "B14", "name": "Bungalow 14", "display_order": 11, "standard_capacity": 6, "maximum_capacity": 6, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B14_DEFAULT", "name": "Default", "standard_capacity": 6, "maximum_capacity": 6, "cleaning_fee": 75, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 0}
                ]
            },
            {
                "code": "B15", "name": "Bungalow 15", "display_order": 12, "standard_capacity": 6, "maximum_capacity": 6, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B15_DEFAULT", "name": "Default", "standard_capacity": 6, "maximum_capacity": 6, "cleaning_fee": 75, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 0}
                ]
            },
            {
                "code": "B16", "name": "Bungalow 16", "display_order": 13, "standard_capacity": 6, "maximum_capacity": 6, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B16_DEFAULT", "name": "Default", "standard_capacity": 6, "maximum_capacity": 6, "cleaning_fee": 75, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 0}
                ]
            },
            {
                "code": "B19", "name": "Bungalow 19", "display_order": 14, "standard_capacity": 6, "maximum_capacity": 6, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B19_DEFAULT", "name": "Default", "standard_capacity": 6, "maximum_capacity": 6, "cleaning_fee": 75, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 0}
                ]
            },
            {
                "code": "B20", "name": "Bungalow 20", "display_order": 15, "standard_capacity": 6, "maximum_capacity": 6, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B20_DEFAULT", "name": "Default", "standard_capacity": 6, "maximum_capacity": 6, "cleaning_fee": 75, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 0}
                ]
            },
            {
                "code": "B25", "name": "Bungalow 25", "display_order": 16, "standard_capacity": 7, "maximum_capacity": 7, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B25_3BR", "name": "3BR", "standard_capacity": 7, "maximum_capacity": 7, "cleaning_fee": 85, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 0}
                ]
            },
            {
                "code": "B27", "name": "Bungalow 27", "display_order": 17, "standard_capacity": 7, "maximum_capacity": 9, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B27_3BR", "name": "3BR", "standard_capacity": 7, "maximum_capacity": 9, "cleaning_fee": 85, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 10}
                ]
            },
            {
                "code": "B28", "name": "Bungalow 28", "display_order": 18, "standard_capacity": 6, "maximum_capacity": 6, "pets_allowed": True,
                "rate_plans": [
                    {"code": "B28_DEFAULT", "name": "Default", "standard_capacity": 6, "maximum_capacity": 6, "cleaning_fee": 75, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 0}
                ]
            },
            {
                "code": "APT17A", "name": "Apartment 17A", "display_order": 19, "standard_capacity": 2, "maximum_capacity": 3, "pets_allowed": True,
                "rate_plans": [
                    {"code": "APT17A_1BR", "name": "1BR", "standard_capacity": 2, "maximum_capacity": 3, "cleaning_fee": 20, "refundable_deposit": 0, "pets_allowed": True, "extra_person_fee_per_night": 10}
                ]
            },
            {
                "code": "CASA", "name": "Casa Barracuda", "display_order": 20, "standard_capacity": 8, "maximum_capacity": 9, "pets_allowed": True,
                "rate_plans": [
                    {"code": "CASA_3BR", "name": "3BR Private House", "standard_capacity": 8, "maximum_capacity": 9, "cleaning_fee": 100, "refundable_deposit": 200, "pets_allowed": True, "extra_person_fee_per_night": 10}
                ]
            },
        ]

        rate_plan_map = {}
        for p_def in properties_def:
            res = await session.execute(select(Property).filter(Property.code == p_def["code"]))
            prop = res.scalar_one_or_none()
            if not prop:
                prop = Property(
                    id=uuid.uuid4(),
                    code=p_def["code"],
                    name=p_def["name"],
                    standard_capacity=p_def["standard_capacity"],
                    maximum_capacity=p_def["maximum_capacity"],
                    pets_allowed=p_def["pets_allowed"],
                    display_order=p_def["display_order"]
                )
                session.add(prop)
                await session.flush()

            for rp_def in p_def["rate_plans"]:
                rp_res = await session.execute(
                    select(PropertyRatePlan).filter(
                        PropertyRatePlan.property_id == prop.id,
                        PropertyRatePlan.code == rp_def["code"]
                    )
                )
                rp = rp_res.scalar_one_or_none()
                if not rp:
                    rp = PropertyRatePlan(
                        id=uuid.uuid4(),
                        property_id=prop.id,
                        code=rp_def["code"],
                        name=rp_def["name"],
                        standard_capacity=rp_def["standard_capacity"],
                        maximum_capacity=rp_def["maximum_capacity"],
                        cleaning_fee=rp_def["cleaning_fee"],
                        extra_person_fee_per_night=rp_def["extra_person_fee_per_night"],
                        refundable_deposit=rp_def["refundable_deposit"],
                        pets_allowed=rp_def["pets_allowed"]
                    )
                    session.add(rp)
                    await session.flush()
                rate_plan_map[rp_def["code"]] = rp

        # 6. Seasonal Prices Matrix
        matrix_data = [
            # plan_code, intl_low, intl_mid, intl_high, intl_holiday, loc_low, loc_mid, loc_high, loc_holiday
            ("B1_2BR_LOFT", 240, 255, 295, 325, 200, 215, 250, 275),
            ("B2_DEFAULT", 625, 660, 750, 825, 550, 580, 660, 725),
            ("B3_2BR", 160, 190, 220, 280, 135, 160, 185, 235),
            ("B3_3BR", 180, 200, 240, 300, 150, 170, 200, 250),
            ("B4_DEFAULT", 150, 160, 200, 275, 125, 135, 165, 230),
            ("B5_DEFAULT", 115, 130, 165, 250, 95, 110, 140, 210),
            ("B6_DEFAULT", 125, 135, 170, 250, 105, 115, 155, 210),
            ("B7_DEFAULT", 115, 130, 165, 250, 100, 115, 145, 210),
            ("B8_DEFAULT", 115, 130, 165, 250, 100, 115, 145, 210),
            ("B9_DEFAULT", 115, 130, 165, 250, 100, 115, 145, 210),
            ("B13_DEFAULT", 125, 135, 175, 250, 105, 115, 150, 210),
            ("B14_DEFAULT", 115, 130, 165, 250, 100, 115, 145, 210),
            ("B15_DEFAULT", 115, 130, 165, 250, 100, 115, 145, 210),
            ("B16_DEFAULT", 100, 125, 150, 220, 85, 105, 130, 185),
            ("B19_DEFAULT", 125, 135, 180, 250, 105, 115, 155, 210),
            ("B20_DEFAULT", 125, 135, 180, 250, 105, 115, 155, 210),
            ("B25_3BR", 145, 155, 195, 290, 120, 130, 165, 245),
            ("B27_3BR", 135, 155, 190, 275, 115, 130, 170, 230),
            ("B28_DEFAULT", 100, 125, 150, 220, 85, 105, 130, 185),
            ("APT17A_1BR", 80, 80, 95, 120, 70, 70, 80, 100),
            ("CASA_3BR", 275, 295, 325, 375, 250, 255, 280, 315),
        ]

        intl_tier = tier_map["INTERNATIONAL"]
        local_tier = tier_map["LOCAL"]
        low_season = season_map["LOW"]
        mid_season = season_map["MID"]
        high_season = season_map["HIGH"]
        holiday_season = season_map["HOLIDAY"]

        for row in matrix_data:
            rp_code, i_low, i_mid, i_high, i_hol, l_low, l_mid, l_high, l_hol = row
            rp_obj = rate_plan_map[rp_code]

            prices_to_seed = [
                (rp_obj.id, low_season.id, intl_tier.id, i_low),
                (rp_obj.id, mid_season.id, intl_tier.id, i_mid),
                (rp_obj.id, high_season.id, intl_tier.id, i_high),
                (rp_obj.id, holiday_season.id, intl_tier.id, i_hol),
                (rp_obj.id, low_season.id, local_tier.id, l_low),
                (rp_obj.id, mid_season.id, local_tier.id, l_mid),
                (rp_obj.id, high_season.id, local_tier.id, l_high),
                (rp_obj.id, holiday_season.id, local_tier.id, l_hol),
            ]

            for rp_id, s_id, t_id, price in prices_to_seed:
                res = await session.execute(
                    select(PropertySeasonPrice).filter(
                        PropertySeasonPrice.property_rate_plan_id == rp_id,
                        PropertySeasonPrice.season_id == s_id,
                        PropertySeasonPrice.pricing_tier_id == t_id
                    )
                )
                if not res.scalar_one_or_none():
                    session.add(PropertySeasonPrice(
                        id=uuid.uuid4(),
                        property_rate_plan_id=rp_id,
                        season_id=s_id,
                        pricing_tier_id=t_id,
                        nightly_rate=price
                    ))

        # 7. Default Promotion
        promo_res = await session.execute(
            select(Promotion).filter(Promotion.name == "July–November 2026 Promotion")
        )
        promo = promo_res.scalar_one_or_none()
        if not promo:
            promo = Promotion(
                id=uuid.uuid4(),
                name="July–November 2026 Promotion",
                description="Flat promotional nightly prices for selected properties",
                start_date=date(2026, 7, 22),
                end_date=date(2026, 11, 20),
                enabled=True,
                waive_pet_fee=True,
                priority=1000
            )
            session.add(promo)
            await session.flush()

        promo_prices_def = [
            ("B5_DEFAULT", 100),
            ("B6_DEFAULT", 100),
            ("B7_DEFAULT", 100),
            ("B8_DEFAULT", 100),
            ("B9_DEFAULT", 100),
            ("B14_DEFAULT", 100),
            ("B15_DEFAULT", 100),
            ("B16_DEFAULT", 100),
            ("B19_DEFAULT", 100),
            ("B28_DEFAULT", 100),
            ("B4_DEFAULT", 120),
            ("B13_DEFAULT", 120),
            ("B20_DEFAULT", 120),
            ("B27_3BR", 120),
            ("B3_2BR", 130),
            ("B3_3BR", 140),
        ]

        for rp_code, rate in promo_prices_def:
            rp_obj = rate_plan_map[rp_code]
            ppp_res = await session.execute(
                select(PromotionPropertyPrice).filter(
                    PromotionPropertyPrice.promotion_id == promo.id,
                    PromotionPropertyPrice.property_rate_plan_id == rp_obj.id
                )
            )
            if not ppp_res.scalar_one_or_none():
                session.add(PromotionPropertyPrice(
                    id=uuid.uuid4(),
                    promotion_id=promo.id,
                    property_rate_plan_id=rp_obj.id,
                    nightly_rate=rate
                ))

        await session.commit()
        print("Pricing data seeding completed successfully.")

if __name__ == "__main__":
    asyncio.run(seed_pricing_data())

"""Telegram bot integration using Aiogram 3.x."""
import logging
from aiogram import Bot
from typing import Optional
from app.config import settings
from app.services import get_vehicle_unit
from app.models import SpeedingEvent

logger = logging.getLogger(__name__)

# Global bot instance (will be initialized in main)
bot: Optional[Bot] = None


async def init_bot() -> Bot:
    """Initialize the Telegram bot."""
    global bot
    if bot is None:
        bot = Bot(token=settings.telegram_bot_token)
    return bot


async def close_bot() -> None:
    """Close the Telegram bot connection."""
    global bot
    if bot is not None:
        await bot.session.close()
        bot = None


def kph_to_mph(kph: float) -> float:
    """Convert kilometers per hour to miles per hour."""
    return kph * 0.621371


async def process_alert(event_data: dict) -> None:
    """
    Process a speeding alert event and send Telegram notification if threshold met.
    
    Args:
        event_data: The speeding event data dictionary
    """
    try:
        logger.info(f"Processing alert event: {event_data.get('id')}")
        
        # Initialize bot if not already done
        if bot is None:
            await init_bot()
        
        # Parse the event
        event = SpeedingEvent.model_validate(event_data)
        
        # Convert speeds to MPH
        limit_mph = kph_to_mph(event.max_posted_speed_limit_in_kph)
        speed_mph = kph_to_mph(event.max_vehicle_speed)
        over_mph = kph_to_mph(event.max_over_speed_in_kph)
        
        # Safety filter: Only send if over_speed_mph >= 5 (prevents spam for minor fluctuations)
        if over_mph < 5:
            logger.info(f"Event {event.id} filtered: over_speed_mph={over_mph:.1f} < 5 mph threshold")
            return
        
        # Get vehicle unit number
        unit_number = await get_vehicle_unit(event.vehicle_id)

        # Fallback: if unit is unknown, include raw vehicle_id
        if unit_number == "Unit Unknown":
            unit_display = f"Unknown (ID: {event.vehicle_id})"
        else:
            unit_display = unit_number
        
        # Format the alert message using HTML
        message = (
            "🚨 <b>SPEEDING ALERT</b>\n"
            f"<b>Unit:</b> {unit_display}\n"
            f"<b>Route Limit:</b> {limit_mph:.1f} mph\n"
            f"<b>Current Speed:</b> {speed_mph:.1f} mph\n"
            f"<b>Violation:</b> +{over_mph:.1f} mph"
        )
        
        # Send the message
        await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=message,
            parse_mode="HTML"
        )
        
        logger.info(f"Telegram alert sent for event {event.id}, unit {unit_number}")
        
    except Exception as e:
        logger.error(f"Error processing alert: {e}", exc_info=True)
        raise

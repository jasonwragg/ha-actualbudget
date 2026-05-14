"""The actualbudget integration."""

from __future__ import annotations
from urllib.parse import urlparse
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .actions import register_actions
from .actualbudget import ActualBudget, _normalize_cert
from .const import (
    CONFIG_CERT,
    CONFIG_ENCRYPT_PASSWORD,
    CONFIG_ENDPOINT,
    CONFIG_FILE,
    CONFIG_PASSWORD,
    CONFIG_SKIP_VALIDATE_CERT,
    DOMAIN,
)
from .coordinator import ActualBudgetCoordinator

__version__ = "3.0.2"
_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor"]


async def async_setup(hass: HomeAssistant, config: ConfigType):
    """Start configuring the API."""
    _LOGGER.debug("Start 'async_setup'...")

    hass.data.setdefault(DOMAIN, {})

    register_actions(hass)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the component from a config entry."""
    config = entry.data
    cert = config.get(CONFIG_CERT)
    if config.get(CONFIG_SKIP_VALIDATE_CERT, False):
        cert = False
    else:
        cert = _normalize_cert(cert)

    api = ActualBudget(
        hass,
        config[CONFIG_ENDPOINT],
        config[CONFIG_PASSWORD],
        config[CONFIG_FILE],
        cert,
        config.get(CONFIG_ENCRYPT_PASSWORD),
    )

    coordinator = ActualBudgetCoordinator(hass, api)

    # Compute a stable source id used for entity unique_ids.
    endpoint = config[CONFIG_ENDPOINT]
    parsed = urlparse(endpoint)
    unique_source_id = f"{parsed.hostname}_{parsed.port}_{config[CONFIG_FILE]}"

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "unique_source_id": unique_source_id,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _async_initial_refresh() -> None:
        """Refresh Actual Budget data without blocking Home Assistant startup."""
        try:
            await coordinator.async_request_refresh()
        except Exception:
            _LOGGER.exception("Initial Actual Budget refresh failed")

    refresh_task = hass.async_create_task(_async_initial_refresh())
    entry.async_on_unload(refresh_task.cancel)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if entry_data:
            api: ActualBudget = entry_data["api"]
            await api.async_close()
    return unloaded

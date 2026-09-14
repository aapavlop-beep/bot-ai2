"""Use both certifi and OS trusted roots without disabling TLS verification."""
from aiogram.client.session.aiohttp import AiohttpSession


def telegram_session() -> AiohttpSession:
    session = AiohttpSession(timeout=30)
    # aiogram 3 initializes a certifi-only context. Windows installations may
    # additionally need a locally managed trusted CA from the operating system.
    # There is no public SSL-context parameter in AiohttpSession 3.x.
    context = session._connector_init["ssl"]
    context.load_default_certs()
    return session

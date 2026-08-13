import os

import reflex as rx
from reflex_base.plugins.sitemap import SitemapPlugin


PUBLIC_APP_URL = (
    os.getenv("QCC_PUBLIC_APP_URL", "").strip()
    or os.getenv("RENDER_EXTERNAL_URL", "").strip()
    or "http://localhost:3000"
).rstrip("/")


config = rx.Config(
    app_name="qcc_reflex_pilot",
    api_url=PUBLIC_APP_URL,
    deploy_url=PUBLIC_APP_URL,
    cors_allowed_origins=[PUBLIC_APP_URL],
    disable_plugins=[SitemapPlugin],
)

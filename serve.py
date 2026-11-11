import os

from waitress import serve

from app import app

serve(
    app,
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8080")),
    threads=6,
    trusted_proxy=os.environ.get("TRUSTED_PROXY", "*"),
    trusted_proxy_count=1,
    trusted_proxy_headers={"x-forwarded-for"},
)

from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "Vault City AI"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    
    # Database (PostgreSQL + pgvector)
    DATABASE_URL: str = "postgresql+asyncpg://vault_user:vault_secure_password@db:5432/vault_db"
    VECTOR_DIMENSION: int = 384

    # AWS SAM Local Lambda Pipeline, ClamAV & MinIO/S3 (Quarantine -> Secured Vault)
    SAM_API_URL: str = "http://host.docker.internal:3001"
    SAM_LAMBDA_ENDPOINT_URL: str = "http://host.docker.internal:3002"
    USE_SAM_PIPELINE: bool = True
    MINIO_ENDPOINT_URL: str = "http://minio:9000"
    MINIO_ACCESS_KEY: str = "vault_minio_admin"
    MINIO_SECRET_KEY: str = "vault_minio_secret"
    MINIO_REGION: str = "us-east-1"
    MINIO_BUCKET: str = "vault-uploads"
    S3_QUARANTINE_BUCKET: str = "vault-quarantine"
    S3_SECURED_BUCKET: str = "vault-secured"
    CLAMAV_HOST: str = "clamav"
    CLAMAV_PORT: int = 3310
    MAX_UPLOAD_SIZE_BYTES: int = 25 * 1024 * 1024  # 25 MB
    USER_STORAGE_QUOTA_BYTES: int = 100 * 1024 * 1024  # 100 MB free storage hard limit

    # Host-mounted File Pipeline Logs (persisted in ./logs on host + docker logs stdout)
    FILE_LOG_DIR: str = "/app/logs"

    # OpenRouter Unified Gateway (Jev Decision Router + Qwen 2.5 Text/Reasoning)
    OPENROUTER_API_KEY: Optional[str] = None
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_DEFAULT_MODEL: str = "qwen/qwen-2.5-72b-instruct"
    OPENROUTER_FAST_MODEL: str = "qwen/qwen-2.5-7b-instruct"
    OPENROUTER_JEV_MODEL: str = "typesafe/jev"
    OPENROUTER_FREE_MODELS: str = "meta-llama/llama-3.3-70b-instruct:free,google/gemini-2.0-flash-exp:free,qwen/qwen-2.5-coder-32b-instruct:free"
    USE_FREE_MODELS_FIRST: bool = True

    # Cloudflare Workers AI (Zero-Cost Daily Free Neurons for Image-Only Vision)
    CLOUDFLARE_ACCOUNT_ID: Optional[str] = None
    CLOUDFLARE_API_TOKEN: Optional[str] = None
    CLOUDFLARE_VISION_MODEL: str = "@cf/meta/llama-3.2-11b-vision-instruct"

    # Vision / Image Summary Engine (Anthropic Claude Haiku / Sonnet -> Gemini Vision -> Ollama VLM -> Built-in Tesseract OCR + Analyzer)
    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_MODEL: str = "claude-haiku-4-5-20251001"
    ANTHROPIC_SONNET_MODEL: str = "claude-sonnet-4-5-20250929"
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_VISION_MODEL: str = "gemini-2.5-flash"
    OLLAMA_URL: Optional[str] = None
    OLLAMA_VISION_MODEL: str = "llava"
    
    # Redis Cache, Token Map & Distributed Rate Limiting (pylimitx)
    REDIS_URL: str = "redis://redis:6379/0"
    RATE_LIMIT_FAIL_OPEN: bool = True

    # Security & JWT (5-min Access, 7-day Refresh)
    SECRET_KEY: str = "default_insecure_secret_key_change_in_production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 5  # 5 minutes
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days (10080 minutes)

    # HttpOnly Cookie Settings
    COOKIE_ACCESS_NAME: str = "vault_access_token"
    COOKIE_REFRESH_NAME: str = "vault_refresh_token"
    COOKIE_SECURE: bool = False  # Set to True in production (HTTPS)
    COOKIE_SAMESITE: str = "lax"  # 'lax', 'strict', or 'none'
    COOKIE_DOMAIN: Optional[str] = None

    # Google Social Auth
    GOOGLE_CLIENT_ID: Optional[str] = "105470440264-7ep9hin2tddk14jba8agmbddbcl6j12r.apps.googleusercontent.com"

    # Cloudflare Turnstile
    TURNSTILE_ENABLED: bool = True
    TURNSTILE_SITE_KEY: str = "0x4AAAAAAGDGaHDA7lQB_a7O"
    TURNSTILE_SECRET_KEY: str = "0x4AAAAAAGDGaBY2ZuBeRJzSHM3qBN27njY"
    TURNSTILE_VERIFY_URL: str = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

    # SMTP Email Delivery (Gmail STARTTLS default: smtp.gmail.com:587)
    SMTP_ENABLED: bool = True
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM_EMAIL: Optional[str] = None
    SMTP_FROM_NAME: str = "Vault City AI"
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False
    SMTP_VERIFY_SSL: bool = True  # Keep True in production; set False only for local dev behind antivirus TLS proxies
    SMTP_CA_BUNDLE: Optional[str] = None  # Optional path to custom PEM CA certificate bundle
    FRONTEND_URL: str = "http://localhost:3000"

    # Email Verification OTP Settings
    OTP_EXPIRE_MINUTES: int = 10
    OTP_MAX_ATTEMPTS: int = 5
    OTP_RESEND_COOLDOWN_SECONDS: int = 60
    OTP_MAX_RESENDS: int = 5
    OTP_RESEND_WINDOW_SECONDS: int = 3600  # 1 hour window for resend cap
    SMTP_MAX_RETRIES: int = 2
    
    # CORS
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:80",
        "http://127.0.0.1:3000",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="allow",
    )


settings = Settings()

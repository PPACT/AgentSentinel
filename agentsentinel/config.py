"""运行时配置：从环境变量与 .env 加载，密钥不回显。

环境变量统一使用 ``SENTINEL_`` 前缀，避免与宿主环境中的同名变量冲突。
"""

from pathlib import Path

from pydantic import EmailStr, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """项目配置。环境变量优先于 .env，.env 优先于代码内缺省值。"""

    model_config = SettingsConfigDict(
        env_prefix="SENTINEL_",
        env_file=Path(".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: SecretStr | None = None
    git_user_name: str = ""
    git_user_email: EmailStr | None = None
    git_remote_url: str = ""

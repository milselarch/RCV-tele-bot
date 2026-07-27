import configparser
import os.path
import shutil

from typing import TypeVar, Type, Self
from pydantic import BaseModel, Field
from pydantic_yaml import parse_yaml_raw_as, to_yaml_str

from helpers.constants import DEFAULT_CONFIG_PATH, __VERSION__

T = TypeVar('T', bound='LoadableBaseModel')


class LoadableBaseModel(BaseModel):
    @classmethod
    def load_from(cls: Type[T], config_path: str = DEFAULT_CONFIG_PATH):
        return cls.load_for(cls, config_path)

    @staticmethod
    def load_for(cls: Type[T], config_path: str = DEFAULT_CONFIG_PATH) -> T:
        with open(config_path, 'r') as config_file_obj:
            raw_data = config_file_obj.read()
            if raw_data.strip() == "":
                return cls()

            yaml_config = parse_yaml_raw_as(cls, raw_data)
            return yaml_config


class SettingsConfig(BaseModel):
    production: bool = True

class DatabaseConfig(BaseModel):
    name: str = "ranked_choice_voting"
    user: str = "rcv_user"
    password: str = ""
    host: str = "localhost"


class TelegramConfig(BaseModel):
    bot_token: str = ""
    webhook_url: str = ""
    sudo_id: int = -1


class WebappConfig(BaseModel):
    cors_origins: list[str] = Field(default_factory=list)


class BotConfig(LoadableBaseModel):
    version: str = __VERSION__
    settings: SettingsConfig = Field(default_factory=SettingsConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    webapp: WebappConfig = Field(default_factory=WebappConfig)

    @property
    def is_production(self) -> bool:
        return bool(self.settings.production)

    @classmethod
    def load_from(
        cls: Type[Self], config_path: str = DEFAULT_CONFIG_PATH
    ) -> Self:
        if not os.path.exists(config_path):
            print(f"{config_path} not found, generating default")
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            default_config = cls()

            with open(config_path, 'w') as config_file:
                yaml_str = to_yaml_str(default_config)
                config_file.write(yaml_str)

        config = cls.load_for(cls, config_path)
        return config


class ConfigLoader(object):
    _config_map: dict[str, BotConfig] = {}

    @classmethod
    def load_config(
        cls, config_path: str = DEFAULT_CONFIG_PATH
    ) -> BotConfig:
        if config_path in cls._config_map:
            return cls._config_map[config_path]

        config = BotConfig.load_from(config_path)
        cls._config_map[config_path] = config
        return config

import yaml

CONFIG_PATH = 'config.yml'
with open(CONFIG_PATH, 'r') as config_file_obj:
    YAML_CONFIG = yaml.safe_load(config_file_obj)

TELE_CONFIG = YAML_CONFIG['telegram']
TELEGRAM_BOT_TOKEN = TELE_CONFIG['bot_token']
WEBHOOK_URL = TELE_CONFIG['webhook_url']
SUDO_TELE_ID: int = TELE_CONFIG['sudo_id']
SETTINGS = YAML_CONFIG['settings']
PRODUCTION_MODE = bool(SETTINGS['production'])
CORS_ORIGINS = YAML_CONFIG['webapp']['cors_origins']

# print('CORS_ORIGINS =', CORS_ORIGINS)
# print('PRODUCTION_MODE =', PRODUCTION_MODE)

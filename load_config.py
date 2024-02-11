import yaml

CONFIG_PATH = 'config.yml'
with open(CONFIG_PATH, 'r') as config_file_obj:
    YAML_CONFIG = yaml.safe_load(config_file_obj)

TELE_CONFIG = YAML_CONFIG['telegram']
TELEGRAM_BOT_TOKEN = TELE_CONFIG['bot_token']
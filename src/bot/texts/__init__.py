import json

LANGUAGES = ["EN", "RU", "CZ", "UA"]


button_texts = {}
message_texts = {}
commands_description = {}

for lang in LANGUAGES:
    with open(f"src/bot/texts/{lang}/buttons.json", "r") as file:
        button_texts[lang] = json.load(file)

    with open(f"src/bot/texts/{lang}/messages.json", "r") as file:
        message_texts[lang] = json.load(file)

    with open(f"src/bot/texts/{lang}/commands.json", "r") as file:
        commands_description[lang] = json.load(file)

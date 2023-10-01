import json

LANGUAGES = ["EN"]


button_texts = {}
message_texts = {}

for lang in LANGUAGES:
    with open(f"src/bot/texts/{lang}/buttons.json", "r") as file:
        button_texts[lang] = json.load(file)

    with open(f"src/bot/texts/{lang}/messages.json", "r") as file:
        message_texts[lang] = json.load(file)

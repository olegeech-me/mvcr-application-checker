import json

LANGUAGES = ["EN"]

with open("src/bot/texts/EN/buttons.json", "r") as file:
    button_texts = json.load(file)

with open("src/bot/texts/EN/messages.json", "r") as file:
    message_texts = json.load(file)

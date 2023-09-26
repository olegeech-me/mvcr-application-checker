import json


with open("src/bot/texts/buttons.json", "r") as file:
    button_texts = json.load(file)

with open("src/bot/texts/messages.json", "r") as file:
    message_texts = json.load(file)

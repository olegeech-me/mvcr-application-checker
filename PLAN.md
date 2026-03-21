# Subscribe Flow Rework

## Goal

Split the subscribe dialog into two paths: user first picks OAM or ZOV via buttons, then gets source-specific number input instructions with source-specific parsing.

## Part 1: Confirmed text

All i18n text is in this section. Code changes reference keys defined here.

### `dialog_source` (replaces `dialog_app_number`)

**EN:**

```
Hey there! 👋 Let's get started.

What kind of application would you like to track?

🏠 Long-term residence (OAM) — residence permit applications filed in the Czech Republic

✈️ Long-term visa (ŽOV) — long-term visa applications filed at Czech embassies abroad
```

**RU:**

```
Привет! 👋 Давайте начнем.

Какое заявление вы хотите отслеживать?

🏠 Долгосрочное пребывание (OAM) — заявления на ВНЖ, поданные на территории Чехии

✈️ Долгосрочная виза (ŽOV) — заявления на долгосрочную визу, поданные в посольствах Чехии за рубежом
```

**UA:**

```
Привіт! 👋 Давайте почнемо.

Яке клопотання Ви хочете відстежувати?

🏠 Довготривале перебування (OAM) — клопотання на ПМП, подані на території Чехії

✈️ Довготривала віза (ŽOV) — клопотання на довготривалу візу, подані в посольствах Чехії за кордоном
```

**CZ:**

```
Ahoj! 👋 Začněme.

Jakou žádost chcete sledovat?

🏠 Dlouhodobý pobyt (OAM) — žádosti o pobytové oprávnění podané v ČR

✈️ Dlouhodobé vízum (ŽOV) — žádosti o dlouhodobé vízum podané na zastupitelských úřadech ČR v zahraničí
```

### `dialog_app_number_oam`

**EN:**

```
Please enter your application number:

Enter all digits after "OAM-" but before "/ XX-2023", e.g. 12345-6 from OAM-12345-6/TP-2023. You can also paste the full number like 12345/TP-2023.

💡 The suffix (the 6 in OAM-12345-6) is optional and isn't used by the Czech MoI verification site.
```

**RU:**

```
Введите номер вашего заявления:

Все цифры после "OAM-", но до "/ XX-2023", например 12345-6 из OAM-12345-6/TP-2023. Также можно вставить полный номер, например 12345/TP-2023.

💡 Суффикс (т.е. 6 в OAM-12345-6) опционален и не учитывается сайтом проверки МВД Чехии.
```

**UA:**

```
Введіть номер Вашого клопотання:

Цифри після коду "OAM-", перед скісною рискою "/ XX-2023", наприклад 12345-6 з OAM-12345-6/TP-2023. Також можна вставити повний номер, наприклад 12345/TP-2023.

💡 Суфікс (тобто 6 у OAM-12345-6) є необов'язковим і не враховується на сайті перевірки МВЧР.
```

**CZ:**

```
Zadejte prosím číslo vaší žádosti:

Zadejte všechna čísla po "OAM-", ale před "/ XX-2023", např. 12345-6 z OAM-12345-6/TP-2023. Můžete také zadat celé číslo, např. 12345/TP-2023.

💡 Přípona (tj. 6 v OAM-12345-6) je volitelná a na ověřovacím webu MVČR není brána v úvahu.
```

### `dialog_app_number_zov`

**EN:**

```
Please enter your visa application number:

The full number from your confirmation, e.g. ISTA202504220001. It starts with 4 letters (embassy code) followed by digits.
```

**RU:**

```
Введите номер вашего визового заявления:

Полный номер из подтверждения, например ISTA202504220001. Он начинается с 4 букв (код посольства), затем цифры.
```

**UA:**

```
Введіть номер Вашого візового клопотання:

Повний номер з підтвердження, наприклад ISTA202504220001. Він починається з 4 літер (код посольства), далі цифри.
```

**CZ:**

```
Zadejte prosím číslo vaší vízové žádosti:

Celé číslo z potvrzení, např. ISTA202504220001. Začíná 4 písmeny (kód zastupitelského úřadu), následují číslice.
```

### `error_invalid_number_zov`

**EN:**

```
Oops! 🙈 That doesn't look like a valid visa application number. It should be 4 letters followed by 9-12 digits, e.g. ISTA202504220001.
```

**RU:**

```
Ой! 🙈 Это не похоже на правильный номер визового заявления. Формат: 4 буквы и 9-12 цифр, например ISTA202504220001.
```

**UA:**

```
Овва! 🙈 Це не схоже на правильний номер візового клопотання. Формат: 4 літери та 9-12 цифр, наприклад ISTA202504220001.
```

**CZ:**

```
Oj! 🙈 To nevypadá jako správné číslo vízové žádosti. Správný formát: 4 písmena a 9-12 číslic, např. ISTA202504220001.
```

### Button labels

`**source_oam`:** EN: `🏠 Long-term residence (OAM)` | RU: `🏠 Долгосрочное пребывание (OAM)` | UA: `🏠 Довготривале перебування (OAM)` | CZ: `🏠 Dlouhodobý pobyt (OAM)`

`**source_zov`:** EN: `✈️ Long-term visa (ŽOV)` | RU: `✈️ Долгосрочная виза (ŽOV)` | UA: `✈️ Довготривала віза (ŽOV)` | CZ: `✈️ Dlouhodobé vízum (ŽOV)`

---

## Part 2: Code changes

### State machine

Current: `START(0) → NUMBER(1) → TYPE(2) → YEAR(3) → VALIDATE(4)`

New: `START(0) → SOURCE(1) → NUMBER(2) → TYPE(3) → YEAR(4) → VALIDATE(5)`

```
                         ┌─ OAM partial ──→ TYPE → YEAR → VALIDATE
START → SOURCE → NUMBER ─┤─ OAM full ────────────────────→ VALIDATE
                         └─ ZOV ──────────────────────────→ VALIDATE

/subscribe <args>: auto-detect OAM/ZOV, skip straight to VALIDATE (unchanged)
```

### `src/bot/handlers.py`

#### 1. State constants (line 24)

```python
# before
START, NUMBER, TYPE, YEAR, VALIDATE = range(5)

# after
START, SOURCE, NUMBER, TYPE, YEAR, VALIDATE = range(6)
```

#### 2. New helper: `_show_source_selection()`

Extracted because both `subscribe_button()` and `subscribe_command()` need to show the same source selection screen.

```python
async def _show_source_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    """Show OAM/ZOV source selection buttons."""
    lang = await _get_user_language(update, context)
    keyboard = [
        [InlineKeyboardButton(button_texts[lang]["source_oam"], callback_data="application_source_oam")],
        [InlineKeyboardButton(button_texts[lang]["source_zov"], callback_data="application_source_zov")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if edit:
        await update.callback_query.edit_message_text(
            message_texts[lang]["dialog_source"], reply_markup=reply_markup
        )
    else:
        await get_effective_message(update).reply_text(
            message_texts[lang]["dialog_source"], reply_markup=reply_markup
        )
    return SOURCE
```

#### 3. New handler: `application_dialog_source()`

```python
async def application_dialog_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles OAM/ZOV source selection callback."""
    query = update.callback_query
    lang = await _get_user_language(update, context)

    if await _is_button_click_abused(update, context):
        return
    await query.answer()

    source = query.data.split("application_source_")[-1]  # "oam" or "zov"
    context.user_data["application_source"] = source

    if source == "oam":
        msg_key = "dialog_app_number_oam"
    else:
        msg_key = "dialog_app_number_zov"

    await query.edit_message_text(message_texts[lang][msg_key])
    return NUMBER
```

#### 4. Modify `subscribe_button()` (line 517-530)

```python
# before (line 528-530)
            await query.edit_message_text(message_texts[lang]["dialog_app_number"])
            return NUMBER

# after
            return await _show_source_selection(update, context, edit=True)
```

#### 5. Modify `subscribe_command()` (line 468-513)

Only the no-args path changes (line 511-513):

```python
# before (line 511)
        await update.message.reply_text(message_texts[lang]["dialog_app_number"])
    return NUMBER

# after
        return await _show_source_selection(update, context, edit=False)
    return SOURCE
```

The with-args path (lines 491-510) stays unchanged — it auto-detects OAM/ZOV and skips to VALIDATE.

#### 6. Parser functions: NO CHANGES

`_parse_zov_number()`, `_parse_application_number_full()`, and `_parse_application_number()` remain untouched.
Their regexes are already type-specific (OAM parsers can't match ZOV format and vice versa).
The separation happens at the caller level below — we just stop calling the wrong parser for the wrong source.

#### 7. Modify `application_dialog_number()` (line 279-330)

Replace the blind try-all-three cascade with source-aware branching:

```python
async def application_dialog_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = get_effective_message(update)
    lang = await _get_user_language(update, context)
    number_str = message.text.strip()
    source = context.user_data.get("application_source", "oam")

    if source == "zov":
        # --- ZOV path: only try ZOV parser ---
        zov_number = _parse_zov_number(number_str)
        if zov_number:
            context.user_data["application_number"] = zov_number
            context.user_data["application_suffix"] = "0"
            context.user_data["application_type"] = "ZOV"
            context.user_data["application_year"] = 0
            await _show_app_number_final_confirmation(update, context)
            return VALIDATE
        # ZOV-specific error
        await message.reply_text(message_texts[lang]["error_invalid_number_zov"])
        return None

    # --- OAM path: try full, then partial ---
    number_parsed = _parse_application_number_full(number_str)
    if number_parsed:
        context.user_data["application_number"] = number_parsed[0]
        context.user_data["application_suffix"] = number_parsed[1]
        context.user_data["application_type"] = number_parsed[2]
        context.user_data["application_year"] = number_parsed[3]
        await _show_app_number_final_confirmation(update, context)
        return VALIDATE

    number_parsed = _parse_application_number(number_str)
    if not number_parsed:
        await message.reply_text(message_texts[lang]["error_invalid_number"])
        return None

    context.user_data["application_number"] = number_parsed[0]
    context.user_data["application_suffix"] = number_parsed[1]
    # show type keyboard (unchanged from current code)
    keyboard = [
        [InlineKeyboardButton(t, callback_data=f"application_dialog_type_{t}") for t in POPULAR_ALLOWED_TYPES],
        [InlineKeyboardButton(t, callback_data=f"application_dialog_type_{t}") for t in sorted(set(ALLOWED_TYPES) - set(POPULAR_ALLOWED_TYPES))],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message_texts[lang]["dialog_type"], reply_markup=reply_markup)
    return TYPE
```

#### 8. Modify `clean_sub_context()` (line 192-200)

```python
# before
    keys_to_delete = [
        "application_number", "application_suffix",
        "application_type", "application_year",
    ]

# after
    keys_to_delete = [
        "application_number", "application_suffix",
        "application_type", "application_year",
        "application_source",
    ]
```

### `src/bot/__main__.py`

#### 1. Imports (lines 12-33)

Add `SOURCE` and `application_dialog_source` to imports:

```python
from bot.handlers import (
    application_dialog_number,
    application_dialog_source,   # NEW
    application_dialog_year,
    application_dialog_type,
    application_dialog_validate,
    START,
    SOURCE,    # NEW
    NUMBER,
    TYPE,
    YEAR,
    VALIDATE,
    ...
)
```

#### 2. ConversationHandler states (lines 104-112)

Add SOURCE state between START and NUMBER:

```python
states={
    START: [
        CallbackQueryHandler(subscribe_button, pattern="subscribe"),
        CallbackQueryHandler(set_language_startup, pattern="set_lang_*"),
    ],
    SOURCE: [CallbackQueryHandler(application_dialog_source, pattern="application_source_*")],  # NEW
    NUMBER: [MessageHandler(filters.TEXT, application_dialog_number)],
    TYPE: [CallbackQueryHandler(application_dialog_type, pattern="application_dialog_type_*")],
    YEAR: [CallbackQueryHandler(application_dialog_year, pattern="application_dialog_year_*")],
    VALIDATE: [CallbackQueryHandler(application_dialog_validate, pattern="proceed_subscribe|cancel_subscribe")],
},
```

### `src/bot/texts/{EN,RU,UA,CZ}/messages.json`

In each language file:

1. **Rename key** `dialog_app_number` → `dialog_source`, replace value with Part 1 text
2. **Add key** `dialog_app_number_oam` with Part 1 OAM text
3. **Add key** `dialog_app_number_zov` with Part 1 ZOV text
4. **Add key** `error_invalid_number_zov` with Part 1 error text

Existing `error_invalid_number` stays — it's used for OAM errors.

### `src/bot/texts/{EN,RU,UA,CZ}/buttons.json`

In each language file, add two keys:

1. `source_oam` — see Part 1 button labels
2. `source_zov` — see Part 1 button labels

---

## Part 3: Test changes

File: `src/tests/test_handlers.py`

### Update existing tests

1. `**test_clean_sub_context_removes_oam_keys` (line 260):**
  Add `"application_source": "oam"` to initial `context.user_data`, assert it's removed after `clean_sub_context()`.
2. `**test_subscribe_command_no_args_returns_number` (line 591):**
  Now returns `SOURCE` instead of `NUMBER`. Update assertion.
3. `**test_application_dialog_number_full_oam` (line 481):**
  Add `context.user_data = {"application_source": "oam"}` (was empty `{}`).
4. `**test_application_dialog_number_partial_oam` (line 501):**
  Add `context.user_data = {"application_source": "oam"}`.
5. `**test_application_dialog_number_invalid_input` (line 518):**
  Add `context.user_data = {"application_source": "oam"}`.
6. `**test_application_dialog_number_zov` (line 534):**
  Add `context.user_data = {"application_source": "zov"}`.

### New tests

1. `**test_application_dialog_source_oam`:**
  Mock callback with `query.data = "application_source_oam"`. Assert returns `NUMBER`, assert `context.user_data["application_source"] == "oam"`, assert `edit_message_text` called with OAM prompt.
2. `**test_application_dialog_source_zov`:**
  Same as above but for ZOV. Assert `context.user_data["application_source"] == "zov"`, assert message contains ZOV prompt.
3. `**test_application_dialog_number_zov_rejects_oam_input`:**
  Set `context.user_data = {"application_source": "zov"}`, input `"12345/TP-2023"`. Assert returns `None`, assert `reply_text` called with `error_invalid_number_zov` message.
4. `**test_application_dialog_number_oam_rejects_zov_input`:**
  Set `context.user_data = {"application_source": "oam"}`, input `"ISTA202504220001"`. Assert returns `None`, assert `reply_text` called with `error_invalid_number` message (existing OAM error — ZOV format doesn't match either OAM parser).

---

## Part 4: Validation

Run the full test suite and verify all tests pass:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -vvv src/tests/
```

Fix any failures before considering the task done.


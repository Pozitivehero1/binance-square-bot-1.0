# Mistral Content Engine v3

Изменения:

- Mistral теперь используется как полноценный редактор Binance Square, а не только полировка.
- Добавлена подготовка к генерации нескольких вариантов текста через AI_VARIANTS.
- Усилен промпт против AI-стиля.
- Сохранены строгие проверки тикера, уровней входа, TP и стопа.

Настройки .env:

ENABLE_AI_POLISH=1
AI_VARIANTS=3
MISTRAL_MODEL=mistral-small-latest

Следующий этап: добавить AI-оценщик, который выбирает лучший вариант по hook/comment score.

# Данные русской нормализации из NVIDIA NeMo text-processing

Источник: https://github.com/NVIDIA/NeMo-text-processing (коммит `ddadfb2a38d2`),
папка `nemo_text_processing/text_normalization/ru/data`.
Copyright (c) 2021, NVIDIA CORPORATION. Лицензия — Apache License 2.0 (файл `LICENSE` рядом).

| Файл здесь | Файл в NeMo | Что внутри |
|---|---|---|
| `abbreviations.tsv` | `whitelist.tsv` | сокращения и их формы («т.е.» → «то есть», «г.» → «год/года/году…») |
| `measurements.tsv` | `measurements.tsv` | единицы измерения во всех формах |
| `currency_singular.tsv`, `currency_plural.tsv` | `currency/…` | валюты |
| `months_abbr.tsv` | `months/abbr_to_name.tsv` | сокращённые месяцы |

Файлы скопированы без изменений (переименованы). BookReader читает из них списки сокращений и единиц
(ru_numbers.py); выбор нужной формы по числу и падежу — собственный код BookReader.

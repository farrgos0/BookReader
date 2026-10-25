# Сторонние компоненты

Лицензия MIT-0 (файл `LICENSE`) относится только к коду BookReader. Сторонние компоненты
распространяются на своих условиях — они не меняются от того, что BookReader свободен.

| Компонент | Где используется | Лицензия | Что важно |
|---|---|---|---|
| **Модели Silero TTS (v5_ru)** | сервер; в архиве релиза — `packages/silero` (без изменений) | **CC BY-NC-SA 4.0** (© Silero Team) | **только некоммерческое использование, с указанием авторства Silero** (snakers4/silero-models). Продавать озвучку или сервис на её основе нельзя без лицензии от Silero |
| PyTorch | сервер | BSD-3-Clause | — |
| NumPy | сервер | BSD-3-Clause | — |
| SciPy | сервер (нужен Silero) | BSD-3-Clause | — |
| OmegaConf | сервер (нужен Silero) | BSD-3-Clause | — |
| FFmpeg (через пакет imageio-ffmpeg) | сервер, сжатие в Opus | LGPL/GPL (зависит от сборки) | в архиве релиза — колесо imageio-ffmpeg из PyPI без изменений (`packages/wheels/common`); исходники FFmpeg — https://ffmpeg.org/download.html |
| CMUdict | сервер, произношение английских слов | BSD-2-Clause | в архиве релиза — `packages/cmudict.dict` |
| pdf.js (Mozilla) | веб-интерфейс, чтение PDF | Apache-2.0 | файл `web/vendor/pdfjs/LICENSE` |
| .NET MAUI, .NET | приложение | MIT | — |
| sqlite-net | приложение | MIT | — |
| SQLitePCLRaw | приложение | Apache-2.0 | — |
| SQLite | приложение | Public Domain | — |
| Concentus (декодер Opus) | приложение | BSD (лицензия Opus) | — |
| Python | установщик сервера | PSF License | в архиве релиза — встраиваемый дистрибутив с python.org и `get-pip.py` (pip — MIT) |
| Microsoft Visual C++ Redistributable | установщик сервера (нужен PyTorch) | лицензия Microsoft (распространяемые компоненты Visual Studio) | в архиве релиза — `packages/vc_redist.x64.exe` без изменений |
| Зависимости PyTorch и Silero: sympy, mpmath, networkx, Jinja2, MarkupSafe, fsspec, filelock, typing_extensions, setuptools, antlr4-python3-runtime, PyYAML | сервер | BSD / MIT / PSF / Unlicense (у каждого своя) | в архиве релиза — колёса из PyPI без изменений (`packages/wheels`); тексты лицензий — внутри каждого колеса |

**Пакеты для установки без интернета** (папка `packages` в архивах релиза) — это сторонние файлы в неизменном виде,
скачанные с python.org, pypi.org, download.pytorch.org, microsoft.com и GitHub (Silero, CMUdict). На них действуют их
собственные лицензии из таблицы выше, а не MIT-0.

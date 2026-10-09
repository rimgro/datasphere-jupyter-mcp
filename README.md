# DataSphere MCP for Pi

Локальный stdio MCP-сервер для Jupyter/DataSphere с совместимостью notebook CRUD и kernel WebSocket и инструментами передачи файлов. Пакет проверен с `jupyter-mcp-server==2.2.3`.

## Состав

- `start_mcp.py`, `runtime.py` — запуск и bootstrap адаптеров;
- `rest_notebook_compat.py`, `kernel_compat.py` — DataSphere compatibility;
- `file_transfer_tools.py` — upload/download/link;
- `verify_mcp.py`, `verify_file_transfer.py` — удалённые smoke tests;
- `test_*.py` — локальные тесты.

## Установка (Linux)

Нужны Pi и [uv](https://docs.astral.sh/uv/). Распакуйте пакет в `~/.pi/agent/datasphere` (или выберите постоянный абсолютный путь). Установите проверенную зависимость:

```bash
uv tool install 'jupyter-mcp-server==2.2.3'
```

Убедитесь, что CLI доступен в `PATH` Pi. Если `uv tool` бинарники не в `PATH`, добавьте каталог с `jupyter-mcp-server` в `PATH` для процесса Pi. Для токена создайте отдельный файл вне репозитория:

```bash
mkdir -p ~/.pi/agent/datasphere
chmod 700 ~/.pi/agent/datasphere
umask 077
read -rsp 'Jupyter token: ' TOKEN; echo
printf '%s' "$TOKEN" > ~/.pi/agent/datasphere/token
unset TOKEN
chmod 600 ~/.pi/agent/datasphere/token
```

Получите у владельца DataSphere персональный URL среды и токен. Не используйте токен коллеги и не отправляйте секрет вместе с исходным архивом.

В `~/.pi/agent/mcp.json` добавьте сервер (замените `<...>` на реальные пути/URL):

```json
{
  "mcpServers": {
    "datasphere": {
      "command": "/home/USER/.local/share/uv/tools/jupyter-mcp-server/bin/python",
      "args": ["/home/USER/.pi/agent/datasphere/start_mcp.py"],
      "env": {
        "JUPYTER_URL": "https://<your-datasphere-jupyter-host>",
        "DATASPHERE_TOKEN_FILE": "/home/USER/.pi/agent/datasphere/token",
        "PATH": "/home/USER/.local/bin:/usr/local/bin:/usr/bin"
      }
    }
  }
}
```

Сохраните `mcp.json` с правами `600`; в нём нет токена, но URL — внутренняя конфигурация. Путь в args и URL должны быть локальными/командными. Перезапустите Pi или выполните `/reload`, затем проверьте `/mcp` или `pi mcp list`.

## Проверка

Офлайн-тесты, не запускают удалённый compute:

```bash
python3 ~/.pi/agent/datasphere/test_kernel_compat.py
python3 ~/.pi/agent/datasphere/test_file_transfer_tools.py
```

Удалённые smoke tests создают собственные временные ресурсы и могут стоить денег; запускайте осознанно:

```bash
python3 ~/.pi/agent/datasphere/verify_file_transfer.py
python3 ~/.pi/agent/datasphere/verify_mcp.py
```

Полный тест исполнения должен вывести `MCP END-TO-END VERIFIED`. MCP connected не гарантирует, что удалённый kernel доступен. Не повторяйте таймауты автоматически и не тестируйте на рабочем kernel.

## Работа и безопасность

Используйте `project/` для новых файлов. Не запускайте все ячейки незнакомого ноутбука, не перезапускайте чужие kernels и не загружайте чувствительные файлы. Вычисления DataSphere могут тарифицироваться. При утечке немедленно отзовите токен и выдайте новый.

MCP работает через stdio, публичный порт не открывает. Передача файлов идёт по Jupyter HTTP API: upload ограничен `project/` и 32 MiB; локальный download по умолчанию до 2 GiB. Ссылки `get_file_download_link` не публичные и требуют авторизованного браузера.

## Обновление

Зафиксируйте версию зависимости и исходного пакета вместе. Перед выпуском проверьте исходники на секреты и персональные ссылки; индивидуальные token/config-файлы и результаты тестов не включайте в архив.

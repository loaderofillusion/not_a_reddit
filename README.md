# not_a_reddit

Соцсеть похожая на Reddit для конспектов: люди публикуют конспекты, ставят лайки и обсуждают.

## Что умеет

- Регистрация / вход / выход
- Создание постов с заголовком, текстом и категорией
- Лента (новые / популярные)
- Страница поста с лайками и комментариями
- Профиль пользователя с био и списком его постов
- Счётчик просмотров

## Локальный запуск

```bash
pip install -r requirements.txt
python main.py
```

Откроется на `http://127.0.0.1:8080`.

При первом запуске база `db.db` создаётся автоматически.

## Деплой на PythonAnywhere

1. Зарегистрируйся на https://www.pythonanywhere.com (бесплатный аккаунт Beginner подойдёт).

2. Заходи в **Files** и загружай туда `main.py`, `models.py`, `requirements.txt` и папку `templates/`. Можно сразу залить zip-ом и распаковать в Bash-консоли через `unzip`.

   Структура должна быть такая:
   ```
   /home/ТВОЙ_НИК/not_a_reddit/
       main.py
       models.py
       requirements.txt
       templates/
           base.html
           index.html
           post.html
           login.html
           register.html
           profile.html
           edit_profile.html
           new_post.html
   ```

3. Открой **Consoles → Bash** и поставь зависимости:
   ```bash
   cd ~/not_a_reddit
   pip install --user -r requirements.txt
   ```

4. Иди во вкладку **Web → Add a new web app**:
   - Выбери **Manual configuration** (НЕ Flask из мастера, это даст больше контроля)
   - Версия Python — 3.10 или новее

5. На странице веб-приложения найди раздел **Code** и пропиши:
   - **Source code:** `/home/ТВОЙ_НИК/not_a_reddit`
   - **Working directory:** `/home/ТВОЙ_НИК/not_a_reddit`

6. Открой **WSGI configuration file** (ссылка прямо там же) и замени всё содержимое на:
   ```python
   import sys
   path = '/home/ТВОЙ_НИК/not_a_reddit'
   if path not in sys.path:
       sys.path.insert(0, path)

   from main import app as application
   ```

7. **Важно:** в `main.py` поменяй секретный ключ. На PythonAnywhere можно задать через переменную окружения, но для простоты можно прямо в коде заменить `"very-secret-key"` на что-то своё длинное и случайное.

8. Жми зелёную кнопку **Reload** на вкладке Web. Сайт будет доступен по адресу `https://ТВОЙ_НИК.pythonanywhere.com`.

### Если что-то сломалось
- Смотри **Error log** на вкладке Web — там видно реальную ошибку
- Проверь, что папка `templates` лежит рядом с `main.py`
- База создаётся автоматически при первом обращении, но если не создалась — в Bash-консоли:
  ```bash
  cd ~/not_a_reddit
  python3 -c "from main import app, db; app.app_context().push(); db.create_all()"
  ```

## Стек

- Flask + Flask-Login + Flask-SQLAlchemy
- SQLite (для PythonAnywhere free плана норм, для прода лучше Postgres)
- Bootstrap 5 + Bootstrap Icons

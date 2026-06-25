import os
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

USERNAME = os.getenv("REDDIT_USERNAME")
PASSWORD = os.getenv("REDDIT_PASSWORD")

if not USERNAME or not PASSWORD:
    raise SystemExit("Добавь REDDIT_USERNAME и REDDIT_PASSWORD в файл .env")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)        # окно видимое
    context = browser.new_context(locale="en-US")
    page = context.new_page()
    page.goto("https://www.reddit.com/login/", timeout=120000)
    page.wait_for_timeout(3000)

    # Поля входа Reddit лежат в Shadow DOM — ищем по name, Playwright это умеет.
    try:
        page.fill('input[name="username"]', USERNAME, timeout=20000)
        page.fill('input[name="password"]', PASSWORD, timeout=20000)
        print(">>> Логин и пароль введены автоматически.")
    except Exception as e:
        print(f">>> Не удалось заполнить поля автоматически: {e}")
        print(">>> Введи данные в окне вручную.")

    # Пробуем нажать кнопку входа
    try:
        page.get_by_role("button", name="Log In").click(timeout=10000)
    except Exception:
        try:
            page.click('button[type="submit"]', timeout=10000)
        except Exception:
            print(">>> Кнопку входа не нашли — нажми её вручную в окне.")

    print("\n>>> Если Reddit просит код с почты, капчу или 2FA —")
    print(">>> заверши вход вручную в окне браузера.")
    print(">>> Когда увидишь свою ленту (ты залогинен) — нажми Enter здесь.\n")
    input("Нажми Enter после успешного входа... ")

    context.storage_state(path="reddit_state.json")
    print("Готово! Сессия сохранена в reddit_state.json")
    browser.close()

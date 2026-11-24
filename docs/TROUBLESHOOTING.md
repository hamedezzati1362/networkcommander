# عیب‌یابی

| نشانه | علت/راه‌حل |
|---|---|
| «Session missing» / 401 | برنامه را از میان‌بر Start باز کنید (لینک `/auth/bootstrap`). |
| `host_key_untrusted` | صفحهٔ دستگاه ← Fetch ← مقایسه ← trust. |
| `host_key_changed` | کلید عوض شده؛ اگر دستگاه تعویض شده دوباره trust کنید، وگرنه مشکوک است. |
| `auth_failed` | نام کاربری/رمز یا privilege (Cisco: enable secret). |
| `not_cisco` / `not_mikrotik` / `not_fortinet` | فروشندهٔ انتخاب‌شده با خروجی دستگاه نمی‌خواند. |
| ماژول «unsupported» | دلیل در ماتریس قابلیت است؛ مثلاً روتر VLAN database ندارد. |
| `unknown_state` | اتصال حین اعمال قطع شد؛ پشتیبان پیش‌ازتغییر را با وضعیت فعلی مقایسه کنید. |
| `device_busy` | تغییر دیگری روی همان دستگاه در حال اجراست. |
| لاگ | `%LOCALAPPDATA%\NetworkCommander\logs\networkcommander.log` |
| پورت 8765 اشغال | `NC_PORT` را تغییر دهید. |

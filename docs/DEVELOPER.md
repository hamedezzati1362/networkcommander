# توسعه‌دهنده و افزونه

ساختار: `backend/nc/{api,core,engine,security,services,transport,vendors}`, `backend/tests`, `frontend/src`.

## افزودن ماژول پیکربندی
1. مدل در `engine/specs.py` (با `module: Literal[...]`، اعتبارسنج‌ها، به `Spec` و `MODULE_MODELS/TITLES` اضافه شود).
2. نگاشت قابلیت در `engine/executor.py::MODULE_CAPS`.
3. متد `_t_<module>` در هر آداپتور (یا `Unsupported` با دلیل). فرانت‌اند خودکار فرم می‌سازد.
4. ریسک در `engine/risk.py`؛ تست در `tests/`.

## افزودن فروشنده (افزونه)
زیرکلاس `VendorAdapter` بنویسید (`nc/vendors/base.py`) و `nc.vendors.registry.register(MyAdapter())` را هنگام بارگذاری فراخوانی کنید. باید پیاده کند: `identify_commands`، `parse_identity` (در خروجی فروشندهٔ دیگر `NCError` بدهد)، `probes`، `backup_commands`، `translate`، `diag_command`. ‌`Plan.checks` را حتماً بنویسید؛ بدون verify ماژولی «کار می‌کند» حساب نمی‌شود. مکانیزم کشف خودکار افزونه (entry points) هنوز نیست.

## شبیه‌ساز
`nc/simulator.py` فقط منطق موتور را می‌آزماید و رفتار فریمور واقعی را اثبات نمی‌کند. با فعال بودن `NC_ENABLE_SIMULATOR=1`، دستگاه با transport=simulator اضافه کنید.

## تست
`cd backend && python -m pytest -q`؛ `cd frontend && npm test`. مهاجرت: `alembic -c alembic.ini revision --autogenerate`.
